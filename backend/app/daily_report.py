"""
================================================================================
【文件作用】每日 A 股大盘日报（盘后 16:20 自动生成）
================================================================================

定位：
  与三段"快讯复盘"(run_review)互补——快讯复盘偏事件流；本日报聚焦
  「全市场 + 系统状态 + 用户持仓 + 模拟盘」的体系化复盘。

组成（1-3 部分为硬数据直填 DB，LLM 不参与 → 保证准确；仅第 4 部分 LLM 解读）：
  1. 今日市场脉冲：外围环境 + 指数结构 + ETF + 样本宽度（market_snapshot）
  2. 系统状态：regime 判定与权重 / 评分榜 Top10 及较昨日变化 / 板块资金流 / 战法命中 / 模拟盘
  3. 你的持仓：现价 / 浮盈亏（DB user_portfolio × 实时行情）
  4. LLM 解读：今日主线 → 板块逻辑 → 明日预案 → 持仓观察（R1 单次调用，受日熔断保护）

评审驱动改进（2026-09-03）：
  - 外围环境必须纳入（macro_snapshot 已有）
  - 指数结构 vs 个股结构必须分离（防"指数红=没跌"的误导）
  - 样本范围必须标注（3230 只是有效交易样本，非全 A 5300+）
  - ETF 缺失时明确警示信号可信度下降
  - 模拟盘增加盈亏率/仓位/标的明细
  - LLM 不给买卖指令，但给观察要点+关键位
================================================================================
"""

import json
import os
from datetime import datetime

from app.database import db
from app.flash import rules, store

_REVIEWS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reviews")


def ensure_table():
    db.execute("""
        CREATE TABLE IF NOT EXISTS daily_reports (
            date TEXT PRIMARY KEY,
            markdown TEXT,
            created_at TEXT
        )
    """)


def _now() -> str:
    return rules.beijing_now().isoformat()


def _today() -> str:
    return rules.beijing_now().strftime("%Y-%m-%d")


def _yesterday_trading_day() -> str:
    """最近的上一交易日（跳过周末；节假日以 date 字符串近似）。"""
    d = rules.beijing_now().date()
    for _ in range(7):
        d = d - __import__("datetime").timedelta(days=1)
        if d.weekday() < 5:
            return d.strftime("%Y-%m-%d")
    return ""


# ──────────────────────────────────────────────────────────────
#  1. 今日市场脉冲（硬数据：外围 + 指数结构 + ETF + 样本宽度）
# ──────────────────────────────────────────────────────────────
def _macro_snapshot() -> dict:
    """宏观快照（含外围环境），失败返回空 dict。"""
    try:
        from app.macro import get_macro_snapshot
        return get_macro_snapshot() or {}
    except Exception:
        return {}


def _etf_pulse() -> list:
    """关键 ETF 收盘涨跌（跟踪指数代理）。

    ★ 字段修正：get_etf_quotes() 返回的涨跌幅字段是 `change`（不是 change_pct），
      旧代码读 change_pct 恒为 None → ETF 区块一直空。此处优先读 change，兼容 change_pct。
    ★ 盘后兜底：实时源取不到（腾讯盘后偶发空）时回退 store.load_etf_close() 收盘快照。
    """
    from app.signals import tracker
    market = tracker.get_market_data(force=True)
    holdings = market.get("holdings") or []
    if not holdings:
        close = store.load_etf_close()
        holdings = close.get("holdings") or []
    out = []
    for h in holdings:
        p = h.get("price") or 0
        c = h.get("change")
        if c is None:
            c = h.get("change_pct")  # 兼容旧字段
        if p > 0 and c is not None:
            out.append({"name": h.get("name"), "price": p, "change_pct": c})
    return sorted(out, key=lambda x: x["change_pct"], reverse=True)


def _breadth() -> dict:
    """样本宽度：来自收盘行情快照（market_snapshot）。字段缺失自动降级。"""
    snap = store.load_market_snapshot()
    stocks = snap.get("stocks") or {}
    items = list(stocks.values()) if isinstance(stocks, dict) else list(stocks)
    total = len(items)
    if not total:
        return {}
    up = down = flat = 0
    chgs = []
    for s in items:
        if not isinstance(s, dict):
            continue
        c = s.get("change_pct")
        if c is None:
            c = s.get("pct_chg") or s.get("pct")
        if c is None:
            continue
        c = float(c)
        chgs.append(c)
        if c > 0:
            up += 1
        elif c < 0:
            down += 1
        else:
            flat += 1
    return {
        "total": total, "up": up, "down": down, "flat": flat,
        "avg": round(sum(chgs) / len(chgs), 2) if chgs else None,
        "saved_at": snap.get("saved_at", ""),
    }


# ──────────────────────────────────────────────────────────────
#  2. 系统状态（硬数据）
# ──────────────────────────────────────────────────────────────
def _regime() -> dict:
    try:
        from app.backtest.market_regime import (
            get_regime_cache, get_regime_description, restore_regime_cache_from_db)
        c = get_regime_cache()
        if not c or not c.get("state"):
            restore_regime_cache_from_db()
            c = get_regime_cache()
        if not c or not c.get("state"):
            return {}
        detail = c.get("detail") or {}
        return {
            "date": c.get("date"), "state": c.get("state"),
            "desc": get_regime_description(c.get("state")),
            "weights": c.get("weights"),
            "adx": detail.get("adx"), "ma_trend": detail.get("ma_trend"),
            "vol_ratio": detail.get("volume_ratio_20d"),
        }
    except Exception:
        return {}


def _ranking_top(limit: int = 10) -> list:
    """今日 Top N 评分快照 + 昨日同股分差。"""
    out = []
    today = _today()
    yesterday = _yesterday_trading_day()
    rows = db.fetch(
        "SELECT rank_date, code, name, total_score, price, dimensions_json "
        "FROM ranking_history WHERE rank_date = %s AND dimensions_json != '' "
        "ORDER BY rank_pos ASC LIMIT %s", (today, limit))
    prev = {}
    if yesterday:
        ph = ",".join(["%s"] * len([r for r in rows]))
        if rows:
            codes = [r["code"] for r in rows]
            p_rows = db.fetch(
                "SELECT code, total_score FROM ranking_history "
                f"WHERE rank_date = %s AND code IN ({ph})", (yesterday, *codes))
            prev = {r["code"]: r["total_score"] for r in (p_rows or [])}
    for r in rows or []:
        dims = {}
        try:
            dims = json.loads(r.get("dimensions_json") or "{}")
        except Exception:
            pass
        old = prev.get(r["code"])
        out.append({
            "rank": len(out) + 1, "code": r["code"], "name": r.get("name") or r["code"],
            "total": r.get("total_score"), "price": r.get("price"),
            "delta": round((r.get("total_score") or 0) - old, 1) if old is not None else None,
            "quality": dims.get("质量"), "growth": dims.get("成长"),
        })
    return out


def _sector_flow() -> list:
    try:
        from app.eastmoney import get_sector_flow
        rows = get_sector_flow("industry", limit=40) or []
        return [{"name": r.get("name"), "change_pct": r.get("change_pct"),
                 "net_inflow": r.get("net_inflow")} for r in rows]
    except Exception:
        return []


def _strategy_scan() -> list:
    try:
        rows = db.fetch(
            "SELECT strategy_name, COUNT(*) AS n, MAX(results_json) AS results_json "
            "FROM strategy_results WHERE scan_date = %s "
            "GROUP BY strategy_name ORDER BY n DESC", (_today(),))
        out = []
        for r in rows or []:
            names = []
            try:
                arr = json.loads(r.get("results_json") or "[]") or []
                for s in arr[:3]:
                    if s.get("name"):
                        names.append(str(s["name"]))
            except Exception:
                pass
            out.append({"name": r["strategy_name"], "n": r["n"], "samples": names})
        return out
    except Exception:
        return []


def _paper_summary() -> dict:
    try:
        holding = db.fetch_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(cost), 0) AS cost "
            "FROM paper_positions WHERE status = 'holding'")
        closed = db.fetch_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(pnl_amount), 0) AS pnl "
            "FROM paper_positions WHERE status = 'closed' AND exit_date = %s", (_today(),))
        acc = db.fetch_one("SELECT * FROM paper_account WHERE id = 1")
        nav = (acc or {}).get("nav") or 0
        init = (acc or {}).get("initial_capital") or 1
        # 当日已平仓标的明细
        closed_rows = db.fetch(
            "SELECT code, name, pnl_amount, exit_reason "
            "FROM paper_positions WHERE status = 'closed' AND exit_date = %s", (_today(),))
        return {
            "holding": (holding or {}).get("n") or 0,
            "cost": (holding or {}).get("cost") or 0,
            "closed_today": (closed or {}).get("n") or 0,
            "pnl_today": (closed or {}).get("pnl") or 0,
            "realized": (acc or {}).get("realized_pnl"),
            "nav": nav,
            "nav_pct": round((nav - init) / init * 100, 2) if init else None,
            "closed_list": [{"code": r["code"], "name": r.get("name") or r["code"],
                             "pnl": r.get("pnl_amount"), "reason": r.get("exit_reason") or ""}
                            for r in (closed_rows or [])],
        }
    except Exception:
        return {}


# ──────────────────────────────────────────────────────────────
#  3. 用户持仓（硬数据：DB × 实时行情，按 code 聚合）
# ──────────────────────────────────────────────────────────────
def _portfolio() -> list:
    try:
        rows = db.fetch("SELECT * FROM user_portfolio ORDER BY created_at DESC")
        if not rows:
            return []
        codes = [str(r["code"]) for r in rows]
        quotes = {}
        try:
            from app.tencent import get_stocks_batch
            for q in (get_stocks_batch(codes) or []):
                if q and q.get("code"):
                    quotes[str(q["code"])] = q
        except Exception:
            pass
        agg = {}
        for r in rows:
            code = str(r["code"])
            cost = float(r.get("cost") or 0)
            shares = int(r.get("shares") or 0)
            cur = agg.setdefault(code, {"name": r.get("name") or code,
                                        "shares": 0, "total_cost": 0.0, "note": ""})
            cur["shares"] += shares
            cur["total_cost"] += cost * shares
            if r.get("note"):
                cur["note"] = str(r["note"])[:30]
        out = []
        for code, cur in agg.items():
            q = quotes.get(code) or {}
            price = float(q.get("price") or 0)
            cost = cur["total_cost"] / cur["shares"] if cur["shares"] else 0.0
            pnl_pct = round((price - cost) / cost * 100, 2) if cost and price else None
            out.append({
                "code": code, "name": cur["name"], "shares": cur["shares"],
                "cost": round(cost, 3), "price": price, "pnl_pct": pnl_pct,
                "note": cur["note"],
            })
        out.sort(key=lambda x: (x["pnl_pct"] if x["pnl_pct"] is not None else 0))
        return out
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
#  组装：硬数据 markdown（1-3 部分）
# ──────────────────────────────────────────────────────────────
def _fmt_money(v) -> str:
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(v) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "-"


def _northbound() -> dict:
    """北向资金当日净流入（收盘后取最终值），失败返回空 dict。"""
    try:
        from app.eastmoney import get_northbound
        nb = get_northbound()
        if not nb:
            return {}
        # 取 series 最后一根作为收盘最终值（休市时也是当日最终）
        series = nb.get("series") or []
        if series:
            last = series[-1]
            return {
                "time": last.get("time"),
                "sh_net": last.get("total_net", 0),  # series 里只有 total_net
                "total_net": nb.get("total_net", 0),
                "series_len": len(series),
            }
        return {
            "time": nb.get("time"),
            "sh_net": nb.get("sh_net", 0),
            "sz_net": nb.get("sz_net", 0),
            "total_net": nb.get("total_net", 0),
        }
    except Exception:
        return {}


def _mainforce_summary() -> dict | None:
    """当日主力行为汇总（mainforce_state 日批表，17:30 刷新）。"""
    try:
        from app.database import db
        rows = db.fetch("""
            SELECT code, name, phase, signal, flow5_amt, chip_json,
                   (SELECT MAX(date) FROM mainforce_state) AS snap_date
            FROM mainforce_state
            WHERE date = (SELECT MAX(date) FROM mainforce_state)
        """)
        if not rows:
            return None
        dist, accum = [], []
        for r in rows:
            item = {"code": r["code"], "name": r.get("name") or r["code"],
                    "phase": r.get("phase"), "flow5": r.get("flow5_amt")}
            if r["signal"] == "distribution":
                dist.append(item)
            elif r["signal"] == "accum":
                accum.append(item)
        return {"date": str(rows[0].get("snap_date") or ""),
                "total": len(rows), "dist": dist, "accum": accum}
    except Exception as e:
        print(f"[daily_report] 主力行为汇总失败: {e}")
        return None


def build_data_md() -> str:
    lines = []
    add = lines.append

    # ── 1 市场脉冲 ──
    add("## 一、今日市场脉冲\n")

    # 1a 外围环境（macro_snapshot）
    macro = _macro_snapshot()
    panel = macro.get("panel") or {}
    direction = macro.get("direction") or {}
    derived = macro.get("derived") or {}
    add("### 1.1 外围环境\n")
    if panel:
        brent = panel.get("brent", {})
        nikkei = panel.get("nikkei", {})
        nasdaq = panel.get("nasdaq", {})
        us10 = panel.get("us10y", {})
        us30 = panel.get("us30y", {})
        dxy = panel.get("dxy", {})
        parts = []
        if nikkei.get("change_pct") is not None:
            parts.append(f"日经 {nikkei['change_pct']:+.2f}%")
        # 韩国无直接代码，用宏观方向分里的标签间接反映（若有）
        tags = macro.get("tags") or []
        global_bear = [t for t in tags if t.get("group") == "global" and t.get("direction") == "bear"]
        if global_bear:
            parts.append("全球风险偏好承压（" + "、".join(t.get("tag", "") for t in global_bear[:2]) + "）")
        if brent.get("price"):
            parts.append(f"布伦特 ${brent['price']:.2f}（{_fmt_pct(brent.get('change_pct'))}）")
        if us10.get("price"):
            parts.append(f"10Y美债 {us10['price']:.2f}%（{derived.get('us10y_bp_change', 0):+.0f}bp）")
        if us30.get("price"):
            parts.append(f"30Y美债 {us30['price']:.2f}%")
        if dxy.get("price"):
            parts.append(f"美元指数 {dxy['price']:.2f}")
        if parts:
            add("- " + " ｜ ".join(parts))
        else:
            add("- 外围数据暂缺（macro 面板未就绪）")
    else:
        add("- 外围数据暂缺（macro 面板未就绪）")
    # 方向分一句话
    if direction.get("level"):
        add(f"- 宏观方向分：**{direction['level']}**（{direction.get('score', '-')} 分）"
            f"{' → A股承压' if direction.get('score', 0) < 0 else ''}")

    # 1b 指数结构 vs 个股结构（必须分离，防误导）
    add("\n### 1.2 指数与个股结构\n")
    etf = _etf_pulse()
    # 取沪深300/创业板/中证1000代理指数表现
    idx_map = {"沪深300ETF": "沪深300", "创业板ETF": "创业板", "中证1000ETF": "中证1000",
               "中证500ETF": "中证500", "科创板50ETF": "科创50"}
    idx_perf = []
    for e in etf:
        alias = idx_map.get(e["name"])
        if alias:
            idx_perf.append(f"{alias} {e['change_pct']:+.2f}%")
    if idx_perf:
        add(f"- 指数表现：{' ｜ '.join(idx_perf)}")
    else:
        add("- 指数表现：ETF 数据缺失，指数层面无法验证")

    breadth = _breadth()
    if breadth.get("total"):
        ratio = round(breadth["up"] / max(1, breadth["down"]), 2)
        add(f"- 个股样本（有效交易）：{breadth['total']} 只 ｜ "
            f"涨 {breadth['up']} / 跌 {breadth['down']} / 平 {breadth['flat']} "
            f"｜ 涨跌比 {ratio} ｜ 平均 {breadth['avg']:+.2f}%"
            f"（快照 {breadth.get('saved_at', '')[:16]}）")
        # 关键结构判断：指数红 vs 个股绿
        if idx_perf:
            # 简单判断：若任一主要指数微红/涨但个股平均跌 → 权重护盘
            any_up = any("+" in p for p in idx_perf)
            avg_neg = (breadth.get("avg") or 0) < 0
            if any_up and avg_neg:
                add("- ⚠️ **结构失真**：指数红 / 个股平均绿 = 权重股护盘，小票失血。"
                    "这不是健康市场，是磨底期典型特征。")
    else:
        add("- 个股样本数据暂缺（收盘快照未就绪）")

    # 1c 北向资金（收盘最终值）
    add("\n### 1.3 北向资金\n")
    nb = _northbound()
    if nb:
        total = nb.get("total_net") or 0
        sh = nb.get("sh_net") or 0
        sz = nb.get("sz_net") or 0
        sign = "+" if total >= 0 else ""
        add(f"- 当日净流入：**{sign}{total/1e8:.1f}亿**（沪 {sh/1e8:+.1f}亿 / 深 {sz/1e8:+.1f}亿）"
            f"（收盘 {nb.get('time', '-')}）")
        if total > 5e8:
            add("- 北向大幅净流入，外资态度积极")
        elif total < -5e8:
            add("- ⚠️ 北向大幅净流出，外资离场信号")
    else:
        add("- 北向资金数据暂缺")

    # 1d ETF 关键资产 + 缺失警示
    add("\n### 1.4 ETF 关键资产\n")
    if etf:
        add("| 资产(ETF) | 现价 | 涨跌幅 |")
        add("|---|---|---|")
        for e in etf[:10]:
            add(f"| {e['name']} | {e['price']:.3f} | {e['change_pct']:+.2f}% |")
    else:
        add("> ⚠️ **ETF 行情未取到**（可能为休市/数据源暂不可用）。"
            "今日 ETF 相关信号可信度下降，建议以个股层面数据为主。")

    # ── 2 系统状态 ──
    add("\n## 二、系统状态\n")
    reg = _regime()
    if reg.get("state"):
        w = reg.get("weights") or {}
        add(f"- 市场状态：**{reg['state']}**（{reg.get('desc', '')}）判定日期 {reg.get('date')}")
        add(f"  - ADX={reg.get('adx')}，MA趋势={reg.get('ma_trend')}"
            + (f"，量比20d={reg.get('vol_ratio')}" if reg.get("vol_ratio") is not None else ""))
        add(f"  - 生效权重：技术 {round((w.get('technical') or 0) * 100)} / 资金 "
            f"{round((w.get('capital') or 0) * 100)} / 基本面 {round((w.get('fundamental') or 0) * 100)}"
            f" / 成长 {round((w.get('growth') or 0) * 100)} / 质量 {round((w.get('quality') or 0) * 100)}")
    else:
        add("- 市场状态未判定（regime 缓存缺失）")
    top = _ranking_top()
    if top:
        add("")
        add("**评分榜 Top10（较昨日）**：")
        add("| 排名 | 代码 | 名称 | 综合分 | 日变化 | 质量 | 成长 |")
        add("|---|---|---|---|---|---|---|")
        for t in top:
            d = f"{t['delta']:+.1f}" if t["delta"] is not None else "-"
            add(f"| {t['rank']} | {t['code']} | {t['name']} | {t['total']} | {d} | "
                f"{t['quality'] if t['quality'] is not None else '-'} | "
                f"{t['growth'] if t['growth'] is not None else '-'} |")
    flow = _sector_flow()
    if flow:
        strong = [f for f in flow if (f["net_inflow"] or 0) > 0][:5]
        weak = sorted(flow, key=lambda x: (x["net_inflow"] or 0))[:5]
        add("")
        add("**行业主力净流入 Top5**：" + "、".join(
            f"{f['name']}({f['change_pct']:+.1f}%)" if f.get('change_pct') is not None else str(f['name'])
            for f in strong))
        add("**行业主力净流出 Top5**：" + "、".join(
            f"{f['name']}({f['change_pct']:+.1f}%)" if f.get('change_pct') is not None else str(f['name'])
            for f in weak))

    # 2.x 主力行为（筹码×资金流组合信号；全池截面回测 10,744 样本验证）
    mf = _mainforce_summary()
    if mf:
        add("")
        add("**主力行为扫描**（全池 "
            f"{mf['total']} 只）：出货嫌疑 **{len(mf['dist'])}** 只、吸筹区 **{len(mf['accum'])}** 只")
        if mf["dist"]:
            names = "、".join(f"{d['name']}({d['flow5']:+.0f}%)" if d.get("flow5") is not None
                              else d["name"] for d in mf["dist"][:8])
            add(f"- ⚠️ 出货嫌疑（高位高获利+主力流出，回测 10 日 -7.5pt）：{names}"
                + (" 等" if len(mf["dist"]) > 8 else ""))
        if mf["accum"]:
            names = "、".join(f"{d['name']}({d['flow5']:+.0f}%)" if d.get("flow5") is not None
                              else d["name"] for d in mf["accum"][:8])
            add(f"- 🎯 吸筹区（低位筹码密集+主力净流入，回测 10 日 +1.1pt）：{names}"
                + (" 等" if len(mf["accum"]) > 8 else ""))

    scan = _strategy_scan()
    if scan:
        add("")
        parts = []
        for s in scan:
            seg = f"{s['name']}×{s['n']}"
            if s.get("samples"):
                seg += "(" + "、".join(s["samples"]) + ")"
            parts.append(seg)
        add("**今日战法扫描命中**：" + "、".join(parts))
    paper = _paper_summary()
    if paper:
        add("")
        nav_info = f"，NAV {float(paper.get('nav') or 0):,.0f}（{paper.get('nav_pct') or '-'}%）" if paper.get("nav") else ""
        pnl_rate = ""
        if paper.get("cost"):
            try:
                rate = float(paper.get("pnl_today") or 0) / max(1, float(paper["cost"])) * 100
                pnl_rate = f"（{rate:+.3f}%）"
            except Exception:
                pass
        add(f"**模拟盘**：持仓 {paper['holding']} 笔（市值 {_fmt_money(paper['cost'])}）{nav_info}，"
            f"今日平仓 {paper['closed_today']} 笔盈亏 {float(paper.get('pnl_today') or 0):+,.0f} 元{pnl_rate}，"
            f"累计已实现 {float(paper.get('realized') or 0):+,.0f} 元")
        if paper.get("closed_list"):
            add("- 平仓明细：" + "、".join(
                f"{c['name']}({float(c['pnl'] or 0):+,.0f}｜{c['reason']})"
                for c in paper["closed_list"]))

    # ── 3 用户持仓 ──
    pos = _portfolio()
    if pos:
        add("\n## 三、你的持仓\n")
        add("| 代码 | 名称 | 股数 | 成本 | 现价 | 浮盈% | 备注 |")
        add("|---|---|---|---|---|---|---|")
        for p in pos:
            pnl = f"{p['pnl_pct']:+.2f}%" if p["pnl_pct"] is not None else "-"
            add(f"| {p['code']} | {p['name']} | {p['shares']} | {p['cost']} | "
                f"{p['price'] if p['price'] else '-'} | {pnl} | {p['note']} |")
    else:
        add("\n## 三、你的持仓\n- 暂无持仓记录（user_portfolio 为空）")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
#  4. LLM 解读 + 收尾
# ──────────────────────────────────────────────────────────────
_ORDER_WORDS = ("买入", "卖出", "加仓", "减仓", "补仓", "清仓", "止损", "止盈",
                "追涨", "杀跌", "抄底", "做多", "做空", "建仓", "空仓")

_SYSTEM = (
    "你是专业的 A 股投研助手。你只会基于用户提供的结构化数据做复盘解读，"
    "绝不编造数据或数字；数据中未出现的信息一律写 N/A。"
    "你是研究员而非交易顾问：绝不给出\"买入/加仓/补仓/卖出/减仓/止损\"等指令式操作建议，"
    "只给出观察要点、关键位与风险提示。"
    "特别注意：\n"
    "1. 若系统判定为震荡偏空(neutral_bearish)，对\"突破信号\"应指出假突破概率高，而非\"机会\"。\n"
    "2. 对\"涨但资金流出\"的板块，应指出\"拉高出货\"风险，而非\"数据波动\"。\n"
    "3. 同一板块不能同时给出矛盾解读（如既\"利好\"又\"避险\"）；若缩量上涨，优先解读为护盘。\n"
    "4. 持仓点评必须给出具体观察位（如\"跌破X元需警惕\"），不能只说\"关注支撑\"。\n"
    "5. 北向资金大幅净流出（>5亿）时，必须指出外资离场风险。\n"
    "必须输出合法 JSON，不要输出 Markdown 或围栏。")


def _valid_holdings_codes() -> set:
    """真实持仓 code 集合（防 LLM 编造持仓观察）。"""
    return {str(p["code"]) for p in _portfolio() if p.get("code")}


def _render_llm_json(data: dict, valid_codes: set) -> str:
    """把 LLM 结构化 JSON 渲染成 markdown（代码层校验 + 过滤）。"""
    lines = []
    add = lines.append

    mainline = str(data.get("mainline") or "").strip()
    if mainline:
        add("## 今日主线")
        add(mainline)
        add("")

    sector = [str(x).strip() for x in (data.get("sector_notes") or []) if str(x).strip()]
    if sector:
        add("## 板块与资金")
        for n in sector[:5]:
            add(f"- {n}")
        add("")

    system = [str(x).strip() for x in (data.get("system_notes") or []) if str(x).strip()]
    if system:
        add("## 系统信号解读")
        for n in system[:5]:
            add(f"- {n}")
        add("")

    watch = [str(x).strip() for x in (data.get("tomorrow_watch") or []) if str(x).strip()]
    holdings = []
    for h in (data.get("holdings") or []):
        if not isinstance(h, dict):
            continue
        code = str(h.get("code") or "").strip()
        note = str(h.get("note") or "").strip()
        if code not in valid_codes or not note:       # 防幻觉：code 必须在真实持仓里
            continue
        if any(w in note for w in _ORDER_WORDS):       # 防指令：剔除含买卖指令词的点评
            continue
        name = str(h.get("name") or "").strip() or code
        level = h.get("key_level")
        lv = f"（关键位 {level:g}）" if isinstance(level, (int, float)) and level > 0 else ""
        holdings.append(f"- {code} {name}：{note}{lv}")
    if watch or holdings:
        add("## 明日预案与持仓观察")
        if watch:
            add("**关键观察指标**：")
            for i, w in enumerate(watch[:5], 1):
                add(f"{i}. {w}")
        if holdings:
            add("")
            add("**持仓观察位**：")
            add("\n".join(holdings[:10]))
        add("")

    risk = str(data.get("risk") or "").strip()
    if risk and not any(w in risk for w in _ORDER_WORDS):
        add(f"**一句话风险提示**：{risk}")

    return "\n".join(lines).rstrip()


def _llm_interpret(data_md: str) -> str:
    """结构化 JSON 输出 + 代码层校验；JSON 失败回退纯 markdown。"""
    try:
        from app.flash import llm
        blocked = llm.llm_blocked_reason()
        if blocked:
            print(f"[daily_report] LLM 不可用: {blocked}")
            return f"\n> LLM 解读未生成（{blocked}）"
        valid_codes = _valid_holdings_codes()
        codes_hint = "、".join(sorted(valid_codes)) if valid_codes else "（无持仓）"
        user = (
            f"以下是 {_today()} A 股收盘后的系统数据。\n\n{data_md}\n\n"
            f"真实持仓代码：{codes_hint}\n\n"
            "请输出 JSON（不要 Markdown），字段：\n"
            "{\n"
            '  "mainline": "一句话定性（区分指数表现与个股表现）",\n'
            '  "sector_notes": ["板块/资金解读 2-5 条"],\n'
            '  "system_notes": ["regime/评分榜/战法/模拟盘解读 2-5 条"],\n'
            '  "tomorrow_watch": ["明日关键观察指标 3-5 条"],\n'
            '  "holdings": [{"code":"600578","name":"京能电力","key_level":5.40,"note":"跌破5.40需警惕"}],\n'
            '  "risk": "一句话风险提示"\n'
            "}\n"
            "holdings 的 code 必须严格取自上面真实持仓代码，不得编造；"
            "note 只能写观察位/风险（如\"跌破X元需警惕\"），不得含买入/卖出/止损等指令词。")
        try:
            data = llm._call_json(_SYSTEM, user, temperature=0.3)
        except Exception as e:
            print(f"[daily_report] _call_json 异常: {e}")
            data = {}
        if data:
            md = _render_llm_json(data, valid_codes)
            if md.strip():
                return "\n" + md
        return _llm_interpret_markdown(data_md)
    except Exception as e:
        print(f"[daily_report] LLM 解读失败: {e}")
        return "\n> LLM 解读失败（硬数据部分不受影响）"


def _llm_interpret_markdown(data_md: str) -> str:
    """纯 markdown 兜底（结构化 JSON 失败时使用，保留原逻辑）。"""
    try:
        from app.flash import llm
        system = _SYSTEM + "\n输出精炼的 Markdown。"
        user = (
            f"以下是 {_today()} A 股收盘后的系统数据。\n\n{data_md}\n\n"
            "请输出以下四个小节（每节 2-5 条，简练）:\n"
            "## 今日主线\n"
            "（一句话定性：分化/普跌/权重护盘/磨底等；必须区分\"指数表现\"和\"个股表现\"）\n"
            "## 板块与资金\n"
            "（净流入/流出行业背后的可能逻辑；对涨但资金流出的板块指出拉高出货风险；"
            "对证券Ⅲ等子行业，若缩量上涨则解读为护盘而非市场自发看好；"
            "北向资金动向必须单独点评，与内资板块流向做对比）\n"
            "## 系统信号解读\n"
            "（regime/评分榜 Top10 异动/战法命中/模拟盘盈亏的解读；"
            "若 regime=neutral_bearish 且出现突破信号，指出假突破概率高）\n"
            "## 明日预案与持仓观察\n"
            "（列出 3-5 个关键观察指标；对\"你的持仓\"每只给具体观察位/风险，"
            "如\"跌破X元需警惕\"或\"反弹至Y元观察量能\"；不给买卖指令）\n"
            "最后用 **一句话风险提示** 收尾。")
        text = llm.call_llm(system, user, temperature=0.3)
        text = (text or "").strip()
        return "\n" + text if text else ""
    except Exception as e:
        print(f"[daily_report] LLM markdown 兜底失败: {e}")
        return ""


def run_daily_report(push: bool = False) -> dict:
    """
    生成当日日报：硬数据 + LLM 解读 → 落盘 + 落库（+ 可选推送）。

    企微推送默认关闭：日报已有前端阅读页（/report），每日推送会刷屏。
    需要推送时显式开启：run_daily_report(push=True) 或环境变量 DAILY_REPORT_PUSH=1。
    返回摘要 dict。
    """
    ensure_table()
    os.makedirs(_REVIEWS_DIR, exist_ok=True)
    date = _today()
    data_md = build_data_md()
    llm_md = _llm_interpret(data_md)
    md = (f"# A股日报 {date}\n> 生成：{_now()}\n\n---\n\n"
          + data_md
          + "\n\n---\n\n## 四、AI 解读\n" + llm_md + "\n")
    fpath = os.path.join(_REVIEWS_DIR, f"daily_{date}.md")
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception as e:
        print(f"[daily_report] 写文件失败: {e}")
    try:
        db.upsert("daily_reports", {"date": date, "markdown": md,
                                    "created_at": _now()}, conflict_columns=["date"])
    except Exception as e:
        print(f"[daily_report] 落库失败: {e}")
    pushed = False
    # 默认不推企微；需推送时 push=True 或 DAILY_REPORT_PUSH=1
    if push or os.environ.get("DAILY_REPORT_PUSH") == "1":
        try:
            from app.flash import wechat
            wechat.push_markdown_batched(f"📋 A股日报 {date}", md)
            pushed = True
        except Exception as e:
            print(f"[daily_report] 推送失败: {e}")
    return {"date": date, "path": fpath, "len": len(md), "pushed": pushed}


def latest(date: str = None) -> dict:
    """读最近一份日报（供手动脚本/未来路由）。"""
    if not date:
        row = db.fetch_one("SELECT * FROM daily_reports ORDER BY date DESC LIMIT 1")
    else:
        row = db.fetch_one("SELECT * FROM daily_reports WHERE date = %s", (date,))
    return dict(row) if row else {}
