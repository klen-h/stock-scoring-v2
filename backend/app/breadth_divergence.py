# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】广度-指数背离观察器 —— 回答"指数看着还行，我的股票却在跌"
================================================================================

【它解决什么】
  2026-11-20 实测：沪指 -1.23%，但全市场个股**平均 -4.18%**、92% 在跌。
  指数被权重股（银行/两桶油）拉抬，掩盖了广度崩塌 ⇒
  任何"沪指跌 3% 才减仓"式的**指数风控在这种结构里完全失效**：
  指数只跌 1.2% 时，个股已经在失血 4%。

【指标：等权 vs 市值加权】
  equal_pct = avg(个股涨跌)                      ← 你的真实体感
  cap_pct   = Σ(涨跌 × 流通市值) / Σ(流通市值)    ← 指数想表达的东西
  gap_pp    = cap_pct − equal_pct

  gap > 0 ⇒ 大市值抗跌、小市值杀跌（"指数假象"）
  gap < 0 ⇒ 大市值拖累、小市值活跃

【为什么不用现成指数】
  项目**没有持久化指数日线**（`market_snapshot` 存的是个股快照；
  `market_tail_snapshot.idx_json` 只有当天尾盘）。
  ⇒ 用 `stock_facts_wide` 的 `chg_pct × float_cap_yi` 自算。
  好处：口径完全自洽（同一份数据、同一交易日、同一批股票）。
  实测校验：11-20 算得 cap_pct = **-1.44%**，而当日沪指 **-1.23%** ⇒ 吻合。

【口径边界（必须知道）】
  · `stock_facts_wide` 约 2800 只（含 ST/北交所，比 `backtest_prices` 的 1099 只更全）
  · 流通市值用**当日**快照加权（严格说应用前一日市值，偏差在单日 <0.1pp，可忽略）
  · `chg_pct` 为 NULL 的行剔除（停牌/新股）
  · **日频**：数据由日批写入 ⇒ 收盘后才有当天值；盘中不更新（盘中要看实时行情，
    那是另一条链路，本模块刻意不碰，避免与 `market_emotion` 抢行情源）

【判档（阈值可用环境变量覆盖）】
  gap ≥ 2.0pp 且 下跌占比 ≥ 80%  ⇒ severe  「严重背离·指数假象」
  gap ≥ 1.0pp                    ⇒ mild    「轻度背离·大市值抗跌」
  gap ≤ -1.0pp                   ⇒ reverse 「反向背离·权重拖累」
  其余                            ⇒ none    「一致」

  ⚠️ 阈值来自 2026-11 实测分布（近 12 日 gap 中位 0.77、极值 2.74），
     **不是统计最优**；样本会随积累变化，调参前先看 `series`。

【Guardrails】
  · 纯观察器：**不改评分、不进准入闸门、不影响任何决策链**（与 tell_watch 同纪律）
  · 只报事实与口径，不给"该买该卖"的指令
  · 失败一律降级（返回 ok=False + 原因），绝不抛给调用方
================================================================================
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_TABLE = "stock_facts_wide"
_BJ = timezone(timedelta(hours=8))

# 阈值（pp = percentage point）
GAP_SEVERE = float(os.environ.get("BD_GAP_SEVERE") or 2.0)
GAP_MILD = float(os.environ.get("BD_GAP_MILD") or 1.0)
DOWN_RATIO_HIGH = float(os.environ.get("BD_DOWN_RATIO") or 0.80)
SERIES_DAYS = int(os.environ.get("BD_SERIES_DAYS") or 20)

_LEVEL_TEXT = {
    "severe": "严重背离·指数假象",
    "mild": "轻度背离·大市值抗跌",
    "reverse": "反向背离·权重拖累",
    "none": "一致",
}
_LEVEL_TONE = {
    "severe": "risk",      # 指数假象 = 风险提示（指数在骗人）
    "mild": "watch",
    "reverse": "info",
    "none": "ok",
}


def _f(v) -> Optional[float]:
    """Decimal/None → float；转不动返回 None（不填 0，0 会被读成"持平"）。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute(rows: List[Dict]) -> Dict:
    """纯函数：给定一批 {chg_pct, float_cap_yi} 算等权/加权/家数。

    抽成纯函数是为了可单测——不依赖数据库（与 `t_trade.auto_build_allowed` 同理）。
    """
    rets, caps = [], []
    up = down = flat = 0
    for r in rows or []:
        chg = _f(r.get("chg_pct"))
        cap = _f(r.get("float_cap_yi"))
        if chg is None or cap is None or cap <= 0:
            continue
        rets.append(chg)
        caps.append(cap)
        if chg > 0:
            up += 1
        elif chg < 0:
            down += 1
        else:
            flat += 1

    n = len(rets)
    if not n:
        return {"ok": False, "error": "无可用的涨跌幅/市值数据", "n": 0}

    eq = sum(rets) / n
    cap_sum = sum(caps)
    cap_w = sum(r * c for r, c in zip(rets, caps)) / cap_sum if cap_sum > 0 else None
    gap = (cap_w - eq) if cap_w is not None else None

    return {
        "ok": True,
        "n": n,
        "up": up, "down": down, "flat": flat,
        "up_ratio": round(up / n, 4),
        "down_ratio": round(down / n, 4),
        "equal_pct": round(eq, 2),
        "cap_pct": round(cap_w, 2) if cap_w is not None else None,
        "gap_pp": round(gap, 2) if gap is not None else None,
    }


def classify(stats: Dict) -> Dict:
    """分档：只看 gap + 下跌占比，不掺主观。"""
    gap = stats.get("gap_pp")
    if gap is None:
        return {"level": "unknown", "level_label": "数据不足", "tone": "info"}
    down_ratio = float(stats.get("down_ratio") or 0)
    if gap >= GAP_SEVERE and down_ratio >= DOWN_RATIO_HIGH:
        lv = "severe"
    elif gap >= GAP_MILD:
        lv = "mild"
    elif gap <= -GAP_MILD:
        lv = "reverse"
    else:
        lv = "none"
    return {"level": lv, "level_label": _LEVEL_TEXT[lv], "tone": _LEVEL_TONE[lv]}


def _hint(cls: Dict) -> str:
    """一句解读（只有结论，数字由前端另行展示，避免同一屏重复两遍）。"""
    return {
        "severe": "大市值在护盘、个股在杀跌，指数会低估你的实际亏损",
        "mild": "大市值相对抗跌，个股弱于指数",
        "reverse": "权重股拖累指数，个股反而更活跃",
        "none": "指数与个股表现基本一致",
    }[cls["level"]]


def _text(stats: Dict, cls: Dict) -> str:
    """完整一句话（含数字），给接口/日志用；前端卡面用 `hint` 免得数字重复。"""
    eq, cp, gap = stats.get("equal_pct"), stats.get("cap_pct"), stats.get("gap_pp")
    n, down, dr = stats.get("n"), stats.get("down"), stats.get("down_ratio")
    if eq is None or cp is None:
        return "数据不足"
    return ("市值加权 %+.2f%% vs 等权 %+.2f%%（差 %+.2fpp）；%d 只中 %d 只下跌（%.0f%%）"
            "—— %s" % (cp, eq, gap, n, down, (dr or 0) * 100, _hint(cls)))


def _latest_date(day: str = None) -> Optional[str]:
    if day:
        return str(day)[:10]
    try:
        r = db.fetch_one("SELECT MAX(date) AS d FROM %s" % _TABLE) or {}
        return str(r.get("d") or "")[:10] or None
    except Exception as e:
        print("[breadth-div] latest date failed: %s" % str(e)[:80])
        return None


def _series(day: str) -> List[Dict]:
    """近 N 个交易日的等权/加权/gap 序列（一条 SQL 取回，别循环查）。"""
    try:
        rows = db.fetch("""
            SELECT date,
                   count(*) AS n,
                   avg(chg_pct) AS eq_pct,
                   sum(chg_pct * float_cap_yi) / NULLIF(sum(float_cap_yi), 0) AS cap_pct
            FROM {T}
            WHERE date IN (
                SELECT DISTINCT date FROM {T}
                WHERE date <= %s ORDER BY date DESC LIMIT %s
            )
              AND chg_pct IS NOT NULL AND float_cap_yi > 0
            GROUP BY date ORDER BY date DESC
        """.replace("{T}", _TABLE), (day, SERIES_DAYS)) or []
    except Exception as e:
        print("[breadth-div] series failed: %s" % str(e)[:80])
        return []

    out = []
    for r in rows:
        eq, cp = _f(r.get("eq_pct")), _f(r.get("cap_pct"))
        out.append({
            "date": str(r.get("date"))[:10],
            "n": int(r.get("n") or 0),
            "equal_pct": round(eq, 2) if eq is not None else None,
            "cap_pct": round(cp, 2) if cp is not None else None,
            "gap_pp": round(cp - eq, 2) if (eq is not None and cp is not None) else None,
        })
    return out


def snapshot(day: str = None) -> Dict:
    """当日背离快照 + 近 N 日序列。失败降级，不抛。"""
    d = _latest_date(day)
    if not d:
        return {"ok": False, "error": "无数据", "date": None,
                "series": [], "thresholds": _thresholds()}

    try:
        rows = db.fetch("""
            SELECT code, chg_pct, float_cap_yi FROM {T}
            WHERE date = %s AND chg_pct IS NOT NULL AND float_cap_yi > 0
        """.replace("{T}", _TABLE), (d,)) or []
    except Exception as e:
        return {"ok": False, "error": "查询失败: %s" % str(e)[:90], "date": d,
                "series": [], "thresholds": _thresholds()}

    stats = compute(rows)
    if not stats.get("ok"):
        return {"ok": False, "error": stats.get("error"), "date": d,
                "series": [], "thresholds": _thresholds()}

    cls = classify(stats)
    return {
        "ok": True,
        "date": d,
        "level": cls["level"],
        "level_label": cls["level_label"],
        "tone": cls["tone"],
        "equal_pct": stats["equal_pct"],
        "cap_pct": stats["cap_pct"],
        "gap_pp": stats["gap_pp"],
        "n": stats["n"],
        "up": stats["up"],
        "down": stats["down"],
        "up_ratio": stats["up_ratio"],
        "down_ratio": stats["down_ratio"],
        "text": _text(stats, cls),
        "hint": _hint(cls),
        "series": _series(d),
        "thresholds": _thresholds(),
        "note": ("口径：等权 = 个股涨跌幅均值（你的体感）；市值加权 = 按流通市值加权"
                 "（指数想表达的东西）。数据来自 stock_facts_wide 日批快照，"
                 "约 2800 只；日频，收盘后更新。纯观察，不进决策链。"),
    }


def _thresholds() -> Dict:
    return {"gap_severe_pp": GAP_SEVERE, "gap_mild_pp": GAP_MILD,
            "down_ratio_high": DOWN_RATIO_HIGH, "series_days": SERIES_DAYS}
