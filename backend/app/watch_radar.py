# -*- coding: utf-8 -*-
"""
================================================================================
个股分时盯盘（2026-11-28）
================================================================================
【用户需求原文】
  "需要新增个股分时盯盘功能，支持同时实时监控不超过 5 只个股，确保低资源占用与
   稳定运行。核心要求：自动标注每只个股当天的支撑位和阻力位，用于实时交易决策，
   并兼顾做T（日内高抛低吸）功能。需能识别并提示关键盘口信号，例如某价位出现
   大额压单（如几十万手卖单）、突破阻力位或跌破支撑位时及时触发通知，避免出现
   突破关键价位后未收到提醒的情况。"

【例证】京东方Ａ（000725）：11-26 收 4.50、该价位有大量卖单压着；11-27 突破 4.50
  后一路走高（实测收 4.80，最高 4.85，昨收 4.50，卖五档比买五档多 14.8 万手）。
  ⇒ 若「阻力=昨收」的判据在，11-27 早盘就能收到突破 4.50 的推送。

【功能范围（明确边界）】
  ✅ 盘中实时盯盘：突破阻力 / 跌破支撑 / 大额压单（卖） / 大额托单（买）
  ✅ 自动标注当日支撑/阻力（规则见下）
  ✅ 企微推送（每只票每类信号每日至多 1 次）
  ❌ **不给买卖指令**：只陈述"发生了什么"，不说"该买该卖"
  ❌ 不改评分、不进闸门、不碰模拟盘
  ❌ 不做回测验证（阈值是经验值，未校准 —— 与 tier_alert 同纪律，必须写在推送里）

【支撑/阻力标注规则（v1，明确可复现）】
  阻力 R = **昨收价**
    · 依据：昨日收盘是当日最强的心理位；昨日在该价位堆积的卖单会延续到今日
      （京东方Ａ 11-26 收 4.50 且卖压重 → 11-27 的 R 就是 4.50）
  支撑 S = min(昨收, 近 5 日最低)
    · 依据：跌破昨收后，下方要看的是近 5 日低点，而不是凭空画线
  盘口阻力/支撑（实时补充，不落库）
    · 卖五档中**挂单量最大**的档位价 = 盘口阻力
    · 买五档中**挂单量最大**的档位价 = 盘口支撑

【提醒触发条件（明确）】
  · breakout   现价 ≥ R × (1 + 0.3%)            —— 突破阻力
  · breakdown  现价 ≤ S × (1 − 0.3%)            —— 跌破支撑
  · wall_ask   某卖档挂单 ≥ max(20万手, 每分钟均量×3) 且**连续 2 次快照都在**
  · wall_bid   某买档挂单 ≥ 同上阈值            且**连续 2 次快照都在**
  ⚠️ 压单必须连续 2 次快照 —— 盘口挂单随时可撤，单次快照的"几十万手"经常是
     幌骗（spoofing），不设这一条会天天误报。

【低资源约束】
  · 硬上限 5 只（`WATCH_MAX_CODES`，超出拒绝登记）
  · 单次扫描 = **1 个 HTTP 请求**（多 code 拼一个 `q=`），60 秒一次
  · 关键位每天只算一次并落库；盘中只读表 + 内存比对
================================================================================
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_T_TARGET = "watch_target"
_T_LEVEL = "watch_level"
_T_LOG = "watch_signal_log"
_BJ = timezone(timedelta(hours=8))
_READY = False

MAX_CODES = max(1, min(5, int(os.environ.get("WATCH_MAX_CODES") or 5)))
PUSH_ENABLED = (os.environ.get("WATCH_RADAR_PUSH") or "1").strip() != "0"
CHECK_INTERVAL = max(30, int(os.environ.get("WATCH_RADAR_INTERVAL_SEC") or 60))

BREAK_PCT = 0.003          # 突破/跌破幅度 0.3%
WALL_ABS = 200000          # 压单绝对阈值：20 万手
WALL_RATIO = 3             # 相对阈值：每分钟均量 × 3

# 压单连续性内存（防幌骗）：{code: {"ask": (价,量), "bid": (价,量)}}
_last_wall: Dict[str, Dict] = {}


def _now() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_TARGET} (
            code TEXT PRIMARY KEY,
            name TEXT,
            added_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_LEVEL} (
            date TEXT, code TEXT,
            support REAL, resistance REAL,
            s_src TEXT, r_src TEXT,
            detail_json TEXT, created_at TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_LOG} (
            date TEXT, code TEXT, kind TEXT,
            price REAL, detail TEXT, pushed_at TEXT,
            PRIMARY KEY (date, code, kind)
        )
    """)
    _READY = True


# ────────────────────────── 盯盘清单（≤5） ──────────────────────────
def list_targets() -> List[Dict]:
    ensure_tables()
    try:
        rows = db.fetch(f"SELECT * FROM {_T_TARGET} ORDER BY added_at") or []
    except Exception as e:
        print(f"[watch-radar] list targets failed: {str(e)[:80]}")
        return []
    return [dict(r) for r in rows]


def add_target(code: str, name: str = "") -> Dict:
    """加入盯盘清单。**硬上限 5 只**，超出拒绝（低资源是前提，不是建议）。"""
    ensure_tables()
    code = str(code or "").strip()[:6]
    if not code:
        return {"ok": False, "error": "code 必填"}
    cur = list_targets()
    if any(t.get("code") == code for t in cur):
        return {"ok": True, "code": code, "note": "已在清单中"}
    if len(cur) >= MAX_CODES:
        return {"ok": False,
                "error": f"已达上限 {MAX_CODES} 只（当前 {len(cur)} 只），请先移除再添加"}
    try:
        db.insert(_T_TARGET, {"code": code, "name": name or "", "added_at": _now()})
    except Exception as e:
        return {"ok": False, "error": f"落库失败: {str(e)[:60]}"}
    return {"ok": True, "code": code, "n": len(cur) + 1}


def remove_target(code: str) -> bool:
    ensure_tables()
    try:
        db.execute(f"DELETE FROM {_T_TARGET} WHERE code=%s", (str(code)[:6],))
        return True
    except Exception as e:
        print(f"[watch-radar] remove target failed: {str(e)[:80]}")
        return False


# ────────────────────────── 关键位 ──────────────────────────
def _low_5d(code: str, day: str) -> Optional[float]:
    """近 5 个交易日最低价（日线）。取不到返回 None（降级，不猜）。"""
    try:
        from app import tencent
        ks = tencent.get_kline(code, period="day", count=12) or []
        lows = []
        for k in ks:
            d = str(k.get("date") or k.get("day") or "")[:10]
            if d and d < day:                      # 只要 day 之前的
                v = k.get("low")
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    continue
                lows.append((d, f))
        lows.sort(key=lambda x: x[0])
        tail = [f for _, f in lows[-5:]]
        return min(tail) if tail else None
    except Exception as e:
        print(f"[watch-radar] kline low failed {code}: {str(e)[:70]}")
        return None


def compute_levels(code: str, day: str, prev_close: float) -> Dict:
    """算当日支撑/阻力并落库（每天一次，盘中只读）。"""
    if not prev_close or prev_close <= 0:
        return {}
    low5 = _low_5d(code, day)
    support = min(prev_close, low5) if low5 else prev_close
    resistance = prev_close

    row = {
        "date": day, "code": code,
        "support": round(support, 3), "resistance": round(resistance, 3),
        "s_src": ("近5日低点" if (low5 and low5 < prev_close) else "昨收"),
        "r_src": "昨收",
        "detail_json": json.dumps({
            "prev_close": round(prev_close, 3),
            "low_5d": round(low5, 3) if low5 else None,
        }, ensure_ascii=False),
        "created_at": _now(),
    }
    try:
        db.upsert(_T_LEVEL, row, conflict_columns=["date", "code"])
    except Exception as e:
        print(f"[watch-radar] save level failed {code}: {str(e)[:70]}")
    return row


def get_levels(code: str, day: str) -> Dict:
    ensure_tables()
    try:
        r = db.fetch_one(f"SELECT * FROM {_T_LEVEL} WHERE date=%s AND code=%s",
                         (day, code))
    except Exception:
        r = None
    return dict(r) if r else {}


# ────────────────────────── 信号判定 ──────────────────────────
def _wall_threshold(volume, elapsed_min: int) -> float:
    """压单阈值（手）：绝对 20 万手 与 每分钟均量×3 取大。"""
    per_min = (float(volume or 0) / max(elapsed_min, 1))
    return max(WALL_ABS, per_min * WALL_RATIO)


def _elapsed_trading_min(now: datetime = None) -> int:
    """已过的交易分钟数（9:15-11:30 + 13:00-15:00，粗略即可，用于均量估算）。"""
    n = now or datetime.now(_BJ)
    t = n.hour * 60 + n.minute
    am = max(0, min(t, 690) - 570)          # 9:30-11:30
    pm = max(0, min(t, 900) - 780)          # 13:00-15:00
    return max(am + pm, 1)


def scan_once(day: str = None, push: bool = True) -> Dict:
    """扫一次：1 个 HTTP 请求（≤5 只）→ 判信号 → 推送（每票每类每日一次）。"""
    targets = list_targets()
    if not targets:
        return {"ok": True, "n": 0, "signals": [], "note": "盯盘清单为空"}
    codes = [str(t["code"]) for t in targets][:MAX_CODES]
    d = str(day or datetime.now(_BJ).strftime("%Y-%m-%d"))[:10]

    from app import tencent
    depth = tencent.get_depth_batch(codes)
    if not depth:
        return {"ok": False, "error": "行情获取失败", "signals": []}

    elapsed = _elapsed_trading_min()
    signals: List[Dict] = []
    snapshots = {}

    for code in codes:
        q = depth.get(code) or {}
        price = q.get("price") or 0
        prev = q.get("prev_close") or 0
        if price <= 0:
            continue

        lv = get_levels(code, d)
        if not lv:
            lv = compute_levels(code, d, prev)
        R = lv.get("resistance") or 0
        S = lv.get("support") or 0

        base = {"code": code, "name": q.get("name") or "",
                "price": price, "prev_close": prev,
                "resistance": R, "support": S}

        # ① 突破阻力
        if R and price >= R * (1 + BREAK_PCT):
            signals.append({**base, "kind": "breakout",
                            "detail": "现价 %.2f 突破阻力 %.2f（+%.2f%%）"
                                      % (price, R, (price / R - 1) * 100)})
        # ② 跌破支撑
        if S and price <= S * (1 - BREAK_PCT):
            signals.append({**base, "kind": "breakdown",
                            "detail": "现价 %.2f 跌破支撑 %.2f（%.2f%%）"
                                      % (price, S, (price / S - 1) * 100)})

        # ③ 大额压单 / 托单（连续 2 次快照才报）
        thr = _wall_threshold(q.get("volume"), elapsed)
        asks = q.get("asks") or []
        bids = q.get("bids") or []
        top_ask = max(asks, key=lambda x: x[1]) if asks else None
        top_bid = max(bids, key=lambda x: x[1]) if bids else None
        last = _last_wall.get(code) or {}
        cur_snap = {}

        if top_ask and top_ask[1] >= thr:
            cur_snap["ask"] = (top_ask[0], top_ask[1])
            if last.get("ask") and abs(last["ask"][0] - top_ask[0]) < 1e-6:
                signals.append({**base, "kind": "wall_ask",
                                "detail": "卖 %.2f 挂单 %.1f 万手（连续 2 次，阈值 %.1f 万手）"
                                          % (top_ask[0], top_ask[1] / 1e4, thr / 1e4)})
        if top_bid and top_bid[1] >= thr:
            cur_snap["bid"] = (top_bid[0], top_bid[1])
            if last.get("bid") and abs(last["bid"][0] - top_bid[0]) < 1e-6:
                signals.append({**base, "kind": "wall_bid",
                                "detail": "买 %.2f 挂单 %.1f 万手（连续 2 次，阈值 %.1f 万手）"
                                          % (top_bid[0], top_bid[1] / 1e4, thr / 1e4)})
        _last_wall[code] = cur_snap

        snapshots[code] = {**base, "kind": "snapshot",
                           "detail": "阻力 %.2f / 支撑 %.2f | 委差 %.0f 手"
                                     % (R, S, q.get("bid_diff") or 0),
                           "asks": asks, "bids": bids,
                           "wall_thr": round(thr, 0)}

    # 去重（每票每类每日一次）
    ensure_tables()
    fresh = []
    for s in signals:
        try:
            exists = db.fetch_one(
                f"SELECT 1 AS x FROM {_T_LOG} WHERE date=%s AND code=%s AND kind=%s",
                (d, s["code"], s["kind"]))
        except Exception:
            exists = None
        if exists:
            continue
        fresh.append(s)

    # ★★ 2026-11-28 审查修（真 bug）：`push=False` 是**页面查看**，绝不能写日志 ——
    #   否则用户打开页面看一眼，信号就被写进 watch_signal_log，
    #   后端的 watch_radar_loop 会判定"今日已推"而**不再推企微**，
    #   正好违背用户"避免突破关键价位后没收到提醒"的初衷。
    if not push:
        return {"ok": True, "date": d, "n": len(codes),
                "n_signal": len(signals), "n_fresh": len(fresh), "pushed": 0,
                "signals": signals, "snapshots": list(snapshots.values()),
                "note": "push=False（仅查看），不写日志、不影响后续推送"}

    pushed = 0
    if fresh and push and PUSH_ENABLED:
        text = format_push(fresh)
        try:
            from app.flash import wechat
            wechat.push_markdown_batched("个股盯盘提醒", text, category="alert")
            pushed = len(fresh)
        except Exception as e:
            print(f"[watch-radar] push failed: {str(e)[:80]}")
    for s in fresh:
        try:
            db.upsert(_T_LOG, {
                "date": d, "code": s["code"], "kind": s["kind"],
                "price": s.get("price"), "detail": str(s.get("detail") or "")[:300],
                "pushed_at": _now(),
            }, conflict_columns=["date", "code", "kind"])
        except Exception as e:
            print(f"[watch-radar] log failed: {str(e)[:70]}")

    return {"ok": True, "date": d, "n": len(codes),
            "n_signal": len(signals), "n_fresh": len(fresh), "pushed": pushed,
            "signals": signals, "snapshots": list(snapshots.values())}


def format_push(signals: List[Dict]) -> str:
    """企微文本：只陈述事实，明确标注"观察器、非买卖建议、阈值未校准"。"""
    ICON = {"breakout": "▲ 突破", "breakdown": "▼ 跌破",
            "wall_ask": "🧱 大额压单", "wall_bid": "🛡 大额托单"}
    lines = ["**个股盯盘信号**", ""]
    for s in signals:
        lines.append("- **%s %s**  %s"
                     % (s.get("name") or "", s.get("code") or "",
                        ICON.get(s.get("kind"), s.get("kind"))))
        lines.append("  ↳ " + str(s.get("detail") or ""))
    lines.append("")
    lines.append("口径：阻力=昨收；支撑=min(昨收, 近5日低点)；"
                 "突破/跌破幅度 0.3%；压单需连续 2 次快照（防幌骗）。")
    lines.append("⚠️ 观察器，**不是买卖建议**；阈值未回测校准。")
    return "\n".join(lines)


def status(day: str = None) -> Dict:
    """清单 + 当日关键位 + 已触发信号（接口/前端用）。"""
    ensure_tables()
    d = str(day or datetime.now(_BJ).strftime("%Y-%m-%d"))[:10]
    targets = list_targets()
    out = []
    for t in targets:
        code = str(t.get("code"))
        lv = get_levels(code, d)
        out.append({"code": code, "name": t.get("name") or "",
                    "support": lv.get("support"), "resistance": lv.get("resistance"),
                    "s_src": lv.get("s_src"), "r_src": lv.get("r_src")})
    try:
        logs = [dict(r) for r in (db.fetch(
            f"SELECT * FROM {_T_LOG} WHERE date=%s ORDER BY pushed_at DESC", (d,)) or [])]
    except Exception:
        logs = []
    return {"ok": True, "date": d, "max_codes": MAX_CODES,
            "n": len(out), "targets": out, "signals": logs,
            "rules": {
                "resistance": "昨收价",
                "support": "min(昨收, 近5日最低)",
                "break_pct": BREAK_PCT,
                "wall_abs": WALL_ABS, "wall_ratio": WALL_RATIO,
                "interval_sec": CHECK_INTERVAL,
            }}
