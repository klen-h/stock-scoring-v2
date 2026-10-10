"""
================================================================================
【文件作用】预登记验证脚本的**月度自动复核**（2026-09-30，P1 收尾）
================================================================================
【为什么需要（本轮查出来的真缺口）】
  项目有若干**预登记**验证脚本，判定标准都写死在各自脚本头：
    · `scripts/gate_ready_backtest.py`      —— 闸门 `ready==3`（**唯一买入入口**）联合期望值
    · `scripts/sector_momentum_edge_check.py` —— 板块动量延续性（因子层）
    · `scripts/event_live_review.py`        —— E2 实盘样本复核（能否先行解除 defensive）
    · `scripts/reversal_composite_edge_check.py` —— 反弹 vs 反转组合（★ 2026-10-10 P1-1 新增）
  2026-09-30 全仓 grep 发现：**这些脚本当时没有任何调用方** ⇒ 只能人工想起来跑。
  而它们的结论**完全取决于样本**（"样本是时钟，开发加速不了"）——
  **样本够了却没人跑 = 白等**。本模块把它们接进**月度日批**，到点自动出结论。
  样板：`task_subfactor_ic`（每月首个交易日随日批跑 + 分级推送 + 落库留痕）。

【口径纪律（别破坏）】
  · 本模块**只读**：跑脚本、汇总、落库 report_store、推企微。**不改任何生产表**，
    也不改任何脚本的判据（判据一律留在脚本头，改判据须在那份脚本里新开一节）。
  · 幂等：结果落 `report_store`（tag=`verify_monthly`，名字带月份）⇒ 复用日批既有的
    `_monthly_due()` 闸门判"本月是否已复核"，**不自建第二套幂等**。
  · 推送分级（同 `_push_ic_summary` 的教训）：**有结论才推**（`force=True`，关键结论），
    全 INSUFFICIENT ⇒ **不推**（月月推"还没样本"是噪音），但照样落库 + 打日志。

【结构化约定（本模块与三个脚本的契约）】
  脚本加 `--json` ⇒ 在**人类可读输出的末尾追加一行 JSON**（不改变默认行为、不抑制正文）：
    {"script": "<名>", "verdict": "PASS|FAIL|PARTIAL|INSUFFICIENT|ERROR",
     "n": <当前样本数>, "need": <门槛>, "unit": "<单位>", "detail": "<一句话>"}
  本模块取**最后一行 `{...}`** 解析（脚本正文/警告照常打印，人跑 `--json` 也看得懂）。
================================================================================
"""
import json
import os
import subprocess
import sys
from typing import Dict, List

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(BACKEND_DIR))
REPORT_TAG = "verify_monthly"
RUN_TIMEOUT = 900          # 单脚本上限 15 分钟（月度任务；实测三者合计 <1 分钟）

# (脚本名, 中文说明, 预登记门槛, 单位)
SCRIPTS = (
    ("gate_ready_backtest", "闸门 ready==3 联合期望值（唯一买入入口）", 30, "快照日"),
    ("sector_momentum_edge_check", "板块动量延续性（是否可当因子）", 30, "交易日"),
    ("event_live_review", "E2 实盘复核（能否先行解除 defensive）", 10, "E2 触发日"),
    # ★ 2026-10-10 新增（P1-1，PLAN_RESERVE_SIGNALS §6.3）：「反弹 vs 反转」组合信号
    #   （跌过一波 + 金银比 risk-on 确认）。判据预登记在该脚本头（GO/DOWNGRADE/KILL +
    #   block bootstrap B=10000 + 复利净值 + 分年分解）；接进来即"到点自动出结论"，
    #   避免重蹈"脚本写了但没人跑 = 白等"的覆辙（本模块存在的原因）。
    ("reversal_composite_edge_check", "反弹vs反转组合（金银比 risk-on 确认）", 30, "信号日"),
    # ★ 2026-10-10 新增（P1-2，PLAN_RESERVE_SIGNALS §6.4）：把两个**孤儿/纯归档**数据源
    #   接成候选信号并预登记 —— `flow_consec`（连续主力净流入，原先算了没消费方）与
    #   龙虎榜净买强度（`lhb.py` 原先只归档不信号化）。两者都**只做展示/观察**，
    #   进决策链须看这里每月跑出的结论（判据预登记在各自脚本头）。
    ("flow_consec_edge_check", "连续主力净流入分档（孤儿字段信号化）", 5000, "观测"),
    ("lhb_edge_check", "龙虎榜净买强度（候选信号，含覆盖率门）", 300, "上榜事件"),
    # ★ 2026-10-10 新增（P2-1，PLAN_RESERVE_SIGNALS §6.5）：`intraday_path`（自建分时归档）
    #   的**第一个消费方** ——「午盘前跌>1% → 午后拉升/次日」。补上 P1-1 结论里最缺的
    #   **日内拼图**（日线看不到 V 型）。
    #   ⚠️ **时钟属性**：`intraday_path` 自 2026-10-09 起累积（腾讯分时只有当日）⇒ 初期必然
    #   INSUFFICIENT；接进来后到点自动出结论并推送（本模块存在的原因就是"别让脚本白等"）。
    ("intraday_midday_reversal_check", "午盘跌>1%的午后/次日（日内反转）", 30, "交易日"),
)
# 视为"有结论"的 verdict（除 INSUFFICIENT 外都值得让用户知道；ERROR 属运维信号也要推）
_NO_VERDICT = {"", "INSUFFICIENT", None}


def _parse(stdout: str, name: str) -> Dict:
    """取输出里**最后一行 JSON**（脚本约定）；解析不出 ⇒ ERROR（含 stderr 尾巴便于排查）。"""
    for line in reversed((stdout or "").splitlines()):
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    obj.setdefault("script", name)
                    return obj
            except ValueError:
                continue
    return {"script": name, "verdict": "ERROR",
            "detail": "未取到结构化结论（脚本未加 --json？或运行失败）"}


def run_script(name: str) -> Dict:
    """跑单个验证脚本（`--json`）并解析结论。任何异常都折成 ERROR，不抛出。"""
    path = os.path.join(ROOT, "scripts", f"{name}.py")
    meta = next((s for s in SCRIPTS if s[0] == name), None)
    if not os.path.exists(path):
        return {"script": name, "verdict": "ERROR", "detail": f"脚本不存在: {path}"}
    try:
        # ★★ 编码必须显式钉死（2026-09-30 实测踩到）：`text=True` 会用**系统默认编码**
        #   解码子进程输出 —— Windows 上是 GBK，而脚本正文/JSON 都是 UTF-8 中文
        #   ⇒ `UnicodeDecodeError` ⇒ stdout 为空 ⇒ **三个脚本全被误判成 ERROR
        #   ⇒ 月度复核会推三条假的"运行异常"**。两道保险：
        #     ① 子进程 `PYTHONIOENCODING=utf-8`（让它以 UTF-8 输出）
        #     ② 本侧 `encoding="utf-8", errors="replace"`（绝不让解码异常冒泡）
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run([sys.executable, path, "--json"], cwd=ROOT, env=env,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=RUN_TIMEOUT)
        res = _parse(p.stdout or "", name)
        if res.get("verdict") == "ERROR":
            tail = ((p.stderr or "") + (p.stdout or ""))[-300:].replace("\n", " ")
            res["detail"] = f"{res.get('detail', '')}｜输出尾部: {tail}"
    except subprocess.TimeoutExpired:
        return {"script": name, "verdict": "ERROR", "detail": f"超时（>{RUN_TIMEOUT}s）"}
    except Exception as e:
        return {"script": name, "verdict": "ERROR", "detail": str(e)[:200]}
    if meta:
        res.setdefault("need", meta[2])
        res.setdefault("unit", meta[3])
    return res


def _verdict_cn(v: str) -> str:
    return {"PASS": "✓ 通过", "FAIL": "✗ 未通过", "PARTIAL": "◐ 部分达标",
            "INSUFFICIENT": "… 样本不足", "ERROR": "⚠️ 运行异常"}.get(
                (v or "").upper(), v or "—")


def _progress(r: Dict) -> str:
    """进度文案（INSUFFICIENT 时最有价值：还差多少）。缺失字段不猜 ⇒ 留空。"""
    n, need, unit = r.get("n"), r.get("need"), r.get("unit") or ""
    if n is None:
        return ""
    if need:
        return f"（{n}/{need} {unit}）"
    return f"（{n} {unit}）" if unit else f"（{n}）"


def format_markdown(results: List[Dict]) -> str:
    """汇总 markdown（落库 + 企微共用同一份，避免两套文案漂移）。"""
    lines = ["### 预登记验证 · 月度复核", "",
             "> 口径：各脚本的判据都**预登记在各自脚本头**（改判据须在那里新开一节并注明日期）；",
             "> 本表只汇总它们自己的结论，**不重算、不改判据**。" , ""]
    for r in results:
        name = r.get("script") or "?"
        cn = next((s[1] for s in SCRIPTS if s[0] == name), "")
        lines.append(f"**{cn or name}**（`{name}`）")
        lines.append(f"- 判定：**{_verdict_cn(r.get('verdict'))}** {_progress(r)}")
        if r.get("detail"):
            lines.append(f"- {r['detail']}")
        lines.append("")
    done = [r for r in results if (r.get("verdict") or "") not in _NO_VERDICT]
    if not done:
        lines.append("> 全部仍在**等样本**（样本是时钟，开发加速不了）；"
                     "到点会自动出结论并推送，**不必人工跑**。")
    return "\n".join(lines)


def _save(md: str, day: str) -> str:
    """落 `report_store`（顺手进「回测中心」）+ 作为 `_monthly_due` 的幂等判据。"""
    try:
        from app.backtest import report_store
        name = f"verify_monthly_{str(day)[:7]}"
        ok = report_store.save_report(name, md, tag=REPORT_TAG)
        return f"已落库 {name}" if ok else "落库失败（仅日志）"
    except Exception as e:
        print(f"[edge_verify] report save failed: {str(e)[:80]}")     # ASCII（铁律⑥）
        return f"落库异常（仅日志）: {str(e)[:60]}"


def run_monthly(push: bool = True) -> str:
    """月度入口（日批任务调用）。返回**ASCII** 摘要（日批日志用，铁律⑥）。

    ★ 推送分级：有结论（含 ERROR）⇒ `force=True` 推企微；全 INSUFFICIENT ⇒ 只落库不推。
    """
    from app.flash.rules import latest_completed_trading_day
    day = latest_completed_trading_day()
    results = [run_script(s[0]) for s in SCRIPTS]
    md = format_markdown(results)
    store_note = _save(md, day)
    done = [r for r in results if (r.get("verdict") or "") not in _NO_VERDICT]
    head = (f"[edge_verify] {day} verdicts="
            + ",".join(f"{r.get('script')}:{r.get('verdict')}" for r in results)
            + f" concluded={len(done)}/{len(results)}")
    if not done:
        return head + " (all insufficient, not pushed)"
    if not push:
        return head + " push=off"
    try:
        from app.flash import wechat
        if not wechat.WECHAT_WEBHOOK:
            return head + " (no webhook, not pushed)"
        title = ("⚠️ 预登记验证有结论" if any(
            (r.get("verdict") or "").upper() in ("PASS", "FAIL", "PARTIAL") for r in done)
            else "⚠️ 预登记验证运行异常")
        # force=True：预登记结论是"能不能证明有用"的唯一证据来源，属关键通知
        #   （同 regime_alert / E2 的稀有信号口径）；频次天然受月度闸门限制。
        wechat.push_markdown_batched(title, md, force=True)
        return head + " pushed"
    except Exception as e:
        print(f"[edge_verify] push failed: {str(e)[:80]}")
        return head + " push-error"
