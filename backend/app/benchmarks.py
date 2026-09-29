# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】**基准收益的唯一事实源**（2026-09-29，P0 双口径系列的**口径收敛**）
================================================================================
【为什么需要（本模块的由来）】
  2026-09-29 做「P0 基准双口径」时，同一个公式（**首末收盘算收益**）被写在 **4 处**：
    · `daily_report._bench5`            —— 日报「空仓对照」近 5 交易日
    · `weekly_review._idx_ret`          —— 周复盘「本周绩效」块
    · `routers.performance._bench_pair` —— 性能页三轨道
    · `coach.attribution._bench_ret`    —— 教练建议事后归因
  这是**当天新造的债**，且违反项目「单一事实源」纪律（前车之鉴：本地 68.8 vs
  后端 72.6 的评分漂移）⇒ 收进本模块，四处共用；**改本文件 = 全项目同步改口径**。

【口径（★ 全项目统一）】
  · 基准收益 = `backtest_prices` **首末收盘**比 − 1，**同起止日**（指数基准用收盘口径；
    个股侧的"T+1 开盘"是可交易口径，两者差异由调用方在文案里说明，勿混为一谈）；
  · 一律 `round(..., 2)` —— 全项目基准统一 2 位小数，避免同一数字两种精度；
  · **中证1000 `sh000852` 为主基准**（持仓/信号偏 20–50 亿中小盘），
    沪深300 `sh000300` 为对照 / **退路**（缺口 1 结论）；
  · 缺失（行数 < 2 / 收盘 ≤ 0）返回 **None —— 缺失 ≠ 0**（全项目纪律，前端显示"—"）。

【两种数据形态都收在这里（避免各自实现）】
  ① **按需查询**：`index_ret` / `dual_bench` / `recent_ret` / `recent_dual`
     （自己查库，适合单点调用）；
  ② **已加载**：`ret_in_window(closes_by_date, d0, d1)` —— 调用方已批量查过 K 线时用
     （**零额外 egress**，`coach/attribution.py` 用它）。

⚠️ 回填依赖：中证1000/中证500 由 `backfill_history.backfill_daily` 的**指数段**写入
  （`INDEX_BENCHMARKS`，2026-09-29 起）⇒ 回填前相关字段为 None 属**预期**，不是故障。

【边界：两处"公式相同但刻意不复用"的实现（别当遗漏去统一）】
  2026-09-29 收敛时已实测核对，仅剩本模块一处**基准**取数；另有两条**同公式不同用途**：
  ① `coach/rules.py`：「指数近 20 交易日涨跌幅」= **决策链输入**（喂规则判断），
     公式与 `recent_ret(code, 20)` 相同 —— **刻意不复用**：把展示口径与决策口径耦合，
     会让日后改展示顺手改了闸门（"第五套口径"的前车之鉴）。
  ② `backtest/strategies.py`：`bars[-1].close / bars[0].open - 1` 是**开→收**口径
     （回测撮合语义），与本模块的**收→收**口径**本来就不是一个东西**。
  ⇒ 判断标准：**"是不是基准"** 比 "公式像不像" 更重要。
================================================================================
"""

from typing import Dict, Optional, Tuple

from app.database import db

# ── 基准定义（展示顺序依赖此元组顺序；键名是 API 字段名，勿轻易改）──────────
HS300 = "sh000300"
ZZ1000 = "sh000852"
ZZ500 = "sh000905"

# (API 键, 代码, 中文名)
DUAL_BENCHES: Tuple[Tuple[str, str, str], ...] = (
    ("hs300", HS300, "沪深300"),      # 对照（大盘股）
    ("zz1000", ZZ1000, "中证1000"),   # 主基准（中小盘）
)
BENCH_CN = {code: label for _k, code, label in DUAL_BENCHES}
BENCH_CN[ZZ500] = "中证500"


# ══════════════════════════════════════════════════════════════════════════
#  ① 按需查询
# ══════════════════════════════════════════════════════════════════════════

def index_ret(code: str, d0: str, d1: Optional[str] = None) -> Optional[float]:
    """指数在 `[d0, d1]`（含两端）的**首末收盘**收益 %；`d1=None` ⇒ 一直取到最新一根。"""
    try:
        sql = ("SELECT close FROM backtest_prices WHERE code=%s AND date >= %s"
               + (" AND date <= %s" if d1 else "")
               + " ORDER BY date ASC")
        rows = db.fetch(sql, (code, d0, d1) if d1 else (code, d0))
        closes = [float(r["close"]) for r in rows or [] if r.get("close")]
        if len(closes) >= 2 and closes[0] > 0:
            return round((closes[-1] / closes[0] - 1) * 100, 2)
    except Exception:
        pass
    return None


def recent_ret(code: str, days: int = 5) -> Optional[float]:
    """近 `days` 个交易日的收益 %（**倒序 LIMIT** 取 `days+1` 根收盘的首末比）。

    ⚠️ 语义是"**N 个交易日的**变化"（N 个间隔）⇒ 用 N+1 根 K 线；
      与原 `daily_report._bench5` 完全同实现（保留其倒序取数不然会与既有数字不一致）。
    """
    try:
        rows = db.fetch("SELECT close FROM backtest_prices WHERE code=%s "
                        "ORDER BY date DESC LIMIT %s", (code, int(days) + 1))
        closes = [float(r["close"]) for r in rows or [] if r.get("close")]
        if len(closes) >= 2 and closes[-1] > 0:
            return round((closes[0] / closes[-1] - 1) * 100, 2)
    except Exception:
        pass
    return None


def dual_bench(d0: str, d1: Optional[str] = None) -> Dict[str, Optional[float]]:
    """**双基准**在 `[d0, d1]` 的收益（键见 `DUAL_BENCHES`：`hs300` / `zz1000`）。

    为什么两个都要（缺口 1）：只对沪深300 会「**跑赢大盘仍绝对亏损**」，
    且超额被**风格暴露污染**（持仓/信号偏中小盘）。
    """
    return {key: index_ret(code, d0, d1) for key, code, _cn in DUAL_BENCHES}


def recent_dual(days: int = 5) -> Dict[str, Optional[float]]:
    """双基准**近 days 个交易日**收益（日报「空仓对照」用）。"""
    return {key: recent_ret(code, days) for key, code, _cn in DUAL_BENCHES}


# ══════════════════════════════════════════════════════════════════════════
#  ② 已加载数据（零额外查询）
# ══════════════════════════════════════════════════════════════════════════

def ret_in_window(closes: Dict[str, float], d0: str, d1: str) -> Optional[float]:
    """从**已加载**的 `{date: close}` 取 `[d0, d1]` 首末收益 %（缺任一端 ⇒ None）。

    供"已批量查过 K 线"的调用方（如 `coach/attribution.py` 用它对齐个股窗口）。
    """
    a, b = closes.get(str(d0)[:10]), closes.get(str(d1)[:10])
    if not a or not b or a <= 0:
        return None
    return round((b / a - 1) * 100, 2)


# ══════════════════════════════════════════════════════════════════════════
#  ③ 选基准（只能出一条线的场景）
# ══════════════════════════════════════════════════════════════════════════

def pick_bench() -> Tuple[str, str, bool]:
    """选**个股侧**基准：优先中证1000，无数据退沪深300。

    返回 `(code, 中文名, 是否退路)` —— 第三项**必须随展示一起披露**
    （退路态意味着"超额"的含义变了，不能静默）。
    """
    for _key, code, label in reversed(DUAL_BENCHES):      # 先 zz1000，再 hs300
        try:
            n = (db.fetch_one("SELECT COUNT(*) AS n FROM backtest_prices WHERE code=%s",
                              (code,)) or {}).get("n") or 0
        except Exception:
            n = 0
        if n:
            return code, label, code != ZZ1000
    return HS300, BENCH_CN[HS300], True
