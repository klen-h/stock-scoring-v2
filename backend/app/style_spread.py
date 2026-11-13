# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】风格分化观察器 —— 「钱在权重还是在成长」
================================================================================
背景（2026-11-13，用户："把分化度做成观察器里的独立块"）：

  实测 17 个 `coach.gate_add` 触发日的 T+5：
      上证50 -0.83% ｜ 沪深300 -1.85% ｜ 中证1000 -2.50% ｜ 创业板 -4.36%
  ⇒ 同一个「加仓」信号，买错方向代价约 5 倍。

  按触发日「近 5 日 沪深300 − 创业板」分组（**v1 口径，2026-11-14 已换成上证50，见下**）：
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
  · ~~⚠️ `sh000016`（上证50）实测 **n=0**（日批未回填）⇒ 权重端用 `sh000300`。~~
    **↑ 该障碍已于 2026-11-14 消除**（手工补 726 条并纳入日批 `INDEX_BENCHMARKS`），
    权重端已切回**上证50** —— 详见下方「口径切换」。

★★★ 2026-11-14：权重端 `sh000300`（沪深300）→ **`sh000016`（上证50）**，阈值 3.0 → **4.0**

  【为什么换】沪深300 **混了中盘成长股**，不是"纯权重"。同批 gate_add 触发日实测
    上证50 -0.83% ｜ 沪深300 -1.85%（**差 1pp**）⇒ 用沪深300 会**系统性低估**权重抗跌，
    等于用"半个权重"去回答"该往权重还是往成长加"。

  【阈值为什么是 4.0】用 3 年日线（721 / 733 个共同交易日）实测"近 5 日分化度"分布：
    **上证50 的波动比沪深300 大 27%**（标准差 3.79 vs 2.97）—— 与直觉相反，
    纯权重与创业板相关性更低，差值反而更宽。
    各阈值下「|分化度| ≥ thr」的历史触发率：

        阈值   沪深300          上证50
        2.0    35.9%            49.7%
        2.5    27.0%            41.7%
        3.0    **21.8%（原口径）**  31.8%
        3.5    15.4%            26.2%
        4.0    12.0%            **21.8%（完全吻合）**

    ⇒ 取 **4.0** 使新口径"明显分化"占比与旧口径**完全一致**（单边也吻合：
      权重主导 10.4% vs 10.0%、成长主导 11.4% vs 11.9%），
      **语义（"约两成日子算明显分化"）不变**，只是口径更正确。
    ⚠️ 若沿用 3.0，触发率会从 21.8% 跳到 31.8% —— "明显分化"的日子凭空多 10pp。

  【效果验证（如实披露局限）】17 个 gate_add 触发日中有 14 个同时具备
    "前 5 日历史 + 后 5 日价格"：按沪深300@3.0 与上证50@4.0 分组，
    **两套口径的 9/5 分组完全相同**（强分化组创业板 T+5 -3.38% vs 弱分化 -6.13%，
    差 2.74pp）⇒ 切换**不改变现有结论**（稳健），
    **但不能据此宣称"新口径更好"** —— 本轮样本太巧、太小。
    真实价值是**语义正确**，不是效果提升。
================================================================================
"""
from typing import Dict, List, Optional

# ── 权重 / 成长代表指数（**唯一事实源**，`coach/rules.py` 从这里 import）──
WEIGHT_IDX = "sh000016"      # 上证50（纯权重；2026-11-14 由 sh000300 切换，见文件头）
GROWTH_IDX = "sz399006"      # 创业板指
WEIGHT_LABEL = "上证50"       # 展示名下发给前端；改标的时**必须同步**（否则界面说错话）

# 判定档位（pp）。与 `coach.rules._direction_hint` 的阈值保持一致。
# ⚠️ 4.0 是**按上证50 重校准**的结果（原 3.0 对应沪深300），依据见文件头。
#   换标的必须重算，**不能沿用旧值** —— 同一阈值在两个标的上的触发率差 10pp。
SPREAD_THRESHOLD = 4.0


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
           "threshold": SPREAD_THRESHOLD, "verdict": "", "series": [], "note": "",
           # ★ 2026-11-14：展示名一并下发 —— 前端此前写死"沪深300"，
           #   换标的后会"算上证50、显示沪深300"。所有展示权重端指数的地方
           #   都必须读这个字段，**不要再硬编码**。
           "weight_label": WEIGHT_LABEL}

    w = _closes(WEIGHT_IDX, n + lb + 2)
    g = _closes(GROWTH_IDX, n + lb + 2)
    if not w or not g:
        out["note"] = (f"指数日线缺失（{WEIGHT_LABEL} {len(w)} / 创业板 {len(g)} 根）"
                      "，日批可能未回填")
        return out

    # ★ 只在**两端都有的日期**上计算，且日期必须严格对齐 ——
    #   "一只更新一只滞后"会算出假分化（与 `_bank_catchdown` 同一教训）。
    dates = sorted(set(w) & set(g))
    if len(dates) < n + 1:
        out["note"] = f"共同交易日不足（{len(dates)} < {n + 1}）"
        return out
    if str(max(w)) != str(max(g)):
        out["note"] = (f"两指数最新日期不齐（{WEIGHT_LABEL} {max(w)} / 创业板 {max(g)}），"
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
        "note": (f"近 {n} 日{WEIGHT_LABEL} − 创业板；数据来自日批回填，"
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
