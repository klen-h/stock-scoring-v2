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


# ── ★ 2026-09-19：「LLM provider 链」自检 ────────────────────────────────────
# 为什么单列一项：本类问题**全部是静默的** —— `_providers()` 少一个槽不会报错，
# 链路悄悄退化成"只剩 main"甚至"空链"，直到某处调用失败才暴露；而且错误信息
# 还会把方向带偏（链尾欠费的 SiliconFlow 报 402，**掩盖**链首 free 的真实原因 429）。
# 2026-09-19 一天内踩了四种，**没有一种是代码报错报出来的**：
#   ① 控制台 `LLM_FREE_SHADOW=1`（名字像"启用免费站"，实际是**把它排除出正式链**）
#   ② 服务是控制台手工建的 ⇒ **不应用 render.yaml 的 value** ⇒ 缺
#      `LLM_FREE_BASE_URL` / `LLM_FREE_MODEL`
#   ③ 清空 `LLM_MODEL` 后 main 槽消失
#   ④ 链尾挂着已欠费的 SiliconFlow
# ⇒ 把链打在**启动第一屏**，"用的哪套配置"就不再需要靠反推猜。
# 例外：只回填资金流、根本不碰 LLM 的任务（如 `backend-pack.yml` 的 mainflow
#   步骤）环境里本就没有 LLM 变量 ⇒ 用 `ENV_CHECK_SKIP_LLM=1` 抑制，
#   **避免告警脱敏**（天天刷误报，真出问题时反而没人看）。
def llm_chain_report() -> Dict:
    """LLM provider 链自检。返回 {chain, ok, warn, skipped}。"""
    if (os.environ.get("ENV_CHECK_SKIP_LLM") or "").strip() == "1":
        return {"chain": [], "ok": True, "warn": [], "skipped": True}
    try:
        from app.flash import llm as L          # 延迟 import（同 _actual 风格）
        provs = L._providers() or []
    except Exception as e:      # 读失败不阻断（自检自身故障绝不能影响服务启动）
        return {"chain": [], "ok": False,
                "warn": [f"provider 链读取失败：{type(e).__name__}: {e}"],
                "skipped": False}

    chain = [f"{p['name']}:{p['models'][0]}" for p in provs if p.get("models")]
    names = [p["name"] for p in provs]
    warn: List[str] = []
    if "free" not in names:
        if bool(getattr(L, "LLM_FREE_SHADOW", False)):
            warn.append("免费站未进链：LLM_FREE_SHADOW=1（**影子模式 = 排除出正式链**，"
                        "名字像'启用'极易设错 ⇒ 要它进链请设 0）")
        missing = [n for n in ("LLM_FREE_BASE_URL", "LLM_FREE_API_KEY", "LLM_FREE_MODEL")
                   if not getattr(L, n, "")]
        if missing:
            warn.append("免费站未进链：缺 " + " / ".join(missing)
                        + "（注意：**手工建的服务不会应用 render.yaml 里的 value**，"
                          "必须去 Render 控制台补）")
    # ★ 主力站缺配**不单独告警**：本地 `.env` 就是故意只留免费站（"本地全用日日新"），
    #   单独告警会变成**每次运行的永久误报** ⇒ 告警脱敏，真出问题时反而没人看。
    #   它只在「空链」里作为归因细节出现（链本身也已在下面那行打印可见）。
    if not chain:
        detail = []
        if "free" not in names:
            miss = [n for n in ("LLM_FREE_BASE_URL", "LLM_FREE_API_KEY", "LLM_FREE_MODEL")
                    if not getattr(L, n, "")]
            detail.append("free 槽缺 " + (" / ".join(miss) if miss
                                          else "（已配齐但被 SHADOW 排除）"))
        if "main" not in names:
            miss = [n for n in ("LLM_API_KEY", "LLM_MODEL") if not getattr(L, n, "")]
            detail.append("main 槽缺 " + " / ".join(miss))
        warn.append("★ 空链：所有 LLM 功能都会降级（前端显示「未配置任何 LLM provider」）"
                    + ("；" + "；".join(detail) if detail else ""))
    elif names[0] != "free":
        warn.append("链首不是免费站 —— 若本意是「免费站优先、主力站兜底」，"
                    "请核对 LLM_FREE_* 三件套与 LLM_FREE_SHADOW")
    return {"chain": chain, "ok": not warn, "warn": warn, "skipped": False}


def log_switch_report() -> Dict:
    """启动期调用：打印生效值表 + LLM provider 链；不符/退化即显著告警。

    返回 switch_report()（额外带 `llm_chain`）。
    """
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

    # ★ 与上面性质不同：上面是「期望值 vs 生效值」，这条是「**事实**」——
    #   链退化了没有，代码本身不会报错，不主动打就永远看不见。
    c = llm_chain_report()
    print(f"[env_check] LLM provider 链 = "
          f"{' → '.join(c['chain']) if c['chain'] else '（空）'}"
          f"{'（已跳过自检 ENV_CHECK_SKIP_LLM=1）' if c.get('skipped') else ''}")
    for w in c["warn"]:
        print(f"[env_check] ⚠️ {w}")
    r["llm_chain"] = c
    return r
