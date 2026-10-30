# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】宏观剧本追踪器（Thesis Tracker）—— 事前登记 + 事后裁决 + 全程留痕
================================================================================
背景（2026-10-30/31 用户多轮探讨收敛，见 memory 2026-10-30 §讨论 与
`docs/kimi/20261030_note_fedwatch_discount.md`）：
  项目里所有既有验证（`edge_verify` / `contradiction_validations` /
  `scenario_watch`）都在回答「**方向对不对**」，没有一处回答「**概率准不准**」。
  本模块补的正是这一格：把宏观判断事前登记为**可证伪的概率命题**，
  事后由数据（或人工）裁决，全程留痕。

--------------------------------------------------------------------------------
一、三条反自欺铁律（本模块的地基，改动前请先读）
--------------------------------------------------------------------------------
  1. **claim / resolution_rule 登记后不可改**。只允许：
       · `revise_thesis()` 改概率（必须带 reason + trigger_event，append-only 留痕）
       · `invalidate_thesis()` 判失效（必须带 reason）
     没有「改结论」的接口 —— 否则就是事后合理化。
  2. **裁决权不在 LLM**：`resolve_open_theses()` 只按 `resolution_rule` 的结构化
     条件求值；LLM 最多只能"翻译"，不能决定输赢。
  3. **没有 deadline / 失效条件的「长期观点」不允许登记** —— 那是信仰，不是假设。

--------------------------------------------------------------------------------
二、三刀（评审 2026-10-31 追加，缺一刀就会变成自我安慰工具）
--------------------------------------------------------------------------------
  ① **backfilled 隔离**：回填的已裁决样本（如 9/16 加息）打 `backfilled=1`，
     永久排除在统计之外，只做 schema 演示与流程彩排。
     校准时钟从**第一条"deadline 前登记、p 先于证据落库"**的样本才开始。
  ② **范畴切干净**：本表只收**离散、带 deadline、可裁决**的命题；
     连续状态（滞胀 ON、市场底清单 2/6）**不进本表**，继续归
     `scenario_watch` / `stagflation`。故 `nature` 只有 prediction / decision。
  ③ **N≥30 前不出校准结论**：`stats()` 只报计数，不产出 Brier / 折扣系数。
     校准曲线需 100+ 样本；配对比较（Brier_mine vs Brier_market 符号检验）
     20~30 条才出初步读数 —— 在此之前任何"我比市场准"都是噪音。

--------------------------------------------------------------------------------
三、表结构
--------------------------------------------------------------------------------
  `thesis_registry`：每条假设一行（登记簿本体）
      id / claim / nature / thesis_type / p_mine / p_market / market_source /
      market_as_of / deadline / resolution_rule / resolution_source /
      invalidation_early / status / backfilled / created_by / created_at

  `thesis_events`：append-only 状态流转与修订留痕
      id / thesis_id / event_type / payload / created_by / created_at
      event_type ∈ register / revise / invalidate / evidence_manual / resolve

  ★ 双库兼容（评审重点提醒的坑）：`database.py` 的 `db` 同时支持 SQLite 与
    PostgreSQL，但 **SERIAL 在 SQLite 不自增、JSONB 在 SQLite 不识别** ——
    故本模块建表按 `db.is_postgres` 分支选主键定义，JSON 一律存 TEXT 列。

--------------------------------------------------------------------------------
四、状态机
--------------------------------------------------------------------------------
      open ──resolve 全满足──→ resolved_win
        │  └resolve 任一不满足─→ resolved_lose
        ├─invalidate（中途失效）→ invalidated
        └─过期未裁决───────────→ expired（由日批/手工触发，非本 v1 自动）

  `invalidated` **不进 Brier 统计**（及时撤销错误假设是好技能，不该和"判断错"混），
  但仍写 thesis_events 留痕，便于日后单独评估"多快撤销一个坏假设"。

--------------------------------------------------------------------------------
五、本 v1 明确不做（后续阶段，别在这里顺手加）
--------------------------------------------------------------------------------
  · 证据采集日批任务（task_thesis_evidence）—— 下周
  · 周报 / 决策卡 / LLM 抽取层 —— 12/10 拿到第一条真实样本后再决定
  · 校准曲线 / 折扣系数产出 —— N≥30 后
  · 前端页面 —— 纯后端
================================================================================
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.database import db

_REG = "thesis_registry"
_EVT = "thesis_events"
_EVID = "thesis_evidence"       # 全局证据快照（每日一行，抄值留痕）
_READY = False

# 证据快照抄哪些品种（白名单）。
# ★ 不抄整份 panel：存储已 408MB/500MB（2026-10-29 实测），每日整份落库是浪费；
#   这 11 个覆盖"滞胀/利率冲击/衰退"三档所需的全部判别变量。
_EVID_ASSETS = ("brent", "wti", "gold", "silver", "copper",
                "us10y", "dxy", "usdcnh", "nasdaq", "a50", "vix")

# ── 枚举（写死，防止脏值污染统计）────────────────────────────────────────
VALID_NATURE = ("prediction", "decision")
VALID_STATUS = ("open", "resolved_win", "resolved_lose", "invalidated", "expired")
VALID_RESOLUTION_SOURCE = ("auto", "manual")

# 校准门槛（评审铁律③：N≥30 前不出结论）
CALIBRATION_MIN_N = int(os.environ.get("THESIS_CALIB_MIN_N") or 30)


# ══════════════════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════════════════
def _now_utc() -> datetime:
    """当前 UTC 时间（★ 卫生门会拦无时区的 now()，生产容器是 UTC）。"""
    return datetime.now(timezone.utc)


def _now_bjt() -> datetime:
    """当前北京时间（**naive**）——用于与 deadline（BJT 口径、naive）比较。

    ★ 必须 `.replace(tzinfo=None)`：`datetime.now(timezone.utc)` 是 **aware**，
      而 `_parse_dt()` 返回 **naive**，两者比较会抛
      `can't compare offset-naive and offset-aware datetimes`。
    """
    return (_now_utc() + timedelta(hours=8)).replace(tzinfo=None)


def _ts(dt: datetime = None) -> str:
    return (dt or _now_utc()).strftime("%Y-%m-%d %H:%M:%S")


def _dumps(obj) -> Optional[str]:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)


def _loads(raw, default):
    """JSON 反序列化。★ 兼容两种脏数据：TEXT 原始串 / 驱动已解析的对象。"""
    if raw is None:
        return default
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default
    return raw


def _norm_p(p) -> Optional[float]:
    """概率归一化到 0~1 小数。

    ★ 入参可能来自表单（65）或代码（0.65），统一归一 —— 否则 Brier 会差 100 倍，
      且这种错误**不会报错**，是最容易静默污染统计的地方。
    """
    if p is None or p == "":
        return None
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    if v > 1.0:
        v = v / 100.0
    return max(0.0, min(1.0, v))


def _num(v) -> Optional[float]:
    """安全转 float（历史数据里可能是字符串/None）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_dt(s) -> Optional[datetime]:
    """解析日期/时间字符串为 naive datetime（兼容 `2026/9/7` 斜杠格式）。

    ★ `macro_daily.date` 与历史脏数据混着斜杠格式；字符串排序会把 10 月排到
      9 月前面（stagflation.py / policy_bottom_watch 都踩过同型坑）。
    """
    s = str(s or "").strip()
    if not s:
        return None
    s = s.replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _dig(obj, path: str):
    """按 `a.b.c` 点路径取值；任一层不是 dict 或缺键 → None。"""
    cur = obj
    for part in str(path or "").split("."):
        if not part:
            continue
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


# ══════════════════════════════════════════════════════════════════════════
# 建表
# ══════════════════════════════════════════════════════════════════════════
def ensure_tables() -> None:
    """建两张表（幂等）。★ 双库分支见模块文档头「双库兼容」。"""
    global _READY
    if _READY:
        return

    # SQLite 必须 INTEGER PRIMARY KEY AUTOINCREMENT 才有自增；
    # PostgreSQL 用 SERIAL PRIMARY KEY。
    pk = "SERIAL PRIMARY KEY" if db.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"

    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_REG} (
            id {pk},
            claim TEXT NOT NULL,
            nature TEXT NOT NULL,
            thesis_type TEXT,
            p_mine REAL,
            p_market REAL,
            market_source TEXT,
            market_as_of TEXT,
            deadline TEXT,
            resolution_rule TEXT,
            resolution_source TEXT,
            invalidation_early TEXT,
            status TEXT,
            backfilled INTEGER DEFAULT 0,
            created_by TEXT,
            created_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_EVT} (
            id {pk},
            thesis_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT,
            created_by TEXT,
            created_at TEXT
        )
    """)
    # ③ 全局证据快照（每日一行；date 作主键 ⇒ 同日重跑只覆盖，天然幂等）
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_EVID} (
            date TEXT PRIMARY KEY,
            as_of TEXT,
            data_ts TEXT,
            payload TEXT,
            created_at TEXT
        )
    """)
    # 索引：裁决器扫 status='open'、按 thesis_id 查事件，都是高频路径。
    for sql in (f"CREATE INDEX IF NOT EXISTS idx_{_REG}_status ON {_REG}(status)",
                f"CREATE INDEX IF NOT EXISTS idx_{_EVT}_thesis ON {_EVT}(thesis_id)"):
        try:
            db.execute(sql)
        except Exception as e:      # 索引失败不影响主流程（部分旧库权限受限）
            print(f"[thesis] 建索引失败: {str(e)[:80]}")
    _READY = True


# ══════════════════════════════════════════════════════════════════════════
# 内部：事件写入
# ══════════════════════════════════════════════════════════════════════════
def _add_event(thesis_id: int, event_type: str, payload: Dict = None,
               created_by: str = "user") -> None:
    """append-only 写一条事件（永不更新、永不删除）。"""
    db.execute(
        f"INSERT INTO {_EVT} (thesis_id, event_type, payload, created_by, created_at) "
        f"VALUES (%s, %s, %s, %s, %s)",
        (int(thesis_id), event_type, _dumps(payload or {}), created_by, _ts()),
    )


# ══════════════════════════════════════════════════════════════════════════
# 登记
# ══════════════════════════════════════════════════════════════════════════
def register_thesis(claim: str, nature: str, p_mine,
                    p_market=None, market_source: str = None,
                    deadline: str = None, resolution_rule: Dict = None,
                    resolution_source: str = "manual",
                    invalidation_early: str = None,
                    created_by: str = "user", backfilled: bool = False,
                    thesis_type: str = None, market_as_of: str = None,
                    status: str = None) -> int:
    """登记一条假设，返回 thesis id（幂等：同 claim 已存在则直接返回其 id）。

    参数：
      nature        : prediction / decision（状态类命题会被拒绝，见铁律②）
      p_mine        : 0~1 或百分数（自动归一）
      resolution_rule: {"logic":"AND","conditions":[{field,op,value,source},...]}
      backfilled    : True = 回填的已裁决样本，永久排除统计
      status        : 默认 open；回填演示样本可传 resolved_win
    """
    ensure_tables()

    claim = (claim or "").strip()
    if not claim:
        raise ValueError("claim 不能为空")
    nature = (nature or "").strip()
    if nature not in VALID_NATURE:
        raise ValueError(
            f"nature 必须是 {VALID_NATURE} 之一；连续状态类命题"
            "（如滞胀状态位、市场底清单）请归 scenario_watch / stagflation，不进本表")

    rs = (resolution_source or "manual").strip().lower()
    if rs not in VALID_RESOLUTION_SOURCE:
        raise ValueError(f"resolution_source 必须是 {VALID_RESOLUTION_SOURCE} 之一")

    st = (status or "open").strip()
    if st not in VALID_STATUS:
        raise ValueError(f"status 必须是 {VALID_STATUS} 之一")

    # ── 幂等：同 claim 已存在直接返回（seed 可重复执行）──────────────────
    row = db.fetch_one(
        f"SELECT id FROM {_REG} WHERE claim=%s ORDER BY id DESC LIMIT 1", (claim,))
    if row:
        return int(row["id"])

    db.insert(_REG, {
        "claim": claim,
        "nature": nature,
        "thesis_type": thesis_type,
        "p_mine": _norm_p(p_mine),
        "p_market": _norm_p(p_market),
        "market_source": market_source,
        "market_as_of": market_as_of,
        "deadline": str(deadline).strip() if deadline else None,
        "resolution_rule": _dumps(resolution_rule),
        "resolution_source": rs,
        "invalidation_early": invalidation_early,
        "status": st,
        "backfilled": 1 if backfilled else 0,
        "created_by": created_by,
        "created_at": _ts(),
    })
    # ★ 不用 db.insert 的返回值当 id：它实际返回的是 rowcount（见 database.py
    #   `insert()` → `execute()` → `cur.rowcount`），双库都是 1。必须回查。
    row = db.fetch_one(
        f"SELECT id FROM {_REG} WHERE claim=%s ORDER BY id DESC LIMIT 1", (claim,))
    tid = int((row or {}).get("id") or 0)

    _add_event(tid, "register", {
        "claim": claim, "nature": nature,
        "p_mine": _norm_p(p_mine), "p_market": _norm_p(p_market),
        "backfilled": bool(backfilled), "status": st,
    }, created_by)
    return tid


# ══════════════════════════════════════════════════════════════════════════
# 修订 / 失效 / 人工证据
# ══════════════════════════════════════════════════════════════════════════
def revise_thesis(thesis_id: int, new_p, reason: str,
                  trigger_event: str = None, created_by: str = "user") -> bool:
    """修订概率（**只能改 p_mine，不能改 claim / resolution_rule**）。

    ★ 铁律①：must 带 reason（为什么改）；trigger_event 建议填（什么事件推动的）。
      3 个月后靠这条记录区分「信息驱动」与「情绪驱动」的修订。
    """
    ensure_tables()
    row = db.fetch_one(f"SELECT * FROM {_REG} WHERE id=%s", (int(thesis_id),))
    if not row:
        return False
    if row.get("status") != "open":
        print(f"[thesis] 只能修订 open 的假设（当前 {row.get('status')}）")
        return False
    if not (reason or "").strip():
        raise ValueError("修订必须写 reason（不允许裸改概率）")

    old_p = _norm_p(row.get("p_mine"))
    new_p_n = _norm_p(new_p)
    db.execute(f"UPDATE {_REG} SET p_mine=%s WHERE id=%s", (new_p_n, int(thesis_id)))
    _add_event(thesis_id, "revise", {
        "old_p": old_p, "new_p": new_p_n,
        "reason": reason, "trigger_event": trigger_event,
    }, created_by)
    return True


def invalidate_thesis(thesis_id: int, reason: str,
                      created_by: str = "user") -> bool:
    """中途判失效（如"11 月 CPI 后 FedWatch 跌破 65% → 转按兵不动分支"）。

    ★ invalidated 不进 Brier 统计，但留痕（可单独评估"撤销速度"）。
    """
    ensure_tables()
    row = db.fetch_one(f"SELECT * FROM {_REG} WHERE id=%s", (int(thesis_id),))
    if not row or row.get("status") != "open":
        return False
    if not (reason or "").strip():
        raise ValueError("失效必须写 reason")

    db.execute(f"UPDATE {_REG} SET status=%s WHERE id=%s",
               ("invalidated", int(thesis_id)))
    _add_event(thesis_id, "invalidate", {"reason": reason}, created_by)
    return True


def add_manual_evidence(thesis_id: int, field: str, value,
                        source_url: str, entered_by: str = "user") -> bool:
    """录入人工证据（点阵图 2027 中值、决议声明等无自动采集通道的字段）。

    ★ `source_url` 强制必填：评审要求"录入值必须带原文出处链接"，
      否则事后无法审计这个值是从哪来的。
    """
    ensure_tables()
    row = db.fetch_one(f"SELECT id FROM {_REG} WHERE id=%s", (int(thesis_id),))
    if not row:
        return False
    if not (source_url or "").strip():
        raise ValueError("manual 证据必须携带原文出处链接（source_url）")
    if not (field or "").strip():
        raise ValueError("field 不能为空")

    _add_event(thesis_id, "evidence_manual", {
        "field": field.strip(), "value": value, "source_url": source_url.strip(),
    }, entered_by)
    return True


# ══════════════════════════════════════════════════════════════════════════
# 裁决器 v1
# ══════════════════════════════════════════════════════════════════════════
def _load_macro_latest_with_date() -> Tuple[Dict, str]:
    """读 `macro_daily` 最新一份快照，返回 (data_json, 快照日期 YYYY-MM-DD)。

    ★ 日期是 TEXT 且混斜杠格式 ⇒ 必须解析成 datetime 比较，不能字符串 MAX。
    """
    try:
        rows = db.fetch("SELECT date, data_json FROM macro_daily") or []
    except Exception as e:
        print(f"[thesis] macro_daily 不可读: {str(e)[:80]}")
        return {}, ""
    best_dt, best = None, {}
    for r in rows:
        dt = _parse_dt(r.get("date"))
        if dt is None:
            continue
        if best_dt is None or dt > best_dt:
            obj = _loads(r.get("data_json"), {})
            best_dt, best = dt, (obj if isinstance(obj, dict) else {})
    return best, (best_dt.strftime("%Y-%m-%d") if best_dt else "")


def _load_macro_latest() -> Dict:
    """读 `macro_daily` 最新快照内容（裁决器用，不需要日期）。"""
    return _load_macro_latest_with_date()[0]


# ══════════════════════════════════════════════════════════════════════════
# 证据快照（全局每日一份，抄值留痕）
# ══════════════════════════════════════════════════════════════════════════
def collect_evidence(day: str = None) -> Dict:
    """把 `macro_daily` 最新快照的关键品种**抄一份**到 `thesis_evidence`。

    ★ 为什么"抄值"而不是"引用"（评审明确提醒）：`macro_daily` 的行可能被
      重算/改写（历史回填、口径调整），直接引用等于让**历史证据随今天的数据
      变化** —— 那就不叫证据了。抄下来才是"当时的读数"。
    ★ 抄哪些：`_EVID_ASSETS` 白名单，不抄整份 panel（存储已吃紧）。
    ★ 幂等：`date` 主键 upsert，同日重跑只覆盖不重复。
    ★ 缺品种如实记入 `missing`（不是静默跳过）—— 否则将来复盘会误以为"当时没数据"。
    """
    ensure_tables()
    day = str(day or _now_bjt().strftime("%Y-%m-%d"))[:10]
    snap, as_of = _load_macro_latest_with_date()
    if not snap:
        return {"ok": False, "error": "macro_daily 无可用快照"}

    panel = snap.get("panel") or {}
    derived = snap.get("derived") or {}
    assets: Dict[str, Dict] = {}
    missing: List[str] = []
    for k in _EVID_ASSETS:
        item = panel.get(k) or {}
        price = _num(item.get("price"))
        if price is None:
            missing.append(k)
            continue
        assets[k] = {"price": price,
                     "change_pct": _num(item.get("change_pct")),
                     "bp_change": _num(item.get("bp_change"))}

    payload = {"as_of": as_of, "assets": assets,
               "derived": derived if isinstance(derived, dict) else {}}
    db.upsert(_EVID, {
        "date": day,
        "as_of": as_of,
        "data_ts": _ts(),
        "payload": _dumps(payload),
        "created_at": _ts(),
    }, conflict_columns=["date"])
    return {"ok": True, "date": day, "as_of": as_of,
            "assets": len(assets), "missing": missing}


def load_evidence(day: str = None) -> Dict:
    """读某日证据快照（默认最新一份）。"""
    ensure_tables()
    try:
        if day:
            row = db.fetch_one(f"SELECT * FROM {_EVID} WHERE date=%s",
                               (str(day)[:10],))
        else:
            # 本表只写 YYYY-MM-DD（无斜杠格式），字符串排序即正确顺序
            row = db.fetch_one(f"SELECT * FROM {_EVID} ORDER BY date DESC LIMIT 1")
    except Exception as e:
        return {"error": str(e)[:120]}
    if not row:
        return {}
    d = dict(row)
    d["payload"] = _loads(row.get("payload"), {})
    return d


def _manual_evidence_map(thesis_id: int) -> Dict[str, Dict]:
    """取该假设**每个字段最新一条** manual 证据（field -> payload）。"""
    rows = db.fetch(
        f"SELECT payload FROM {_EVT} WHERE thesis_id=%s AND event_type='evidence_manual' "
        f"ORDER BY id DESC", (int(thesis_id),)) or []
    out: Dict[str, Dict] = {}
    for r in rows:
        p = _loads(r.get("payload"), {})
        if not isinstance(p, dict):
            continue
        f = p.get("field")
        if f and f not in out:      # DESC 排序 ⇒ 第一次见到的就是最新
            out[f] = p
    return out


def _cmp(actual, expect, op: str) -> bool:
    """比较运算。eq/ne 按字符串（去空白、忽略大小写），其余按数值。"""
    op = (op or "eq").strip().lower()
    if op in ("eq", "==", "="):
        return str(actual).strip().lower() == str(expect).strip().lower()
    if op in ("ne", "!=", "<>"):
        return str(actual).strip().lower() != str(expect).strip().lower()
    try:
        a, b = float(actual), float(expect)
    except (TypeError, ValueError):
        return False
    if op in ("lt", "<"):
        return a < b
    if op in ("le", "<=", "lte"):
        return a <= b
    if op in ("gt", ">"):
        return a > b
    if op in ("ge", ">=", "gte"):
        return a >= b
    return False


def _eval_condition(cond: Dict, snapshot: Dict,
                    manual_ev: Dict[str, Dict]) -> Tuple[Optional[bool], str]:
    """评估单条件。返回 (True / False / None, 人可读说明)。None = 证据不足。"""
    if not isinstance(cond, dict):
        return None, "条件格式错误"
    field = str(cond.get("field") or "").strip()
    if not field:
        return None, "条件缺 field"
    op = cond.get("op") or "eq"
    expect = cond.get("value")
    source = str(cond.get("source") or "auto").strip().lower()

    if source == "manual":
        ev = manual_ev.get(field)
        if not ev:
            return None, f"{field} 待人工录入"
        actual = ev.get("value")
        note = f"{field}={actual}（人工，出处 {ev.get('source_url', '')}）"
    else:
        actual = _dig(snapshot, field)
        note = f"{field}={actual}（auto，macro_daily 最新快照）"

    if actual is None:
        return None, f"{field} 缺值"
    return _cmp(actual, expect, op), note


def resolve_open_theses(now: datetime = None, force: bool = False) -> Dict:
    """扫描 open 假设并对**已到期**者裁决。

    流转规则：
      · 任一条件明确不满足            → resolved_lose
      · 全部条件满足                 → resolved_win
      · 有不可判定但无明确不满足      → 保持 open，返回「待录入」清单

    `force=True` 忽略 deadline（仅调试/彩排用；**不要**在日批里传）。
    """
    ensure_tables()
    now = now or _now_bjt()
    rows = db.fetch(f"SELECT * FROM {_REG} WHERE status='open' ORDER BY id") or []
    snapshot = _load_macro_latest()
    results: List[Dict] = []

    for r in rows:
        tid = int(r.get("id"))
        dl = _parse_dt(r.get("deadline"))
        if dl is None:
            results.append({"id": tid, "action": "skip", "reason": "无 deadline"})
            continue
        if not force and dl > now:
            results.append({"id": tid, "action": "skip",
                            "reason": f"未到期（deadline {r.get('deadline')}）"})
            continue

        rule = _loads(r.get("resolution_rule"), {})
        conds = rule.get("conditions") if isinstance(rule, dict) else None
        if not conds:
            results.append({"id": tid, "action": "skip", "reason": "无 resolution_rule"})
            continue

        manual_ev = _manual_evidence_map(tid)
        verdicts: List[Optional[bool]] = []
        notes: List[str] = []
        for c in conds:
            ok, note = _eval_condition(c, snapshot, manual_ev)
            verdicts.append(ok)
            notes.append(note)

        logic = str(rule.get("logic") or "AND").upper()

        if logic == "OR":
            if any(v is True for v in verdicts):
                status = "resolved_win"
            elif all(v is False for v in verdicts):
                status = "resolved_lose"
            else:
                status = None
        else:   # AND（默认）
            if any(v is False for v in verdicts):
                status = "resolved_lose"
            elif all(v is True for v in verdicts):
                status = "resolved_win"
            else:
                status = None

        if status is None:
            pending = [n for v, n in zip(verdicts, notes) if v is None]
            results.append({"id": tid, "action": "pending",
                            "reason": "；".join(pending) or "条件不可判定"})
            continue

        db.execute(f"UPDATE {_REG} SET status=%s WHERE id=%s", (status, tid))
        _add_event(tid, "resolve",
                   {"status": status, "logic": logic, "notes": notes}, "system")
        results.append({"id": tid, "action": status, "notes": notes})

    return {"ok": True, "checked": len(rows), "results": results}


# ══════════════════════════════════════════════════════════════════════════
# 查询 / 统计
# ══════════════════════════════════════════════════════════════════════════
def list_events(thesis_id: int) -> List[Dict]:
    """某条假设的全部事件（append-only，按写入顺序）。"""
    ensure_tables()
    rows = db.fetch(
        f"SELECT * FROM {_EVT} WHERE thesis_id=%s ORDER BY id ASC",
        (int(thesis_id),)) or []
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = _loads(r.get("payload"), {})
        out.append(d)
    return out


def load_thesis(thesis_id: int) -> Dict:
    """单条假设 + 事件历史。"""
    ensure_tables()
    row = db.fetch_one(f"SELECT * FROM {_REG} WHERE id=%s", (int(thesis_id),))
    if not row:
        return {}
    d = dict(row)
    d["resolution_rule"] = _loads(row.get("resolution_rule"), {})
    d["backfilled"] = bool(row.get("backfilled"))
    d["events"] = list_events(thesis_id)
    return d


def list_theses(status: str = None) -> List[Dict]:
    """列出假设（可按 status 过滤），按 id 倒序。"""
    ensure_tables()
    if status:
        rows = db.fetch(f"SELECT * FROM {_REG} WHERE status=%s ORDER BY id DESC",
                        (status,)) or []
    else:
        rows = db.fetch(f"SELECT * FROM {_REG} ORDER BY id DESC") or []
    out = []
    for r in rows:
        d = dict(r)
        d["resolution_rule"] = _loads(r.get("resolution_rule"), {})
        d["backfilled"] = bool(r.get("backfilled"))
        out.append(d)
    return out


def stats() -> Dict:
    """轻量统计：**只报计数，不产出校准结论**（铁律③：N≥30 前无统计意义）。

    ★ 显式排除 backfilled —— 回填样本会系统性美化胜率。
    """
    ensure_tables()
    try:
        row = db.fetch_one(
            f"SELECT COUNT(*) AS n FROM {_REG} "
            f"WHERE backfilled=0 AND status IN ('resolved_win','resolved_lose')")
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    n = int((row or {}).get("n") or 0)
    return {
        "ok": True,
        "resolved_live": n,
        "backfilled_excluded": True,
        "calibration_ready": n >= CALIBRATION_MIN_N,
        "note": ("样本不足（%d/%d），暂不产出 Brier / 折扣系数"
                 % (n, CALIBRATION_MIN_N) if n < CALIBRATION_MIN_N
                 else "样本已达门槛，可评估校准"),
    }
