# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】宏观状态概率账（macro_regime_class）—— 四类互斥分布 + 证据留痕
================================================================================
设计文档：`docs/宏观状态概率账设计_20261107.md`
功能说明：`docs/宏观状态概率账_功能说明_20261107.md`

★ 当前进度（2026-11-07）：设计文档 §八 的第 1~3 步已完成 ——
  `compute()` 纯函数 + 两张表 + 单测 + **已挂进日批**
  （`scripts/daily_batch.py` 的 `task_macro_class`，注册名 `macro_class`，
   排在 `thesis_evidence` 之后，读的就是那一行证据快照）。
  **只落库、不出界面**：无 API 端点、无前端组件，`load()` 是唯一读口。
  第 4 步（5 个交易日人工核对）待 11-14；第 5 步（只读端点 + 观察器）未做。

★ 硬约束：不进任何决策链 —— 评分权重 / 买入闸门 / 仓位 / 模拟盘 / 仓位提示
  一律不接（那些链路已有 FAIL/KILL 结论，见设计文档 §五）。

★★ 核心原则：**分布必须是证据的函数，不是判断的函数。**

  所以 `compute()` 是**纯函数**：输入一坨特征值，输出分布；不连库、不调 LLM、
  不读全局状态。给定同一份 evidence + 同一个 `SCORER_VERSION`，
  **任何人都能推出完全相同的 dist** —— 这才叫可复算，才谈得上可证伪。

  （对照：`llm.scenarios` 的概率不可复算，只能当叙事；见 `a1e35ecc`。）

★ 四类（与 `stagflation` 的关系，详见设计文档 §二）：
    soft      软着陆   ≈ stagflation 的"正常"
    stag      滞胀     =  "滞胀交易"
    rec       衰退     ≈ "衰退前夜"
    overheat  过热     ← stagflation 没有这一态（新增）

★ 与 `stagflation` **并行观察、不合并**：那套是单状态位 + 2 日滞回，
  本模块是**当日分布**；合并需要先证明两者长期一致，现在没有证据。

★ 缺失证据：**不进分母**（"缺数据 ≠ 不满足"，同 `policy_bottom_watch`）。
  把缺失当 0 会让"没读到铜价"变成"铜没涨"，直接把 rec 判错。
================================================================================
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_CLASS = "macro_regime_class"
_EVENTS = "macro_regime_events"
_TABLE_READY = False

_BJ = timezone(timedelta(hours=8))

# 判据版本：**改任何阈值必须递增**，否则不允许覆盖历史（验收标准 §六.5）
SCORER_VERSION = "2026-11-07.1"

# 单类至少要有几条可评估证据才参与归一化；低于此值该类标 insufficient
MIN_EVIDENCE = int(os.environ.get("MRC_MIN_EVIDENCE") or 2)

CLASSES = ("soft", "stag", "rec", "overheat")
CLASS_CN = {"soft": "软着陆", "stag": "滞胀", "rec": "衰退", "overheat": "过热"}

# ── 判据（每类 6 条；阈值来源见设计文档 §三）──────────────────────────────
# ⚠️ 判据是**函数**而不是常量表，是为了让 `stag` / `overheat` 在油价与 10Y 上
#    用同一阈值、却在美元/铜/纳指上分开 —— 这正是两者的区别所在。
_RULES: Dict[str, Dict[str, callable]] = {
    "soft": {
        "brent_chg5":    lambda v: v <= 2,
        "us10y_chg5_bp": lambda v: v <= 5,
        "dxy_chg5":      lambda v: abs(v) <= 1.0,
        "copper_chg5":   lambda v: v >= -2,
        "nasdaq_chg5":   lambda v: v >= 0,
        "vix":           lambda v: v <= 20,
    },
    "stag": {
        "brent_chg5":    lambda v: v >= 5,
        "us10y_chg5_bp": lambda v: v >= 15,
        "dxy_chg5":      lambda v: v >= 1.0,
        "copper_chg5":   lambda v: v <= -2,
        "nasdaq_chg5":   lambda v: v <= -2,
        "vix":           lambda v: 20 <= v <= 28,
    },
    "rec": {
        "brent_chg5":    lambda v: v <= -5,
        "us10y_chg5_bp": lambda v: v <= -15,
        "dxy_chg5":      lambda v: v >= 1.5,
        "copper_chg5":   lambda v: v <= -5,
        "nasdaq_chg5":   lambda v: v <= -5,
        "vix":           lambda v: v >= 28,
    },
    "overheat": {
        "brent_chg5":    lambda v: v >= 5,
        "us10y_chg5_bp": lambda v: v >= 15,
        "dxy_chg5":      lambda v: v <= -1,
        "copper_chg5":   lambda v: v >= 3,
        "nasdaq_chg5":   lambda v: v >= 3,
        "vix":           lambda v: v <= 18,
    },
}


# ══════════════════════════════════════════════════════════════════════════
# 纯函数：证据 → 分布
# ══════════════════════════════════════════════════════════════════════════
def compute(evidence: Dict[str, Optional[float]]) -> Dict:
    """证据特征 → 四类分布。**纯函数**（不连库/不调 LLM/不读全局）。

    输入键（缺谁就传 None，**不要传 0**）：
      brent_chg5     布伦特 5 个快照日变化 %
      us10y_chg5_bp  美 10Y 5 个快照日变化（bp）
      dxy_chg5       美元指数 5 日变化 %
      copper_chg5    铜 5 日变化 %
      nasdaq_chg5    纳指 5 日变化 %
      vix            当前 VIX 水平

    输出：
      {"dist": {...} | None, "evidence_status": "ok"|"insufficient",
       "per_class": {...}, "note": str}

    ★ 为什么"可评估类别 < 2 就不给分布"：只剩一类可评时谈不上"分布"，
      给 100% 会让人误以为"高度确定"，实际是"其余都没数据"。
    """
    per_class: Dict[str, Dict] = {}
    for cls, rules in _RULES.items():
        hit, avail, support, missing = 0, 0, [], []
        for key, pred in rules.items():
            v = evidence.get(key)
            if v is None:                 # ★ 缺失：不进分母
                missing.append(key)
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                missing.append(key)
                continue
            avail += 1
            if pred(fv):
                hit += 1
                support.append(key)
        per_class[cls] = {
            "hit": hit, "avail": avail,
            "score": round(hit / avail, 4) if avail else None,
            "support": support, "missing": missing,
        }

    usable = {c: d for c, d in per_class.items()
              if d["avail"] >= MIN_EVIDENCE and d["score"] is not None}
    if len(usable) < 2:
        return {"dist": None, "evidence_status": "insufficient",
                "per_class": per_class,
                "note": (f"可评估类别 {len(usable)} 个（每类需 ≥{MIN_EVIDENCE} 条证据）"
                         f"⇒ 不强行给分布；缺证据 ≠ 该类不成立")}

    total = sum(d["score"] for d in usable.values())
    if total <= 0:
        return {"dist": None, "evidence_status": "insufficient",
                "per_class": per_class,
                "note": "可用类别得分全为 0 ⇒ 不给分布（不拿均匀分布冒充结论）"}

    dist = {c: round(d["score"] / total, 4) for c, d in usable.items()}
    ks = list(dist)
    dist[ks[-1]] = round(dist[ks[-1]] + (1.0 - sum(dist.values())), 4)  # 末位补残差
    return {"dist": dist, "evidence_status": "ok", "per_class": per_class,
            "note": ""}


# ══════════════════════════════════════════════════════════════════════════
# 建表（双库分支：SERIAL 在 SQLite 不自增）
# ══════════════════════════════════════════════════════════════════════════
def ensure_tables() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    pk = "SERIAL PRIMARY KEY" if db.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_CLASS} (
            date TEXT PRIMARY KEY,
            dist_json TEXT,
            evidence_status TEXT,
            per_class_json TEXT,
            support_json TEXT,
            contradict_json TEXT,
            observed_period TEXT,
            scorer_version TEXT,
            updated_at TEXT
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_EVENTS} (
            id {pk},
            date TEXT,
            old_dist TEXT,
            new_dist TEXT,
            delta_json TEXT,
            reason TEXT,
            trigger_event TEXT,
            created_by TEXT,
            created_at TEXT
        )
    """)
    _TABLE_READY = True


# ══════════════════════════════════════════════════════════════════════════
# 取数：把 thesis_evidence 的历史拼成特征
# ══════════════════════════════════════════════════════════════════════════
_LOOKBACK = 5          # 5 个**快照行**（≈5 个交易日），不是自然日


def _bj_now() -> datetime:
    return datetime.now(_BJ)


def _default_day() -> str:
    """默认日期 = **最近已完成交易日**（与日批 `_batch_trading_day()` 同源）。

    ★★ 2026-11-07 实测踩坑：原实现用"今天"。周六手动跑一次就写进了 `date=11-07`
      （一个不存在的交易日），而 `load()` 是 `ORDER BY date DESC` ⇒ **11-07 永远排在
      11-06 前面**，"最近一天"从此指向那份伪日期，且没有任何报错。
      这与项目反复踩的「写入时刻 ≠ 数据所属时刻」是同一类错误。
    """
    try:
        from app.flash.rules import latest_completed_trading_day
        d = str(latest_completed_trading_day())[:10]
        if d:
            return d
    except Exception:
        pass
    return _bj_now().strftime("%Y-%m-%d")


def _pct(cur, prev):
    try:
        c, p = float(cur), float(prev)
        return round((c / p - 1) * 100, 2) if p else None
    except (TypeError, ValueError):
        return None


def _bp(cur, prev):
    try:
        return round((float(cur) - float(prev)) * 100, 1)
    except (TypeError, ValueError):
        return None


def features(day: str = None) -> Dict:
    """从 `thesis_evidence` 组装 compute() 的输入。缺项为 None。

    ⚠️ 用**快照行**回溯，不是自然日：`thesis_evidence` 只在日批写一行，
       跨周末/长假时"5 个自然日"可能只对应 1 行。
    """
    ensure_tables()
    from app.thesis_tracker import load_evidence
    cur = load_evidence(day) or {}
    cur_assets = ((cur.get("payload") or {}).get("assets") or {}) if cur else {}
    if not cur_assets:
        # ★★ 2026-11-07 实测踩坑：`snapshot()` 默认传"今天"，而 `thesis_evidence`
        #   只在**日批**写一行 ⇒ 周末/节假日/日批未跑时按今天查必然为空，
        #   结果每天都是 insufficient（看起来像"没数据"，实际是**查错了日期**）。
        #   ⇒ 回退到最新一份，并在 `_data_date` 里如实标出用的是哪天的数据。
        cur = load_evidence() or {}
        cur_assets = ((cur.get("payload") or {}).get("assets") or {}) if cur else {}
    if not cur_assets:
        return {}

    base_day = str(cur.get("date") or "")[:10]
    prev = {}
    try:
        if base_day:
            rows = db.fetch(
                f"SELECT date, payload FROM thesis_evidence WHERE date < %s "
                f"ORDER BY date DESC LIMIT %s", (base_day, _LOOKBACK)) or []
        else:
            rows = db.fetch(
                f"SELECT date, payload FROM thesis_evidence "
                f"ORDER BY date DESC LIMIT %s OFFSET 1", (_LOOKBACK,)) or []
        if len(rows) >= _LOOKBACK:
            old = rows[-1]                      # 最早的那份
            old_assets = (json.loads(old.get("payload") or "{}").get("assets") or {})
            prev = old_assets
    except Exception as e:
        print(f"[macro_class] 回溯证据失败（按缺失处理）: {str(e)[:80]}")

    def _price(tbl, k):
        return (tbl.get(k) or {}).get("price")

    return {
        # `_data_date` 不在 _RULES 里 ⇒ compute() 不会读它，仅用于标注数据来源。
        "_data_date": base_day,
        "brent_chg5": _pct(_price(cur_assets, "brent"), _price(prev, "brent")),
        "us10y_chg5_bp": _bp(_price(cur_assets, "us10y"), _price(prev, "us10y")),
        "dxy_chg5": _pct(_price(cur_assets, "dxy"), _price(prev, "dxy")),
        "copper_chg5": _pct(_price(cur_assets, "copper"), _price(prev, "copper")),
        "nasdaq_chg5": _pct(_price(cur_assets, "nasdaq"), _price(prev, "nasdaq")),
        "vix": _price(cur_assets, "vix"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 落库 / 读取
# ══════════════════════════════════════════════════════════════════════════
def snapshot(day: str = None, ev: Dict = None, write: bool = True) -> Dict:
    """算当日分布并落库。`write=False` 只算不写（试跑/核对用）。"""
    ensure_tables()
    day = str(day or _default_day())[:10]
    ev = ev if ev is not None else features(day)
    res = compute(ev)

    if not write:
        return {"date": day, "evidence": ev, **res}

    dist = res["dist"]
    per = res["per_class"]
    support = {c: d["support"] for c, d in per.items() if d["support"]}
    contradict = {c: [k for k in d["missing"] if k] for c, d in per.items()
                  if d["missing"]}
    now = _bj_now().strftime("%Y-%m-%d %H:%M:%S")

    db.upsert(_CLASS, {
        "date": day,
        "dist_json": json.dumps(dist, ensure_ascii=False) if dist else None,
        "evidence_status": res["evidence_status"],
        "per_class_json": json.dumps(per, ensure_ascii=False),
        "support_json": json.dumps(support, ensure_ascii=False),
        "contradict_json": json.dumps(contradict, ensure_ascii=False),
        # observed_period：这些证据对应的数据期。`_data_date` 是**实际用到的快照日**
        # （周末/日批未跑时会回退到最近交易日）⇒ 必须用它，不能填运行日，
        # 否则会把"11-06 的数据"标成"11-07 的环境"（本项目反复踩的时点错位）。
        "observed_period": (ev or {}).get("_data_date") or day,
        "scorer_version": SCORER_VERSION,
        "updated_at": now,
    }, conflict_columns=["date"])

    # 分布变化留痕（append-only）
    if dist:
        try:
            prev = db.fetch_one(
                f"SELECT dist_json FROM {_CLASS} WHERE date < %s "
                f"ORDER BY date DESC LIMIT 1", (day,))
            old = json.loads((prev or {}).get("dist_json") or "null")
            if isinstance(old, dict) and old != dist:
                delta = {c: round((dist.get(c, 0) - old.get(c, 0)) * 100, 1)
                         for c in set(old) | set(dist)}
                db.execute(
                    f"INSERT INTO {_EVENTS} (date, old_dist, new_dist, delta_json, "
                    f"reason, trigger_event, created_by, created_at) "
                    f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (day, json.dumps(old, ensure_ascii=False),
                     json.dumps(dist, ensure_ascii=False),
                     json.dumps(delta, ensure_ascii=False),
                     "证据驱动（规则重算）", "", "rule", now))
        except Exception as e:
            print(f"[macro_class] 事件留痕失败（不影响写入）: {str(e)[:80]}")

    return {"date": day, "evidence": ev, **res}


def load(day: str = None) -> Dict:
    """读某日分布（默认最新）。零网络。"""
    ensure_tables()
    try:
        if day:
            row = db.fetch_one(f"SELECT * FROM {_CLASS} WHERE date=%s",
                               (str(day)[:10],))
        else:
            row = db.fetch_one(f"SELECT * FROM {_CLASS} ORDER BY date DESC LIMIT 1")
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    if not row:
        return {"ok": False, "error": "尚无记录"}

    def _j(v):
        try:
            return json.loads(v) if v else None
        except Exception:
            return None

    dist = _j(row.get("dist_json"))
    return {
        "ok": True, "date": row.get("date"),
        "dist": dist,
        "dist_cn": ({CLASS_CN.get(c, c): v for c, v in dist.items()}
                    if isinstance(dist, dict) else None),
        "evidence_status": row.get("evidence_status"),
        "per_class": _j(row.get("per_class_json")),
        "support": _j(row.get("support_json")),
        "contradict": _j(row.get("contradict_json")),
        "observed_period": row.get("observed_period"),
        "scorer_version": row.get("scorer_version"),
        "note": ("观察器：规则打分 → 归一化，可复算；不进评分/闸门/仓位/模拟盘。"
                 "N<30 前不做校准。"),
    }
