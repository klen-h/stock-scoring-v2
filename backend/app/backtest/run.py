"""
================================================================================
【文件作用】回测 CLI：生成 markdown 绩效报告（M3 里程碑）
================================================================================
运行方式：
  python -m app.backtest.run --strategy all        # 三类策略全部
  python -m app.backtest.run --strategy signals    # 仅 LLM 信号绩效追踪
  python -m app.backtest.run --strategy warfare    # 仅战法选股回测
  python -m app.backtest.run --strategy macro      # 仅宏观方向分回测
  python -m app.backtest.run --strategy review     # 历史评分 × 市场状态 分层复盘
  python -m app.backtest.run --strategy warfare --name 战法名   # 指定战法

输出：markdown 报告存 backend/backtest_reports/（latest.md 覆盖最新一份）。
每策略报告包含：样本说明（LLM/宏观强制标注"仅供参考"）+ 绩效表 +
逐笔交易 Top10 + 资产曲线概要。
================================================================================
"""

import argparse
import os
import time

from app.backtest import strategies

REPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backtest_reports")


# ──────────────────────────────────────────────────────────────
#  markdown 渲染工具
# ──────────────────────────────────────────────────────────────

def _metrics_table(m: dict) -> str:
    """绩效指标 → markdown 表格（两列：指标 | 值）。"""
    if not m:
        return "（无成交，无绩效指标）\n"
    def _fmt(v, suffix=""):
        return f"{v}{suffix}" if v is not None else "-"
    rows = [
        ("交易次数", _fmt(m.get("trade_count"))),
        ("胜率", _fmt(m.get("win_rate"), "%")),
        ("盈亏比", _fmt(m.get("profit_factor"))),
        ("平均单笔收益", _fmt(m.get("avg_pnl_pct"), "%")),
        ("平均持仓天数", _fmt(m.get("avg_hold_days"))),
        ("总收益率", _fmt(m.get("total_return"), "%")),
        ("年化收益", _fmt(m.get("annual_return"), "%")),
        ("最大回撤", _fmt(m.get("max_drawdown"), "%")),
        ("夏普比率", _fmt(m.get("sharpe"))),
        ("基准收益(沪深300)", _fmt(m.get("benchmark_return"), "%")),
        ("超额收益", _fmt(m.get("excess_return"), "%")),
        ("样本天数", _fmt(m.get("period_days"))),
    ]
    reasons = m.get("exit_reasons") or {}
    if reasons:
        rows.append(("退出原因", " / ".join(f"{k}:{v}" for k, v in reasons.items())))
    lines = ["| 指标 | 数值 |", "|---|---|"]
    for k, v in rows:
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines) + "\n"


def _trades_top(trades: list, n: int = 10) -> str:
    """逐笔交易 Top N（按单笔收益绝对值排序）→ markdown 表格。"""
    if not trades:
        return "（无成交）\n"
    top = sorted(trades, key=lambda t: abs(t["pnl_pct"]), reverse=True)[:n]
    lines = ["| 代码 | 方向 | 入场日 | 出场日 | 持仓天 | 单笔收益% | 出场原因 |",
             "|---|---|---|---|---|---|---|"]
    for t in top:
        direction = "多" if t["direction"] > 0 else "空"
        lines.append(f"| {t['code']} | {direction} | {t['entry_date']} | "
                     f"{t['exit_date']} | {t['hold_days']} | {t['pnl_pct']} | "
                     f"{t['exit_reason']} |")
    return "\n".join(lines) + "\n"


def _curve_summary(curve: list) -> str:
    """资产曲线概要（不输出全曲线）：区间、起止净值、回撤最深点、月频抽样。"""
    if not curve:
        return "（无净值曲线）\n"
    first, last = curve[0], curve[-1]
    peak = max(curve, key=lambda c: c["nav"])
    trough_ratio = min(c["nav"] / p for c, p in zip(curve, [curve[0]] + curve[:-1])) if len(curve) > 1 else 1.0
    # 找最大回撤段（峰值日 → 之后最低点日）
    mdd_pair, peak_nav = None, curve[0]
    for c in curve:
        if c["nav"] > peak_nav["nav"]:
            peak_nav = c
        ratio = c["nav"] / peak_nav["nav"]
        if mdd_pair is None or ratio < mdd_pair[0]:
            mdd_pair = (ratio, peak_nav["date"], c["date"])
    lines = [
        f"- 区间：{first['date']} ~ {last['date']}（{len(curve)} 个交易日）",
        f"- 净值：{first['nav']:.4f} → {last['nav']:.4f}（累计 {last['nav'] - 1:+.2%}）",
        f"- 峰值：{peak['date']} {peak['nav']:.4f}",
        f"- 最大回撤段：{mdd_pair[1]} → {mdd_pair[2]}（{mdd_pair[0] - 1:.2%}）",
    ]
    recent = curve[-20:]
    if recent:
        recent_str = " → ".join(f"{c['nav']:.4f}" for c in recent)
        lines.append(f"- 最近 20 日净值：{recent_str}")
    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────────────────────
#  各策略报告段
# ──────────────────────────────────────────────────────────────

def _render_signals() -> str:
    r = strategies.backtest_llm_signals()
    s = [f"## {r['label']}", "", f"> ⚠️ {r['sample_note']}", ""]
    total = r["total"]
    s += ["### 总体", "", _metrics_table({
        "trade_count": total.get("closed"),
        "win_rate": total.get("win_rate"),
        "profit_factor": total.get("profit_factor"),
        "avg_pnl_pct": total.get("avg_profit_pct"),
        "avg_hold_days": None,
        "total_return": None, "annual_return": None, "max_drawdown": None,
        "sharpe": None, "benchmark_return": None, "excess_return": None,
        "period_days": None, "exit_reasons": None,
    })]
    s += ["### 按来源分组", "",
          "| 来源 | 平仓笔数 | 胜率% | 平均单笔% | 盈亏比 |", "|---|---|---|---|---|"]
    for key, g in r["by_source"].items():
        s.append(f"| {g['name']} | {g['closed']} | {g['win_rate']} | "
                 f"{g['avg_profit_pct']} | {g['profit_factor']} |")
    s += ["", f"累计提出信号：{total.get('proposed')} 条", ""]
    return "\n".join(s)


def _render_warfare() -> str:
    r = strategies.backtest_warfare()
    s = [f"## {r['label']}（战法选股回测）", "", f"> 样本期：{r['sample_note']}", ""]
    if r.get("metrics"):
        s += ["### 整体绩效", "", _metrics_table(r["metrics"])]
        s += ["### 逐笔交易 Top10", "", _trades_top(r["trades"])]
        s += ["### 资产曲线概要", "", _curve_summary(r.get("curve") or [])]
    for key, label in (("in_sample", "前 70% 样本"), ("out_sample", "后 30% 样本")):
        part = r.get(key)
        if part and part.get("metrics"):
            s += [f"### {label}（防过拟合切分）", "", _metrics_table(part["metrics"])]
    return "\n".join(s)


def _render_macro() -> str:
    r = strategies.backtest_macro()
    s = [f"## {r['label']}", "", f"> ⚠️ {r['sample_note']}", ""]
    if r.get("metrics"):
        s += ["### 绩效指标", "", _metrics_table(r["metrics"])]
        s += ["### 策略说明", "",
              "- 方向分 score>0 全仓沪深300ETF（sh510300），score≤0 空仓",
              "- 信号日 T+1 生效；空仓现金收益记 0", ""]
    return "\n".join(s)


# ──────────────────────────────────────────────────────────────
#  主入口
# ──────────────────────────────────────────────────────────────

def generate_report(strategy: str) -> str:
    """生成报告 markdown，返回报告内容。"""
    title = f"# 回测报告（{time.strftime('%Y-%m-%d %H:%M:%S')}）\n\n"
    sections = []
    if strategy in ("all", "signals"):
        sections.append(_render_signals())
    if strategy in ("all", "warfare"):
        sections.append(_render_warfare())
    if strategy in ("all", "macro"):
        sections.append(_render_macro())
    if strategy in ("all", "review"):
        from app.backtest.regime_review import run_review, render_review_markdown
        sections.append(render_review_markdown(run_review()))
    body = title + "\n---\n\n".join(sections)
    if strategy != "all":
        body += f"\n> 本次仅生成 {strategy} 报告，完整报告请运行 --strategy all\n"
    return body


def save_report(content: str, tag: str = "") -> str:
    """写 markdown 到 backtest_reports/，返回文件路径。"""
    os.makedirs(REPORT_DIR, exist_ok=True)
    suffix = f"_{tag}" if tag else ""
    fname = f"backtest_report{suffix}_{time.strftime('%Y%m%d_%H%M%S')}.md"
    path = os.path.join(REPORT_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    with open(os.path.join(REPORT_DIR, "latest.md"), "w", encoding="utf-8") as f:
        f.write(content)
    return path


def generate_summary() -> str:
    """
    精简版回测摘要（企微周报推送用）：各策略核心指标 + 分层复盘结论 + 当前市场状态。
    不写文件；完整报告仍走 generate_report + save_report。
    """
    lines = ["## 自动回测报告（精简版）", "", f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')}", ""]

    # LLM 信号追踪
    try:
        r = strategies.backtest_llm_signals()
        lines += ["### 📡 LLM 信号追踪", f"> ⚠️ {r['sample_note']}", "",
                  "| 来源 | 平仓 | 胜率% | 平均单笔% | 盈亏比 |", "|---|---|---|---|---|"]
        for key, g in r["by_source"].items():
            lines.append(f"| {g['name']} | {g['closed']} | {g['win_rate']} | "
                         f"{g['avg_profit_pct']} | {g['profit_factor']} |")
    except Exception as e:
        lines.append(f"> LLM 信号回测异常：{e}")

    # 战法选股回测
    try:
        r = strategies.backtest_warfare()
        lines += ["", "### ⚔️ 战法选股回测", f"> 样本期：{r['sample_note']}", ""]
        lines.append(_metrics_table(r.get("metrics") or {}))
        oos = r.get("out_sample") or {}
        om = oos.get("metrics") or {}
        if om:
            lines.append(f"> 样本外（后30%）：胜率 {om.get('win_rate')}%，"
                         f"平均单笔 {om.get('avg_pnl_pct')}%，盈亏比 {om.get('profit_factor')}")
    except Exception as e:
        lines.append(f"> 战法回测异常：{e}")

    # 宏观方向分回测
    try:
        r = strategies.backtest_macro()
        lines += ["", "### 🌐 宏观方向分回测", f"> ⚠️ {r['sample_note']}", ""]
        lines.append(_metrics_table(r.get("metrics") or {}))
    except Exception as e:
        lines.append(f"> 宏观回测异常：{e}")

    # 分层复盘（评分 × 市场状态）
    try:
        from app.backtest.regime_review import run_review, render_review_markdown
        lines += ["", "### 📊 分层复盘"]
        lines.append(render_review_markdown(run_review()))
    except Exception as e:
        lines.append(f"> 分层复盘异常：{e}")

    # 当前市场状态（生产评分动态权重依据）
    try:
        from app.backtest.market_regime import get_regime_cache, get_regime_description
        cache = get_regime_cache()
        if cache and cache.get("state"):
            w = cache["weights"]
            d = cache.get("detail") or {}
            lines += ["", "### 🎯 当前市场状态",
                      f"- {get_regime_description(cache['state'])}（{cache['date']}）",
                      f"- ADX {d.get('adx')} / 均线 {d.get('ma_trend')} / 波动 {d.get('volatility_regime')}",
                      f"- 评分权重：技术 {w['technical']:.0%} / 资金 {w['capital']:.0%} / "
                      f"基本面 {w['fundamental']:.0%}"]
        else:
            lines += ["", "### 🎯 当前市场状态",
                      "> 数据未就绪（未到盘后窗口或无沪深300数据），评分使用默认权重"]
    except Exception as e:
        lines.append(f"> 市场状态读取异常：{e}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="回测报告生成")
    parser.add_argument("--strategy", default="all",
                        choices=["all", "signals", "warfare", "macro", "review"])
    parser.add_argument("--name", default=None, help="指定战法名（配合 --strategy warfare）")
    args = parser.parse_args()

    if args.strategy == "warfare" and args.name:
        # 指定战法：覆盖 strategies 默认入口，生成单战法报告
        import json
        from app.backtest import data, engine
        signals = strategies._warfare_signal_stream(args.name)
        r = strategies._run_warfare(signals, args.name)
        # 复用渲染逻辑：直接构造
        content = (f"# 回测报告：{args.name}（{time.strftime('%Y-%m-%d %H:%M:%S')}）\n\n"
                   f"## {r['label']}（战法选股回测）\n\n> 样本期：{r['sample_note']}\n\n")
        if r.get("metrics"):
            content += "### 绩效指标\n\n" + _metrics_table(r["metrics"])
            content += "### 逐笔交易 Top10\n\n" + _trades_top(r["trades"])
        path = save_report(content, tag=args.name.replace("/", "_"))
        print(f"[OK] 报告已生成: {path}")
        return

    content = generate_report(args.strategy)
    path = save_report(content)
    print(f"[OK] 报告已生成: {path}")
    print(f"[OK] 覆盖 latest.md")


if __name__ == "__main__":
    main()
