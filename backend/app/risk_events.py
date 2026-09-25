"""
================================================================================
【文件作用】风险事件闸门（框架 C1）—— 解禁/减持/质押/问询 ⇒ 命中即"只减不加"
================================================================================

【为什么需要】（《项目专业审视》C2 原话）
  "强势股最典型的死亡路径：**大额解禁 / 股东减持公告 / 质押平仓风险 / 商誉减值 /
   立案调查**。系统目前对这些**零感知** —— 技术面会继续给高分直到崩塌。"
  其中**解禁是唯一可前瞻的**（日期提前数月公开）⇒ 先做它，优先级最高。

【本轮实现范围】只做**限售解禁**（数据源已验证可用）：
  · 减持/问询/立案：暂无结构化数据源（新闻维度可识别，但滞后）⇒ 见文件末尾"未做"。
  · 商誉/净现比：已在 `portfolio_radar._fundamental_flags` 与 `contradictions.l3_scanner` 覆盖。

【★★ 阈值依据 —— 实测回测，不是拍脑袋】（2026-09-25，脚本见当日 daily 记录）
  方法：过去 12 个月 1778 条解禁记录 → 取 `backtest_prices` 可评估的 268 条，
       算"解禁日前 20 个交易日 → 解禁日"的收益，减同期沪深300（=超额）：
  ```
  解禁/流通 ≥10%（大额）  n=60   平均超额 -2.74%  胜率 40.0%  中位 -3.43%
  5%~10%                 n=19   平均超额 -0.61%  胜率 57.9%   （样本不足，不显著）
  <5%                   n=189   平均超额 +1.85%  ← 小额解禁无影响
  ```
  ⇒ **≥10% 档三项指标同向为负**（均值/中位/胜率）⇒ 定为高风险闸门 ✓
  ⇒ 5%~10% 档 n 太小、方向不一致 ⇒ **只提示不拦**（`watch`）✓
  ⚠️ 诚实标注：可评估样本仅占 15%（`backtest_prices` 只覆盖 ~839 只 + 需 20 日历史）
     ⇒ **有选择偏差**（覆盖票偏向"有信号/上榜"的），且未控制其他因子
     ⇒ 这是**关联不是因果**；阈值应随样本增长复核（本表每日落库，可随时重跑）。

【落点】决策卡"负面清单"（`trader_brief.build_decision_card`）——
  命中**持仓**⇒"只减不加"；命中**候选**⇒"暂缓买入"。
"""

import json
from typing import Dict, List, Optional

from app.database import db

# ── 阈值（★ 见上方回测依据）─────────────────────────────────────────────
LIFT_HIGH_RATIO = 10.0      # 解禁市值/流通市值 ≥10% ⇒ high（实测前 20 日超额 -2.74%、胜率 40%）
LIFT_WATCH_RATIO = 5.0      # ≥5% ⇒ watch（仅提示，证据不足不拦）
LIFT_WINDOW_DAYS = 20       # 闸门只看**未来 20 个自然日**内的事件（回测窗口即"解禁前 20 交易日"）
LIFT_SYNC_AHEAD = 60        # 同步时拉未来 60 天（比闸门窗口宽，留余量）
LIFT_SYNC_BACK = 7          # 同时回看 7 天（补漏 + 覆盖刚过去的）


def _ensure_table() -> None:
    """建表（幂等）。★ 数值列一律 TEXT —— SQLite/PostgreSQL 双库零风险。"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS risk_events (
            id SERIAL PRIMARY KEY,
            event_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            event_type TEXT NOT NULL,
            severity TEXT,
            amount_wan TEXT,
            ratio TEXT,
            detail TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(event_date, code, event_type)
        )
    """)


def sync_lift_calendar(ahead_days: int = LIFT_SYNC_AHEAD,
                       back_days: int = LIFT_SYNC_BACK) -> Dict:
    """同步限售解禁日历到 `risk_events`（幂等：`(event_date, code, event_type)` 唯一）。

    ⚠️ 占比 `ratio` = 解禁市值 ÷ **流通市值**（`tencent._cache` 的 `float_cap`，单位同为万元）。
      行情缓存未就绪时 `ratio` 存空 ⇒ 闸门会**保守地不判高风险**（宁可漏，不可误拦）。
    ⚠️ 由日批调用（那时行情缓存已是当日快照）。
    """
    from datetime import datetime, timedelta
    from app.flash.rules import beijing_now
    from app.eastmoney import get_lift_stage
    today = beijing_now()
    start = (today - timedelta(days=back_days)).strftime("%Y-%m-%d")
    end = (today + timedelta(days=ahead_days)).strftime("%Y-%m-%d")
    rows = get_lift_stage(start, end)
    if not rows:
        return {"ok": False, "error": "解禁日历为空（接口异常或区间内无数据）", "saved": 0}
    _ensure_table()
    try:
        from app.tencent import _cache
        stocks = _cache.get("stocks") or {}
    except Exception:
        stocks = {}
    now = today.isoformat(timespec="seconds")
    saved, no_ratio = 0, 0
    for r in rows:
        cap_wan = float(r.get("cap_wan") or 0)
        float_cap = float((stocks.get(r["code"]) or {}).get("float_cap") or 0)
        ratio = round(cap_wan / float_cap * 100, 2) if float_cap > 0 else None
        if ratio is None:
            no_ratio += 1
        sev = "high" if (ratio is not None and ratio >= LIFT_HIGH_RATIO) else (
            "watch" if (ratio is not None and ratio >= LIFT_WATCH_RATIO) else "low")
        detail = (f"{r['free_date']} 解禁 {cap_wan / 1e4:.2f} 亿元"
                  + (f"（占流通市值 {ratio}%）" if ratio is not None else "（占比未知）")
                  + (f"· {r['kind']}" if r.get("kind") else ""))
        try:
            db.upsert("risk_events", {
                "event_date": r["free_date"], "code": r["code"], "name": r.get("name") or "",
                "event_type": "lift", "severity": sev,
                "amount_wan": str(cap_wan), "ratio": ("" if ratio is None else str(ratio)),
                "detail": detail, "created_at": now,
            }, conflict_columns=["event_date", "code", "event_type"])
            saved += 1
        except Exception as e:
            print(f"[risk_events] save failed {r['code']}: {e}")        # ASCII（铁律⑥）
    n_high = sum(1 for r in rows
                 if (r.get("cap_wan") or 0) > 0
                 and (stocks.get(r["code"]) or {}).get("float_cap")
                 and (r["cap_wan"] / float(stocks[r["code"]]["float_cap"]) * 100) >= LIFT_HIGH_RATIO)
    print(f"[risk_events] lift calendar synced {start}~{end}: {saved} rows "
          f"(high={n_high}, no_ratio={no_ratio})")                      # ASCII
    return {"ok": True, "range": [start, end], "fetched": len(rows),
            "saved": saved, "high": n_high, "no_ratio": no_ratio}


def upcoming_risks(codes: Optional[List[str]] = None,
                   days: int = LIFT_WINDOW_DAYS) -> Dict:
    """未来 `days` 个自然日内的风险事件（默认解禁）。只读、fail-open。

    codes 传入时**只返回这些代码**命中的事件（决策卡用：持仓 + 候选，避免整表塞进 payload）。
    """
    out = {"available": False, "items": [], "high": [], "watch": [], "note": None}
    try:
        from datetime import timedelta
        from app.flash.rules import beijing_now
        _ensure_table()
        today = beijing_now()
        end = (today + timedelta(days=max(1, days))).strftime("%Y-%m-%d")
        sql = ("SELECT event_date, code, name, event_type, severity, detail, ratio "
               "FROM risk_events WHERE event_type='lift' AND event_date >= %s "
               "AND event_date <= %s")
        params: list = [today.strftime("%Y-%m-%d"), end]
        if codes:
            cl = [str(c).strip() for c in codes if str(c).strip()]
            if not cl:
                out["available"] = True
                out["note"] = "今日无风险事件（未指定代码）"
                return out
            ph = ",".join(["%s"] * len(cl))
            sql += f" AND code IN ({ph})"
            params += cl
        sql += " ORDER BY event_date"
        rows = db.fetch(sql, tuple(params)) or []
        items = [{"date": str(r.get("event_date")), "code": str(r.get("code")),
                  "name": r.get("name") or "", "type": r.get("event_type"),
                  "severity": r.get("severity") or "low",
                  "detail": r.get("detail") or "",
                  "ratio": (float(r["ratio"]) if r.get("ratio") not in (None, "") else None)}
                 for r in rows]
        out.update({"available": True, "items": items,
                    "high": [x for x in items if x["severity"] == "high"],
                    "watch": [x for x in items if x["severity"] == "watch"]})
        if not items:
            out["note"] = f"未来 {days} 天内无解禁事件命中"
    except Exception as e:
        print(f"[risk_events] upcoming failed: {e}")                     # ASCII（铁律⑥）
        out["note"] = "读取失败"
    return out


# ==============================================================================
#  未做（数据源待确认，诚实标注 —— 别让读者以为"已覆盖全部风险事件"）
# ==============================================================================
#  · 减持公告 / 质押 / 监管问询 / 立案：**暂无结构化数据源**。
#    现有一半基础：`news_sentiment.NEG`（减持/质押/问询函/警示函/监管函/处罚）
#    与 `NEG_STRONG`（立案/调查/退市风险）**已能识别**，但那是**新闻维度**（滞后），
#    且 `news_history` 只落聚合分、不存"命中了哪个关键词" ⇒ 接入需先扩展打分输出。
#  · 东财 datacenter 另有减持/质押报表（RPT_SHARE_HOLDER_INCREASE 等），
#    本轮**未验证**（一天只验证一个源，避免"以为能用结果不能用"）⇒ 留待下轮。
