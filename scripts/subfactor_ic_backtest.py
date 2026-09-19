#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】评分系统子指标因子体检（技术面 5 + 资金面 5，逐子项 IC/分位检验）
================================================================================
背景：
  五维评分的子指标锚点（MA/MACD/RSI/KDJ/布林/量价/动量/换手/成交额/主力净流入）
  自上线以来从未做过逐项预测力检验——flow5 倒U是第一个被回测检验的子项（09-13），
  其余子项的分数曲线仍是专家经验值。本脚本用与生产**完全同源**的评分引擎
  （ScoreEngine._score_technical / _score_capital，锚点零偏差）对每个子项做：
    - Spearman IC（去超额，5日/10日）
    - 五分位单调性（按子项分值分 5 档 → 去超额收益/胜率）
  输出：哪些子项有真实预测力（保留/权重候选），哪些是噪声（降权/移除候选）。

口径：
  - 数据：research_cache 本地 OHLC（包优先，零 Supabase）+ float_shares.json 文件缓存
  - 截面：资金流覆盖窗，前推 warmup 70 根，尾部留 max_hold，step=5（与 flow5 回测同源）
  - 样本：每股每截面喂引擎的指标数组（_calc_technical_fast 全量算一次、按日期切片）
  - 前瞻：fwd5/fwd10 + 截面全池均值去超额（x_fwd）
  - 换手率：volume(手) × 1e4 / 流通股本（与腾讯口径一致）
  - 局限：基本面/成长/质量子项依赖财报与估值快照，历史截面不可离线重建 → 不在本
    次体检范围（后续可在 ranking_history 快照积累后做）。
用法：python scripts/subfactor_ic_backtest.py [--hold 5 10] [--step 5] [--quiet]
      （日批每月例行：scripts/daily_batch.py --tasks subfactor_ic）

周期化（2026-09-20 落地，见体检报告 §建议5）：
  - 结果**落库** `backtest_reports`（tag=subfactor_ic）—— 日批跑在 GitHub Actions，
    工作区每次全新（文件必丢）⇒ 库才是权威，且前端「回测中心」可见；
  - 每轮与**上一轮**（`subfactor_ic_latest.json`）对比，滚动复核「技术面负 IC」
    是否复现 —— 这是本机制存在的意义（因子半衰期告警，与白名单 `_half_life_alert`
    同构：定期算 + 结构化告警 + 留痕）；
  - 告警判据**预先写死**在 `_review()`（防止事后挑格子）：负 IC 复现度下降 /
    显著子项符号翻转 / 倒U基础（主力净流入·极端流入）转负。
"""

import argparse
import json
import datetime as dt
import os
import sys
from collections import defaultdict

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
sys.path.insert(0, BACKEND_DIR)

from mainforce_factor_backtest import load_float_shares, spearman  # noqa: E402
from app import research_cache  # noqa: E402
from app.scoring.engine import ScoreEngine  # noqa: E402
from app.routers.scoring import _calc_technical_fast  # noqa: E402


def quintile_table(rows):
    """按子项分值分 5 档 → 每档 n / x_fwd 均值 / 去超额胜率。"""
    if len(rows) < 100:
        return []
    srt = sorted(rows, key=lambda r: r["score"])
    q = max(1, len(srt) // 5)
    out = []
    for i in range(5):
        chunk = srt[i * q: (i + 1) * q] if i < 4 else srt[4 * q:]
        x = [r["x_fwd"] for r in chunk if r.get("x_fwd") is not None]
        if not x:
            continue
        out.append({"q": i + 1, "n": len(chunk), "x": sum(x) / len(x),
                    "win": sum(1 for v in x if v > 0) / len(x) * 100})
    return out


_META_NAME = "subfactor_ic_latest.json"   # 机器可读基线（落库、覆盖式）—— 供下轮对比
_TAG = "subfactor_ic"                     # 落库 tag（日批的「月度幂等」判据也用它）


def _load_prev() -> dict:
    """上一轮的机器可读摘要（`subfactor_ic_latest.json`）。无基线/不可用返回 {}。

    ★ 为什么不读文件：日批跑在 Actions，工作区每次全新 ⇒ 文件级基线恒为空，
      「本轮 vs 上轮」永远退化成首轮。库是唯一跨运行持久的载体。
    """
    try:
        from app.backtest import report_store
        raw = report_store.get_report(_META_NAME)
        return json.loads(raw) if raw else {}
    except Exception as e:
        print(f"[review] 上轮基线读取失败（按首轮处理）: {e}")
        return {}


def _review(verdicts, prev) -> tuple:
    """滚动复核：本轮 vs 上轮 → (review, alerts)。

    判据**预先写死**（与体检报告 §建议5 一致，避免事后挑格子）：
      ① 上轮为负 IC 的子项本轮**转正** → 「负 IC 复现度下降」（反转效应可能减弱）；
      ② 上轮 |IC|≥0.04 的子项**符号翻转** → 「预测力方向翻转」；
      ③ 倒U的两个子项本轮 IC **转负** → 「倒U基础动摇」
         （09-13 倒U改造以 +0.004 / +0.093 为背书，转负即背书失效，需复检曲线）。
    """
    cur = {s: r for s, r, _ in verdicts}
    if not prev:
        return ({"neg": sum(1 for v in cur.values() if v < 0), "prev_neg": None,
                 "neg_repro": None, "verdict": "首轮无基线"}, [])
    pv = prev.get("verdicts") or {}
    common = [s for s in cur if s in pv]
    neg_prev = [s for s in common if pv[s] < 0]
    neg_now = [s for s in neg_prev if cur[s] < 0]
    alerts = []
    faded = [s for s in neg_prev if cur[s] >= 0]
    if faded:
        alerts.append(f"负 IC 复现度下降：{'、'.join(faded)} 由负转正"
                      f"（{len(neg_now)}/{len(neg_prev)} 项复现）"
                      "—— 反转效应可能减弱，复核体检报告 §建议1 的技术面降权结论")
    for s in common:
        if abs(pv[s]) >= 0.04 and pv[s] * cur[s] < 0 and s not in faded:
            alerts.append(f"预测力方向翻转：{s} 上轮 {pv[s]:+.3f} → 本轮 {cur[s]:+.3f}")
    for s in ("主力净流入", "主力极端流入(散户陷阱降分)"):
        if s in pv and cur.get(s, 0) < 0:
            alerts.append(f"倒U基础动摇：{s} IC 转负（{cur[s]:+.3f}）"
                          "—— 09-13 倒U改造的背书失效，需复检曲线")
    repro = round(len(neg_now) / len(neg_prev) * 100) if neg_prev else None
    if not neg_prev:
        verdict = "上轮无负 IC 基线"
    elif len(neg_now) == len(neg_prev):
        verdict = "全部复现"
    else:
        verdict = f"{len(neg_now)}/{len(neg_prev)} 复现"
    return ({"neg": len(neg_now), "prev_neg": len(neg_prev),
             "neg_repro": repro, "verdict": verdict}, alerts)


def run(holds=None, step=5, refresh=False, quiet=False, out_dir=None):
    """执行一次体检：算 IC → 滚动复核 → 写 md + 落库 → 返回 summary。

    返回 dict（供 `scripts/daily_batch.py` 的 `task_subfactor_ic` 月度例行调用）：
      {skipped, sections, verdicts, alerts, review, report_path, db_saved}
    数据不足时返回 `{skipped: True, reason}` 且**不写库** —— 避免污染
    「本月已体检」判据（数据不全的报告比没有报告更糟）。
    """
    holds = list(holds or [5, 10])
    step = int(step or 5)
    max_hold = max(holds)

    prices = research_cache.ohlc_all(force=refresh)
    print(f"[data] 日线覆盖 {len(prices)} 只")
    flow_map = research_cache.flow_map(force=False)
    fs_map = load_float_shares()
    print(f"[data] 资金流覆盖 {len(flow_map)} 只 / 流通股本 {len(fs_map)} 只")
    if len(prices) < 100 or len(flow_map) < 100:
        return {"skipped": True, "reason": f"数据覆盖不足（日线 {len(prices)} 只 / "
                                           f"资金流 {len(flow_map)} 只）"}

    common_dates = sorted({str(r["date"]) for rows in flow_map.values() for r in rows})
    sec_dates = common_dates[70:-max_hold] if len(common_dates) > 70 + max_hold else []
    sec_dates = sec_dates[::step]
    if len(sec_dates) < 3:
        return {"skipped": True, "reason": f"截面日不足（{len(sec_dates)} 个，需 ≥3）"}
    sec_set = set(sec_dates)
    print(f"[sections] 截面日 {len(sec_dates)} 个：{sec_dates[0]} ~ {sec_dates[-1]}")

    eng = ScoreEngine()
    subs = defaultdict(list)          # {sub_name: [{date, mkt20, score, fwd*, x_fwd*}]}

    # 市场状态代理：截面日全池等权 20 日动量（无前视，仅用截面日及以前数据）
    mkt20 = {}
    for d in sec_dates:
        rets20 = []
        for _c, bars in prices.items():
            ii = {b["date"]: j for j, b in enumerate(bars)}.get(d)
            if ii is None or ii < 20 or not bars[ii - 20]["close"]:
                continue
            rets20.append(bars[ii]["close"] / bars[ii - 20]["close"] - 1)
        if rets20:
            mkt20[d] = sum(rets20) / len(rets20)
    med_mkt20 = sorted(mkt20.values())[len(mkt20) // 2] if mkt20 else 0
    print(f"[regime-proxy] 截面 20 日动量: " + ", ".join(
        f"{d}:{mkt20.get(d, 0):+.3f}" for d in sec_dates))

    n_done = 0
    for code, bars in sorted(prices.items()):
        if code not in flow_map or len(bars) < 100:
            continue
        tech = _calc_technical_fast(bars)
        if not tech or len(tech) < 100:
            continue
        dates_idx = {b["date"]: i for i, b in enumerate(bars)}
        fs = fs_map.get(code)
        flow = [r for r in flow_map[code] if r["date"] in sec_set]

        for r in flow:
            d = r["date"]
            i = dates_idx.get(d)
            if i is None or i + max_hold >= len(bars):
                continue
            fwd_ok = True
            fwd = {}
            for h in holds:
                if i + h >= len(bars):
                    fwd_ok = False
                    break
                fwd[f"fwd{h}"] = (bars[i + h]["close"] / bars[i]["close"] - 1) * 100
            if not fwd_ok:
                continue
            k = next(kk for kk, rr in enumerate(flow_map[code]) if rr["date"] == d)
            w = flow_map[code][max(0, k - 4):k + 1]
            stock_info = {
                "price": bars[i]["close"],
                "change_pct": (bars[i]["close"] / bars[i - 1]["close"] - 1) * 100
                              if i >= 1 and bars[i - 1]["close"] else 0,
                "turnover_rate": (bars[i]["volume"] * 1e4 / fs) if fs else None,
                "flow5_amt": sum(x["main_pct"] or 0 for x in w),
            }
            tech_slice = tech[max(0, i - 59): i + 1]
            try:
                dt_tech = eng._score_technical(tech_slice)
                dt_cap = eng._score_capital(tech_slice, stock_info)
            except Exception:
                continue
            rec = {"date": d, "mkt20": mkt20.get(d, 0), **fwd}
            for sub, detail in (dt_tech.details or {}).items():
                if isinstance(detail, dict) and detail.get("分值") is not None:
                    subs[sub].append({"score": detail["分值"], **rec})
            for sub, detail in (dt_cap.details or {}).items():
                if isinstance(detail, dict) and detail.get("分值") is not None:
                    subs[sub].append({"score": detail["分值"], **rec})
        n_done += 1
        if n_done % 100 == 0:
            print(f"[score] {n_done} 只")

    # ── 截面去超额（截面全池均值，与 mainforce_factor_backtest 同口径）──
    for sub_rows in subs.values():
        by_date = defaultdict(list)
        for s in sub_rows:
            by_date[s["date"]].append(s)
        for d, grp in by_date.items():
            for h in holds:
                mkt = sum(g[f"fwd{h}"] for g in grp) / len(grp)
                for g in grp:
                    g[f"x_fwd{h}"] = g[f"fwd{h}"] - mkt

    # ── 报告 ──
    lines = []
    add = lines.append
    add("# 评分系统子指标因子体检（技术面/资金面逐子项 IC 与分位检验）\n")
    add(f"> 运行：subfactor_ic_backtest.py ｜ 生成：{dt.datetime.now():%Y-%m-%d %H:%M}"
        f" ｜ 截面 {len(sec_dates)} 个（{sec_dates[0]} ~ {sec_dates[-1]}，step={step}）\n")
    add("> 口径：生产 ScoreEngine 同源锚点（零偏差）；去超额=截面全池均值；"
        "分位=按子项分值 5 等分。基本面/成长/质量依赖财报截面，不在本次范围。\n")

    verdicts = []
    for sub, rows in sorted(subs.items()):
        if len(rows) < 200:
            continue
        add(f"\n## {sub}（n={len(rows)}）\n")
        add("| 持有 | IC(去超额) | n |")
        add("|---|---|---|")
        for h in holds:
            valid = [r for r in rows if r.get(f"x_fwd{h}") is not None]
            ic = spearman([r["score"] for r in valid], [r[f"x_fwd{h}"] for r in valid])
            add(f"| {h}日 | {ic['rho'] if ic else '-'} | {ic['n'] if ic else 0} |")
        for h in holds:
            qt = quintile_table([r for r in rows if r.get(f"x_fwd{h}") is not None])
            if qt:
                cells = [f"Q{b['q']}: n={b['n']} {b['x']:+.2f}%/{b['win']:.0f}%" for b in qt]
                mono = ("单调↑" if all(qt[i]["x"] <= qt[i + 1]["x"] + 0.15
                                       for i in range(len(qt) - 1)) else
                        ("单调↓" if all(qt[i]["x"] >= qt[i + 1]["x"] - 0.15
                                        for i in range(len(qt) - 1)) else "非单调"))
                add(f"\n- 持有{h}日五分位（按分值升序）：" + " ｜ ".join(cells) + f" → **{mono}**")
        # 结论汇总（以 5 日去超额 IC 为主）
        valid = [r for r in rows if r.get("x_fwd5") is not None]
        ic5 = spearman([r["score"] for r in valid], [r["x_fwd5"] for r in valid])
        rho = ic5["rho"] if ic5 else 0
        verdicts.append((sub, rho, len(valid)))

    add("\n\n## 汇总（按 |IC| 排序，5 日去超额）\n")
    add("| 子项 | IC(去超额,5日) | n | 体检意见 |")
    add("|---|---|---|---|")
    for sub, rho, n in sorted(verdicts, key=lambda x: -abs(x[1])):
        if abs(rho) >= 0.04:
            note = "预测力较强：保留"
        elif abs(rho) >= 0.02:
            note = "有弱预测力：保留，权重待优化"
        else:
            note = "接近噪声：降权/曲线重校准候选"
        add(f"| {sub} | {rho:+.3f} | {n} | {note} |")

    # ── 市场状态分层 IC：负 IC 是否集中在弱势截面 ──
    add("\n\n## 市场状态分层 IC（截面全池 20 日动量 >0 / <0 两组）\n")
    add("> 检验负 IC 是全时段效应还是弱势段反转效应——后者支持\"regime 权重\"而非\"改曲线\"。\n")
    add("| 子项 | IC(强市截面) | IC(弱市截面) | n强/n弱 |")
    add("|---|---|---|---|")
    for sub, rows in sorted(subs.items(), key=lambda x: -abs(x[1][0].get("mkt20", 0)) if x[1] else 0):
        if len(rows) < 200:
            continue
        for tag, cond in (("强", lambda m: m > med_mkt20), ("弱", lambda m: m <= med_mkt20)):
            pass
        up = [r for r in rows if r.get("mkt20") is not None and r["mkt20"] > med_mkt20]
        dn = [r for r in rows if r.get("mkt20") is not None and r["mkt20"] <= med_mkt20]
        ic_up = spearman([r["score"] for r in up], [r["x_fwd5"] for r in up]) if len(up) >= 100 else None
        ic_dn = spearman([r["score"] for r in dn], [r["x_fwd5"] for r in dn]) if len(dn) >= 100 else None
        add(f"| {sub} | {ic_up['rho'] if ic_up else '-'}（n={ic_up['n'] if ic_up else 0}） "
            f"| {ic_dn['rho'] if ic_dn else '-'}（n={ic_dn['n'] if ic_dn else 0}） |")

    # ── ★ 滚动复核（2026-09-20）：与上一轮对比 —— 本机制存在的意义 ──
    prev = _load_prev()
    review, alerts = _review(verdicts, prev)
    add("\n\n## 滚动复核（与上一轮对比）\n")
    if not prev:
        add("> 首轮运行，无上一轮基线（下一轮起输出「IC 变化 / 复现判定」）。")
    else:
        add(f"> 上一轮：{prev.get('date', '?')}"
            f"（截面 {prev.get('sections', '?')} 个，"
            f"{prev.get('section_first', '?')} ~ {prev.get('section_last', '?')}）"
            f"｜ 本轮截面 {len(sec_dates)} 个，{sec_dates[0]} ~ {sec_dates[-1]}\n")
        add("| 子项 | 上轮 IC(5日) | 本轮 IC(5日) | 变化 | 判定 |")
        add("|---|---|---|---|---|")
        for sub, rho, _n in sorted(verdicts, key=lambda x: -abs(x[1])):
            p = (prev.get("verdicts") or {}).get(sub)
            if p is None:
                add(f"| {sub} | - | {rho:+.3f} | 新增（无基线） | - |")
                continue
            d = rho - p
            if abs(p) >= 0.04 and p * rho < 0:
                tag = "**符号翻转**"
            elif p * rho > 0 and abs(d) < 0.05:
                tag = "复现"
            else:
                tag = "变化偏大"
            add(f"| {sub} | {p:+.3f} | {rho:+.3f} | {d:+.3f} | {tag} |")
        add(f"\n**负 IC 复现度**：上轮 {review['prev_neg']} 项 → 本轮 {review['neg']} 项"
            f" —— {review['verdict']}")
    add("\n**⚠️ 告警**：" + ("\n" + "\n".join(f"- {a}" for a in alerts) if alerts else "（无）"))

    out_dir = out_dir or os.path.join(BACKEND_DIR, "backtest_reports")
    os.makedirs(out_dir, exist_ok=True)
    stamp = dt.datetime.now()
    out = os.path.join(out_dir, f"subfactor_ic_{stamp:%Y%m%d_%H%M}.md")
    content = "\n".join(lines)
    with open(out, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[report] {out}")

    # ── 落库（Actions 工作区是临时的 ⇒ 库才是权威；前端「回测中心」也读库）──
    meta = {"date": stamp.strftime("%Y-%m-%d %H:%M"),
            "sections": len(sec_dates),
            "section_first": sec_dates[0] if sec_dates else None,
            "section_last": sec_dates[-1] if sec_dates else None,
            "section_step": step, "holds": holds,
            "verdicts": {s: round(r, 4) for s, r, _ in verdicts},
            "alerts": alerts, "review": review}
    db_saved = False
    try:
        from app.backtest import report_store
        ok_md = report_store.save_report(os.path.basename(out), content, tag=_TAG)
        ok_meta = report_store.save_report(
            _META_NAME, json.dumps(meta, ensure_ascii=False), tag=_TAG + "_meta")
        db_saved = bool(ok_md)
        print(f"[db] 落库 {'OK' if ok_md else '失败'}"
              f"（{os.path.basename(out)} / {_META_NAME} meta={ok_meta}）")
    except Exception as e:
        print(f"[db] 落库失败（文件已写，不影响结论）: {e}")

    if not quiet:
        for sub, rho, n in sorted(verdicts, key=lambda x: -abs(x[1])):
            print(f"  {sub:<14s} IC={rho:+.3f} (n={n})")
        for a in alerts:
            print(f"  [警告] {a}")

    return {"skipped": False, "sections": len(sec_dates),
            "section_first": sec_dates[0] if sec_dates else None,
            "section_last": sec_dates[-1] if sec_dates else None,
            "verdicts": {s: r for s, r, _ in verdicts},
            "alerts": alerts, "review": review,
            "report_path": out, "report_name": os.path.basename(out),
            "db_saved": db_saved}


def summary_markdown(res) -> str:
    """体检结果的企微推送摘要（月报）。

    格式注意：**不用 markdown 表格** —— 企微不支持表格，`push_markdown_batched`
    会把表格转成列表（`wechat_fmt.markdown_tables_to_lists`，幂等），不如直接写成
    行内拼接，渲染结果可控。
    内容顺序按「推送的价值」排：**告警置顶** → 复现度（本机制的核心指标）→ IC 明细。
    """
    rev = res.get("review") or {}
    alerts = res.get("alerts") or []
    lines = [f"**截面** {res.get('sections')} 个"
             f"（{res.get('section_first')} ~ {res.get('section_last')}）"
             f"｜**子项** {len(res.get('verdicts') or {})} 个"]
    if alerts:
        lines += ["", f"⚠️ **告警 {len(alerts)} 条**"] + [f"- {a}" for a in alerts]
    if rev.get("prev_neg") is not None:
        lines += ["", f"**负 IC 复现度**：上轮 {rev['prev_neg']} 项 → 本轮 {rev['neg']} 项"
                      f"（{rev.get('neg_repro')}%，{rev.get('verdict')}）"]
    v = res.get("verdicts") or {}
    if v:
        items = [f"{s} {r:+.3f}" for s, r in sorted(v.items(), key=lambda x: -abs(x[1]))]
        lines += ["", "**5 日去超额 IC（按 |IC| 降序）**：" + " ｜ ".join(items)]
    if res.get("report_name"):
        lines += ["", f"报告：`{res['report_name']}`（前端「回测中心」可读全文）"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="+", default=[5, 10])
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    run(holds=args.hold, step=args.step, refresh=args.refresh, quiet=args.quiet)


if __name__ == "__main__":
    main()
