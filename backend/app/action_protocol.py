# -*- coding: utf-8 -*-
"""
================================================================================
动作协议卡（2026-11-28）
================================================================================
【为什么做】用户复盘 11-27（原话）：
  "政策中午发文，午盘开盘猛拉，我当时不知所措，犹豫不决，错过上车好时机。"

  真实时间线（线上库实测）：
    12:23:26  国常会通稿（hot=沸，**系统采到了**）
    12:32     企微推送观察词命中「平准基金」  ← **信息已经到人手上**
    13:00     A 股开盘
    13:33     中证1000 期货涨停
    13:36     创业板指 +10%

  ⇒ 到开盘还有 28 分钟，人不是"不知道"，是**没有卡可翻**。

【与 `thesis_scenarios`（情景卡）的分工 —— 别搞混】
  · 情景卡回答"**会怎样**"：scenarios(概率) + checks(事后验证) + invalidators
  · 本模块回答"**怎么办**"：触发器 → 首动作 → 裁决条款 → 仓位路径 → 失效条件
  用户原话："10% 分支只有验证项，没有动作项" —— 缺的就是这张表。

【为什么规则冲突要写进卡里】
  用户周五撞上的其实是两条规则互斥：
    · 拦刀规则："永不追当日长阳"
    · 事件直觉："这是政策锚长阳，不是普通长阳"
  框架里没有裁决条款 ⇒ 卡死在中间 = "不知所措"的体感。
  ⇒ `verdict` 字段不是装饰，它存在的唯一目的就是**打破平局**。

【红线】纯规则层：不自动下单、不改评分、不进闸门、不碰模拟盘。
  命中后只做一件事：**把卡推到人面前**（企微 + 接口），决策仍由人做。
================================================================================
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_T_PROTO = "action_protocol"
_T_HIT = "action_protocol_hit"      # 命中账本（每协议每日至多推 1 次）
_BJ = timezone(timedelta(hours=8))
_READY = False

# 交易时段窗（含午间休市）：政策通稿恰恰常在午休时段发，必须覆盖
_TRADING_WINDOW = "09:15-15:05"


def _now() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_PROTO} (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            trigger_json TEXT,
            calendar_json TEXT,
            branches_json TEXT,
            first_move TEXT,
            verdict TEXT,
            position_path TEXT,
            invalidate TEXT,
            status TEXT DEFAULT 'active',
            source TEXT DEFAULT 'manual',
            created_at TEXT
        )
    """)
    # ★★ 2026-11-28（用户："按财经日历做提前预案，先推演，如果 xxx 应该 xxx"）：
    #   旧表（只有 keywords 触发）补两列 —— 日历事件触发 + 分支预案。
    #   PG 支持 IF NOT EXISTS；SQLite 不支持 ⇒ 用 try 吞掉"列已存在"。
    for _col in ("calendar_json", "branches_json"):
        try:
            db.execute(f"ALTER TABLE {_T_PROTO} ADD COLUMN {_col} TEXT")
        except Exception:  # noqa: except_pass  列已存在（SQLite/PG 都会抛），属正常
            pass
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_HIT} (
            protocol_id TEXT NOT NULL,
            date TEXT NOT NULL,
            first_time TEXT,
            hits INTEGER DEFAULT 0,
            sample TEXT,
            pushed INTEGER DEFAULT 0,
            created_at TEXT,
            PRIMARY KEY (protocol_id, date)
        )
    """)
    _READY = True


# ══════════════════════════════════════════════════════════════════════════
#  纯函数：文本 → 是否命中（离线可测，不连库）
# ══════════════════════════════════════════════════════════════════════════
def _kw_hit(kw: str, text: str) -> bool:
    """关键词命中：含 '.*' 或 '(' 视为正则（与 flash.rules._kw_match 同口径）。"""
    if not kw:
        return False
    if ".*" in kw or "(" in kw:
        try:
            return re.search(kw, text) is not None
        except re.error:
            return False
    return kw in text


def match_text(text: str, protocols: List[Dict]) -> List[Dict]:
    """单条文本 × 协议列表 → 命中的协议（附 `_hit_keywords`，便于解释为什么触发）。

    `trigger.min_hits` 默认 1（命中任一关键词即算），可按协议调高以降噪。
    """
    if not text or not protocols:
        return []
    out = []
    for p in protocols:
        trig = p.get("trigger") or {}
        kws = [str(k) for k in (trig.get("keywords") or []) if str(k).strip()]
        hit = [k for k in kws if _kw_hit(k, text)]
        need = int(trig.get("min_hits") or 1)
        if len(hit) >= need:
            out.append({**p, "_hit_keywords": hit})
    return out


def match_items(items: List[Dict], protocols: List[Dict] = None) -> List[Dict]:
    """多条快讯 × 协议 → 命中的协议（合并命中关键词、取最早时间）。"""
    protocols = protocols if protocols is not None else list_protocols(active_only=True)
    if not protocols:
        return []
    agg: Dict[str, Dict] = {}
    for it in (items or []):
        text = str((it or {}).get("text") or (it or {}).get("content") or "")
        t = str((it or {}).get("time") or "")
        for p in match_text(text, protocols):
            key = p["id"]
            e = agg.get(key)
            if not e:
                e = agg[key] = {**p, "_hit_keywords": [], "first_time": t,
                                "last_time": t, "n_items": 0, "_sample": text[:160]}
            for k in p.get("_hit_keywords") or []:
                if k not in e["_hit_keywords"]:
                    e["_hit_keywords"].append(k)
            e["n_items"] += 1
            if t and (not e["first_time"] or t < e["first_time"]):
                e["first_time"] = t
                e["_sample"] = text[:160]
            if t and t > (e["last_time"] or ""):
                e["last_time"] = t
    return list(agg.values())


# ══════════════════════════════════════════════════════════════════════════
#  登记 / 读取
# ══════════════════════════════════════════════════════════════════════════
def _row_to_dict(r: Dict) -> Dict:
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "trigger": _loads(r.get("trigger_json")) or {},
        "calendar": _loads(r.get("calendar_json")) or {},
        "branches": _loads(r.get("branches_json")) or [],
        "first_move": r.get("first_move") or "",
        "verdict": r.get("verdict") or "",
        "position_path": r.get("position_path") or "",
        "invalidate": r.get("invalidate") or "",
        "status": r.get("status"),
        "source": r.get("source"),
        "created_at": r.get("created_at"),
    }


def _loads(s) -> Optional[object]:
    if not s:
        return None
    try:
        return json.loads(s) if isinstance(s, str) else s
    except (ValueError, TypeError):
        return None


def create(payload: Dict) -> Dict:
    """登记一张协议卡。校验不过就拒绝（宁可拒，也不写一张没法执行的卡）。"""
    ensure_tables()
    name = str((payload or {}).get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name 必填"}

    trig = (payload or {}).get("trigger") or {}
    if isinstance(trig, str):
        trig = {"keywords": [x.strip() for x in trig.split(",") if x.strip()]}
    kws = [str(k).strip() for k in (trig.get("keywords") or []) if str(k).strip()]

    # ★★ 2026-11-28（用户："按财经日历做提前预案，先推演，如果 xxx 应该 xxx，
    #   这样就不会一脸懵"）：日历事件触发（与快讯关键词并列，二者有其一即可）。
    cal = (payload or {}).get("calendar") or {}
    cal_norm = {}
    if isinstance(cal, dict) and str(cal.get("date") or "").strip():
        cal_norm = {
            "date": str(cal.get("date"))[:10],
            "event": str(cal.get("event") or "").strip(),
            "time": str(cal.get("time") or "").strip(),
            "note": str(cal.get("note") or "").strip(),
        }
    if not kws and not cal_norm:
        return {"ok": False,
                "error": "trigger.keywords 与 calendar 至少填一个（没有触发器就不是协议）"}

    # 分支预案：[{"if": "...", "then": "..."}] —— 这才是"如果 A 就做甲"的载体
    branches = []
    for b in ((payload or {}).get("branches") or []):
        if not isinstance(b, dict):
            continue
        _if = str(b.get("if") or "").strip()
        _then = str(b.get("then") or "").strip()
        if _if and _then:
            branches.append({"if": _if, "then": _then})

    first_move = str((payload or {}).get("first_move") or "").strip()
    if not first_move:
        return {"ok": False, "error": "first_move 必填（触发器命中了总得先做点什么，哪怕是不交易）"}

    pid = uuid.uuid4().hex[:12]
    try:
        db.insert(_T_PROTO, {
            "id": pid,
            "name": name,
            "trigger_json": json.dumps({
                "keywords": kws,
                "min_hits": int(trig.get("min_hits") or 1),
                "window": str(trig.get("window") or _TRADING_WINDOW),
                "note": str(trig.get("note") or ""),
            }, ensure_ascii=False),
            "calendar_json": (json.dumps(cal_norm, ensure_ascii=False)
                              if cal_norm else None),
            "branches_json": (json.dumps(branches, ensure_ascii=False)
                              if branches else None),
            "first_move": first_move,
            "verdict": str((payload or {}).get("verdict") or "").strip(),
            "position_path": str((payload or {}).get("position_path") or "").strip(),
            "invalidate": str((payload or {}).get("invalidate") or "").strip(),
            "status": "active",
            "source": str((payload or {}).get("source") or "manual"),
            "created_at": _now(),
        })
    except Exception as e:
        print(f"[protocol] create failed: {str(e)[:100]}")
        return {"ok": False, "error": f"落库失败: {str(e)[:60]}"}

    # 同名旧卡归档（同一份协议不该并存两份，否则命中时会推两张）
    try:
        db.execute(f"UPDATE {_T_PROTO} SET status='archived' "
                   f"WHERE status='active' AND name=%s AND id<>%s", (name, pid))
    except Exception as e:
        print(f"[protocol] archive same-name failed: {str(e)[:80]}")
    return {"ok": True, "id": pid}


def list_protocols(active_only: bool = False) -> List[Dict]:
    ensure_tables()
    try:
        if active_only:
            rows = db.fetch(f"SELECT * FROM {_T_PROTO} WHERE status='active' "
                            f"ORDER BY created_at DESC") or []
        else:
            rows = db.fetch(f"SELECT * FROM {_T_PROTO} "
                            f"ORDER BY created_at DESC LIMIT 50") or []
    except Exception as e:
        print(f"[protocol] list failed: {str(e)[:90]}")
        return []
    return [_row_to_dict(r) for r in rows]


def get(pid: str) -> Optional[Dict]:
    ensure_tables()
    try:
        r = db.fetch_one(f"SELECT * FROM {_T_PROTO} WHERE id=%s", (pid,))
    except Exception as e:
        print(f"[protocol] get failed: {str(e)[:80]}")
        return None
    return _row_to_dict(r) if r else None


def archive(pid: str) -> bool:
    ensure_tables()
    try:
        db.execute(f"UPDATE {_T_PROTO} SET status='archived' WHERE id=%s", (pid,))
        return True
    except Exception as e:
        print(f"[protocol] archive failed: {str(e)[:80]}")
        return False


# ══════════════════════════════════════════════════════════════════════════
#  盘中：快讯 → 命中 → 推送（每协议每日至多一次）
# ══════════════════════════════════════════════════════════════════════════
def match_calendar(today: str = None, days_ahead: int = 1,
                   protocols: List[Dict] = None) -> List[Dict]:
    """**日历事件匹配**：返回「今天 ~ N 天后」有 `calendar` 的协议卡。

    这是用户要的核心："按财经日历做提前预案，如果 xxx 应该 xxx"。
    与快讯匹配的区别：快讯是**事发后**才知道，日历是**事发前**就摆在那。
    `days_ahead=1` = 今天和明天（提前一天预告，当天再推一次会被幂等挡掉）。
    """
    protocols = protocols if protocols is not None else list_protocols(active_only=True)
    if not protocols:
        return []
    d0_str = str(today or datetime.now(_BJ).strftime("%Y-%m-%d"))[:10]
    try:
        d0 = datetime.strptime(d0_str, "%Y-%m-%d").date()
    except ValueError:
        d0 = datetime.now(_BJ).date()
    end = d0 + timedelta(days=max(0, int(days_ahead or 0)))

    out = []
    for p in protocols:
        cal = p.get("calendar") or {}
        cd = str(cal.get("date") or "")[:10]
        if not cd:
            continue
        try:
            d = datetime.strptime(cd, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d0 <= d <= end:
            out.append({**p, "_days_until": (d - d0).days})
    out.sort(key=lambda x: (x["_days_until"],
                            str((x.get("calendar") or {}).get("time") or "")))
    return out


def format_calendar_push(hits: List[Dict]) -> str:
    """日历预案推送文本。**明确写"不是预测，是提前想好"** —— 防止被当成点位承诺。"""
    lines = ["【事件预案】不是预测，是提前想好「如果…就…」"]
    for h in hits:
        cal = h.get("calendar") or {}
        n = h.get("_days_until")
        when = "今天" if n == 0 else (f"{n} 天后" if n else "")
        lines.append("")
        lines.append("■ %s（%s %s %s）"
                     % (h.get("name"), when, cal.get("date") or "", cal.get("time") or ""))
        if cal.get("event"):
            lines.append("  事件：%s" % cal["event"])
        if cal.get("note"):
            lines.append("  说明：%s" % cal["note"])
        for b in (h.get("branches") or []):
            lines.append("  · 如果 %s → %s" % (b.get("if"), b.get("then")))
        for label, key in (("首动作", "first_move"), ("裁决", "verdict"),
                           ("仓位", "position_path"), ("失效", "invalidate")):
            v = str(h.get(key) or "").strip()
            if v:
                lines.append("  %s：%s" % (label, v))
    return "\n".join(lines)


def check_calendar_push(today: str = None, days_ahead: int = 1,
                        push: bool = True) -> Dict:
    """到点检查日历协议并推送（幂等：每卡每日一次）。

    调用方：`flash/service.poll_flash_once` 每轮调用（5 分钟一次，幂等挡重复）。
    """
    try:
        hits = match_calendar(today=today, days_ahead=days_ahead)
        if not hits:
            return {"ok": True, "pushed": 0, "note": "近期无日历协议"}
        d = str(today or datetime.now(_BJ).strftime("%Y-%m-%d"))[:10]
        todo = [h for h in hits if not _already_pushed(h["id"], d)]
        if not todo:
            return {"ok": True, "pushed": 0, "note": "今日已推过", "hits": len(hits)}
        text = format_calendar_push(todo)
        if push:
            _push(text)
        for h in todo:
            _record_hit(h["id"], d, "", 1, "", True)
        return {"ok": True, "pushed": len(todo), "names": [h["name"] for h in todo]}
    except Exception as e:
        print(f"[protocol] calendar push failed: {str(e)[:100]}")
        return {"ok": False, "pushed": 0, "error": str(e)[:80]}


def _already_pushed(pid: str, day: str) -> bool:
    try:
        r = db.fetch_one(f"SELECT pushed FROM {_T_HIT} "
                         f"WHERE protocol_id=%s AND date=%s", (pid, day))
        return bool((r or {}).get("pushed"))
    except Exception:
        return False


def _record_hit(pid: str, day: str, first_time: str, n: int, sample: str,
                pushed: bool) -> None:
    try:
        old = db.fetch_one(f"SELECT hits, pushed FROM {_T_HIT} "
                           f"WHERE protocol_id=%s AND date=%s", (pid, day)) or {}
        db.upsert(_T_HIT, {
            "protocol_id": pid, "date": day,
            "first_time": first_time,
            "hits": int(old.get("hits") or 0) + n,
            "sample": (sample or "")[:200],
            # ★ 2026-11-28 审查修（真 bug）：`pushed` **只升不降**。
            #   `check_push` 会对"非待推"的命中传 pushed=False，
            #   直接覆盖会把已推过的记录打回 0 ⇒ 同一张卡下一轮又推一遍。
            "pushed": 1 if (pushed or old.get("pushed")) else 0,
            "created_at": _now(),
        }, conflict_columns=["protocol_id", "date"])
    except Exception as e:
        print(f"[protocol] record hit failed: {str(e)[:80]}")


def check_push(items: List[Dict], day: str = None, push: bool = True) -> Dict:
    """快讯批次 → 命中协议 → 推送（幂等：每协议每日至多 1 条）。

    调用方：`flash/service.poll_flash_once` 尾部（与观察词提醒并列）。
    失败必须静默 —— 协议提醒是辅助层，绝不能拖垮快讯主链。
    """
    try:
        protocols = list_protocols(active_only=True)
        if not protocols:
            return {"ok": True, "pushed": 0, "note": "无启用协议"}
        hits = match_items(items or [], protocols)
        if not hits:
            return {"ok": True, "pushed": 0, "note": "无命中"}

        d = str(day or datetime.now(_BJ).strftime("%Y-%m-%d"))[:10]
        todo = [h for h in hits if not _already_pushed(h["id"], d)]
        for h in hits:
            _record_hit(h["id"], d, h.get("first_time") or "", int(h.get("n_items") or 1),
                        h.get("_sample") or "", bool(todo and h["id"] in {x["id"] for x in todo}))
        if not todo:
            return {"ok": True, "pushed": 0, "note": "今日已推过", "hits": len(hits)}

        text = format_push(todo)
        if push:
            _push(text)
        return {"ok": True, "pushed": len(todo), "names": [h["name"] for h in todo]}
    except Exception as e:
        print(f"[protocol] check_push failed: {str(e)[:100]}")
        return {"ok": False, "pushed": 0, "error": str(e)[:80]}


def format_push(hits: List[Dict]) -> str:
    """命中 → 企微文本。**只呈现卡片内容，不追加任何建议**。"""
    lines = ["【动作协议命中】翻卡执行，不是决策"]
    for h in hits:
        lines.append("")
        lines.append(f"■ {h.get('name')}（{h.get('first_time') or ''}）")
        if h.get("_hit_keywords"):
            lines.append(f"  触发：{'、'.join(h['_hit_keywords'])}")
        if h.get("_sample"):
            lines.append(f"  原文：{h['_sample']}")
        for label, key in (("首动作", "first_move"), ("裁决", "verdict"),
                           ("仓位路径", "position_path"), ("失效", "invalidate")):
            v = str(h.get(key) or "").strip()
            if v:
                lines.append(f"  {label}：{v}")
    return "\n".join(lines)


def _push(text: str) -> None:
    """走既有企微通道（`push_markdown_batched`，alert 分类群）；失败静默。"""
    try:
        from app.flash import wechat
        wechat.push_markdown_batched("动作协议命中", text, category="alert")
    except Exception as e:
        print(f"[protocol] push failed: {str(e)[:80]}")
