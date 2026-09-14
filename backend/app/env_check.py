# -*- coding: utf-8 -*-
"""
关键业务开关「生效值 vs 部署意图」自检（审查文档 §5.5，2026-09-14）。

为什么需要：**代码默认值兜底 ≠ 部署意图**。09-14 实测 GitHub Actions 的
`daily-batch.yml` 未配 `WHITELIST_CRITERION` → 日批用代码默认 `win_rate`
（该体系胜率天花板≈50% < 55% 门槛 → 白名单恒空 → 战法推送静默），而
Render 配了 `expectancy` → 两端行为分叉，此前**无任何可见性**。

做法：把「部署意图」固化为期望值表，运行时读取**实际生效值**逐一对比，
不符即在启动日志显著告警，并通过 `/api/health` 暴露（运维/监控可查）。
期望值可用 `EXPECT_<NAME>` 环境变量覆盖（应急/灰度，避免改代码）。

新增开关时：往 EXPECTED_SWITCHES + _actual 各加一项即可。
"""
import os
from typing import Dict, List, Optional


def _on_off(v: bool) -> str:
    return "on" if v else "off"


# name -> (期望值, 说明)。期望值 = 「部署意图」，与代码默认值未必相同
# （如 WHITELIST_CRITERION 代码默认 win_rate，但部署意图是 expectancy）。
EXPECTED_SWITCHES: Dict[str, tuple] = {
    "WHITELIST_CRITERION": (
        "expectancy",
        "战法推送白名单判据（win_rate 因体系胜率天花板≈50% < 55% → 恒空集 → 推送静默）"),
    "CONTRADICTION_RISK_HOOK": (
        "1",
        "宽度崩塌风控钩子（severe 次日新仓风险额减半 + 趋势类暂停推送/入池）"),
    "MAINFORCE_MODE": (
        "auto",
        "主力出货乘数闸门（P1-12 方案A 后仅作标签，不再影响排序/分数）"),
    "STRATEGY_GRIND_GATE": ("on", "阴跌闸门（neutral/nb + MA 向下 → 入场层全禁）"),
    "STRATEGY_WIDE_SCAN": ("on", "扫描层放宽（阴跌段照常扫描落库攒样本）"),
    "STRATEGY_MAINFORCE_GATE": ("on", "战法信号主力过滤闸门（推送/入池）"),
    "TRADE_GATE": ("on", "模拟盘买入闸门"),
    "WARFARE_EXIT_POLICY": ("v2", "战法退出策略版本（v1/v2 持有期与止损口径不同）"),
    "MIDDAY_REGIME_GATE": ("1", "午盘 LLM 信号仅 offensive 市放行"),
}


def _actual(name: str) -> Optional[str]:
    """读取开关的**实际生效值**（import 消费方模块常量，与其解析逻辑同源）。"""
    try:
        if name == "WHITELIST_CRITERION":
            from app.strategies.recommendation import WHITELIST_CRITERION
            return WHITELIST_CRITERION
        if name == "CONTRADICTION_RISK_HOOK":
            from app.contradictions.risk_hook import _hook_enabled
            return "1" if _hook_enabled() else "0"
        if name == "MAINFORCE_MODE":
            from app.mainforce.overlay import _mode
            return _mode()
        if name == "STRATEGY_GRIND_GATE":
            from app.strategies.market_regime import STRATEGY_GRIND_GATE
            return _on_off(STRATEGY_GRIND_GATE)
        if name == "STRATEGY_WIDE_SCAN":
            from app.strategies.market_regime import STRATEGY_WIDE_SCAN
            return _on_off(STRATEGY_WIDE_SCAN)
        if name == "STRATEGY_MAINFORCE_GATE":
            from app.mainforce.gate import _mode_on as _gate_on
            return _on_off(_gate_on())
        if name == "TRADE_GATE":
            from app.mainforce.trade_gate import _mode_on as _trade_gate_on
            return _on_off(_trade_gate_on())
        if name == "WARFARE_EXIT_POLICY":
            from app.backtest.strategies import exit_policy
            return exit_policy()
        if name == "MIDDAY_REGIME_GATE":
            from app.signals.tracker import MIDDAY_REGIME_GATE_ENABLED
            return "1" if MIDDAY_REGIME_GATE_ENABLED else "0"
    except Exception as e:      # 读失败不阻断（自检自身故障绝不能影响服务启动）
        return f"<err:{type(e).__name__}>"
    return None


def switch_report() -> Dict:
    """对比生效值与期望值。返回 {ok, items[], mismatches[]}。"""
    items: List[Dict] = []
    for name, (expected, desc) in EXPECTED_SWITCHES.items():
        exp = (os.environ.get(f"EXPECT_{name}") or expected).strip()
        act = _actual(name)
        items.append({
            "name": name, "expected": exp, "actual": act,
            "ok": bool(act is not None and act.strip().lower() == exp.lower()),
            "desc": desc,
        })
    mismatches = [i["name"] for i in items if not i["ok"]]
    return {"ok": not mismatches, "items": items, "mismatches": mismatches}


def log_switch_report() -> Dict:
    """启动期调用：打印生效值表，不符即显著告警。返回 switch_report()。"""
    r = switch_report()
    print("[env_check] 关键开关生效值自检：")
    for i in r["items"]:
        print(f"  [{'OK' if i['ok'] else '!!'}] {i['name']} = {i['actual']}"
              f"（期望 {i['expected']}）")
    if r["mismatches"]:
        print(f"[env_check] ⚠️ 生效值与部署意图不符: {r['mismatches']} —— "
              f"检查部署环境变量（Render 控制台 / GitHub Actions workflow env）")
    else:
        print("[env_check] 全部一致 ✓")
    return r
