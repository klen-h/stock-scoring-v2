# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】风格分化观察器 —— 「钱在权重还是在成长」
================================================================================
背景（2026-11-13，用户："把分化度做成观察器里的独立块"）：

  实测 17 个 `coach.gate_add` 触发日的 T+5：
      上证50 -0.83% ｜ 沪深300 -1.85% ｜ 中证1000 -2.50% ｜ 创业板 -4.36%
  ⇒ 同一个「加仓」信号，买错方向代价约 5 倍。

  按触发日「近 5 日 沪深300 − 创业板」分组：
      分化度 ≥ +3pp → 创业板 T+5 -2.46%（胜率 33%，n=6）
      分化度 <  +3pp → 创业板 T+5 -5.80%（胜率  0%，n=8）
  ⇒ 分化度是**有区分力的前置变量**，不该只在信号触发时才被动计算。

★★ 定位：**纯观察器**
  · 不改分数、不进闸门、不产生买卖信号（同 `policy_bottom_watch` 纪律）；
  · 教练的 `gate_add_weak_direction` 复用本模块的 `latest()`，
    **指数代码与算法只有这一处**（避免两处各写一份、日后漂移）。

★★ 数据与口径
  · 来源 `backtest_prices`（日批回填）⇒ **零外部请求**；
  · ⚠️ 当日 15:40 后才更新 ⇒ 盘中读到的是**昨日收盘**算出的分化度。
    这是日频指标，盘中不变；但输出里必须带 `as_of` 让用户知道是哪天。
  · ⚠️ `sh000016`（上证50）实测 **n=0**（日批未回填）⇒ 权重端用 `sh000300`。
================================================================================
"""
from typing import Dict, List, Optional

# ── 权重 / 成长代表指数（**唯一事实源**，`coach/rules.py` 从这里 import）──
WEIGHT_IDX = "sh000300"      # 沪深300
GROWTH_IDX = "sz399006"      # 创业板指

# 判定档位（pp）。与 `coach.rules._direction_hint` 的阈值保持一致。
SPREAD_THRESHOLD = 3.0


def _closes(code: str, limit: int = 200) -> Dict[str, float]:
    """取单个指数的收盘价序列 {date: close}（升序由调用方处理）。"""
    try:
        from app.database import db
        rows = db.fetch(
            "SELECT date, close FROM backtest_prices WHERE code=%s "
            "ORDER BY date DESC LIMIT %s", (code, max(2, int(limit)))) or []
    except Exception as e:
        print(f"[style_spread] load {code} failed: {str(e)[:70]}")   # ASCII（铁律⑥）
        return {}
    out = {}
    for r in rows:
        try:
            c = float(r["close"] or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out[str(r["date"])[:10]] = c
    return out


def _verdict(spread_pp: float, thr: float = SPREAD_THRESHOLD) -> str:
    """一句话定性。三档，与 `_direction_hint` 完全一致（中间档不能说成有方向）。"""
    if spread_pp >= thr:
        return "权重主导（资金抱权重，成长失血）"
    if spread_pp <= -thr:
        return "成长主导（资金偏好高弹性，权重滞涨）"
    return "无明显分化（权重与成长同向，换方向避险没有意义）"


def build(days: int = 5, lookback: int = 20) -> dict:
    """风格分化观察器数据。

    参数
      days     : 计算分化度的回看窗口（默认 5 个交易日）
      lookback : 返回多少天的**滚动**分化度序列（默认 20）

    返回
      {
        available: bool,
        spread_pp / weight_ret_pct / growth_ret_pct,   # 当前窗口
        days / as_of / verdict / threshold,
        series: [{date, spread_pp, weight_pct, growth_pct}],   # 升序，末尾=最新
        note: str,
      }

    ⚠️ 任一端数据不足 ⇒ `available=False` + `note` 说明原因，**不返回 0 冒充**。
    """
    # ⚠️ clamp 到 2~60：API 层有 `le=20`，但**内部调用（教练/脚本）会绕过校验**。
    #   `days=500` 在技术上"能算"（历史够长），但"500 日相对强弱"没有可用语义，
    #   而且会让 `as_of` 严重滞后于当下。宁可在入口夹住，不靠调用方自觉。
    n = max(2, min(int(days), 60))
    lb = max(1, min(int(lookback), 120))
    out = {"available": False, "spread_pp": None, "weight_ret_pct": None,
           "growth_ret_pct": None, "days": n, "as_of": None,
           "threshold": SPREAD_THRESHOLD, "verdict": "", "series": [], "note": ""}

    w = _closes(WEIGHT_IDX, n + lb + 2)
    g = _closes(GROWTH_IDX, n + lb + 2)
    if not w or not g:
        out["note"] = (f"指数日线缺失（沪深300 {len(w)} / 创业板 {len(g)} 根）"
                       "，日批可能未回填")
        return out

    # ★ 只在**两端都有的日期**上计算，且日期必须严格对齐 ——
    #   "一只更新一只滞后"会算出假分化（与 `_bank_catchdown` 同一教训）。
    dates = sorted(set(w) & set(g))
    if len(dates) < n + 1:
        out["note"] = f"共同交易日不足（{len(dates)} < {n + 1}）"
        return out
    if str(max(w)) != str(max(g)):
        out["note"] = (f"两指数最新日期不齐（沪深300 {max(w)} / 创业板 {max(g)}），"
                       "疑似一侧滞后，暂不出数")
        return out

    idx = {d: i for i, d in enumerate(dates)}
    series: List[dict] = []
    for d in dates[-lb:]:
        i = idx[d]
        if i - n < 0:
            continue
        w_ret = (w[d] / w[dates[i - n]] - 1) * 100
        g_ret = (g[d] / g[dates[i - n]] - 1) * 100
        series.append({
            "date": d,
            "spread_pp": round(w_ret - g_ret, 2),
            "weight_pct": round(w_ret, 2),
            "growth_pct": round(g_ret, 2),
        })
    if not series:
        out["note"] = "序列为空（历史长度不足）"
        return out

    cur = series[-1]
    out.update({
        "available": True,
        "spread_pp": cur["spread_pp"],
        "weight_ret_pct": cur["weight_pct"],
        "growth_ret_pct": cur["growth_pct"],
        "as_of": cur["date"],
        "verdict": _verdict(cur["spread_pp"]),
        "series": series,
        "note": (f"近 {n} 日沪深300 − 创业板；数据来自日批回填，"
                 f"当日 15:40 后才含当日收盘"),
    })
    return out


def latest(days: int = 5) -> Optional[dict]:
    """当前分化度（教练规则用）。取不到返回 `None`。

    ⚠️ 只返回"当前窗口"那几个字段，不带序列 —— 规则里 30s 轮询，
      带上 20 天序列纯属浪费（调用方按日期自行缓存）。
    """
    d = build(days=days, lookback=1)
    if not d.get("available"):
        return None
    return {
        "spread_pp": d["spread_pp"],
        "weight_ret_pct": d["weight_ret_pct"],
        "growth_ret_pct": d["growth_ret_pct"],
        "days": d["days"],
        "as_of": d["as_of"],
    }
