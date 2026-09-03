"""
================================================================================
【文件作用】每日 A 股大盘日报（盘后 16:20 自动生成）
================================================================================

定位：
  与三段"快讯复盘"(run_review)互补——快讯复盘偏事件流；本日报聚焦
  「全市场 + 系统状态 + 用户持仓 + 模拟盘」的体系化复盘。

组成（1-3 部分为硬数据直填 DB，LLM 不参与 → 保证准确；仅第 4 部分 LLM 解读）：
  1. 今日市场脉冲：ETF 关键资产涨跌 + 全市场宽度（market_snapshot）
  2. 系统状态：regime 判定与权重 / 评分榜 Top10 及较昨日变化 / 板块资金流 / 战法命中 / 模拟盘
  3. 你的持仓：现价 / 浮盈亏（DB user_portfolio × 实时行情）
  4. LLM 解读：今日主线 → 板块逻辑 → 明日预案 → 持仓操作建议（R1 单次调用，受日熔断保护）

存储与推送：
  - 文件 backend/reviews/daily_YYYY-MM-DD.md
  - 表 daily_reports(date PK, markdown, created_at)（供前端/历史回溯）
  - 企微推送（设置环境变量 DAILY_REPORT_NO_PUSH=1 可关闭推送，便于测试）
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
#  1. 今日市场脉冲（硬数据）
# ──────────────────────────────────────────────────────────────
def _etf_pulse() -> list:
    """关键 ETF 收盘涨跌（跟踪指数代理，列名 change_pct 缺失时返回空）。"""
    from app.signals import tracker
    market = tracker.get_market_data(force=True)
    holdings = market.get("holdings") or []
    out = []
    for h in holdings:
        p = h.get("price") or 0
        c = h.get("change_pct")
        if p > 0 and c is not None:
            out.append({"name": h.get("name"), "price": p, "change_pct": c})
    # 按涨跌幅排序（极端涨跌更能说明问题）
    return sorted(out, key=lambda x: x["change_pct"], reverse=True)


def _breadth() -> dict:
    """全市场宽度：来自收盘行情快照（market_snapshot）。字段缺失自动降级。"""
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
            # 独立进程/新起脚本无内存缓存 → 从 market_regime_history 恢复
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
        return {
            "holding": (holding or {}).get("n") or 0,
            "cost": (holding or {}).get("cost") or 0,
            "closed_today": (closed or {}).get("n") or 0,
            "pnl_today": (closed or {}).get("pnl") or 0,
            "realized": (acc or {}).get("realized_pnl"),
        }
    except Exception:
        return {}


def _flash_events() -> list:
    """今日快讯已推送簇的摘要（簇名→更新时间）。"""
    try:
        cutoff = rules.beijing_now().timestamp() - 12 * 3600
        out = []
        for c in store.load_state().get("pushedClusters", []):
            try:
                if datetime.fromisoformat(c["lastUpdateTime"]).timestamp() > cutoff:
                    out.append(c.get("cluster", ""))
            except Exception:
                continue
        return out[-8:]
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
#  3. 用户持仓（硬数据：DB × 实时行情）
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
        # 按 code 聚合（同股多账户/多行 → 加权均价 + 总股数），避免表格重复行
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


def build_data_md() -> str:
    lines = []
    add = lines.append

    # ── 1 市场脉冲 ──
    add("## 一、今日市场脉冲\n")
    etf = _etf_pulse()
    if etf:
        add("| 资产(ETF) | 现价 | 涨跌幅 |")
        add("|---|---|---|")
        for e in etf[:10]:
            add(f"| {e['name']} | {e['price']:.3f} | {e['change_pct']:+.2f}% |")
    breadth = _breadth()
    if breadth.get("total"):
        add("")
        add(f"- 全市场：{breadth['total']} 只 ｜ 涨 {breadth['up']} / 跌 {breadth['down']} "
            f"/ 平 {breadth['flat']}"
            + (f" ｜ 平均 {breadth['avg']:+.2f}%" if breadth.get("avg") is not None else "")
            + f"（快照 {breadth.get('saved_at', '')[:16]}）")
    if not etf and breadth.get("total"):
        add("- ETF 行情未取到（保留快照宽度统计；可能为休市/源暂不可用）")
    if not etf and not breadth.get("total"):
        add("- 市场数据暂缺（收盘快照未就绪）")

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
        add(f"**模拟盘**：持仓 {paper['holding']} 笔（市值 {_fmt_money(paper['cost'])}），"
            f"今日平仓 {paper['closed_today']} 笔盈亏 {float(paper.get('pnl_today') or 0):+,.0f} 元，"
            f"累计已实现 {float(paper.get('realized') or 0):+,.0f} 元")

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
def _llm_interpret(data_md: str) -> str:
    """R1 单次调用；失败/熔断返回空串（硬数据部分不受影响）。"""
    try:
        from app.flash import llm
        blocked = llm.llm_blocked_reason()
        if blocked:
            print(f"[daily_report] LLM 不可用: {blocked}")
            return f"\n> LLM 解读未生成（{blocked}）"
        system = (
            "你是专业的 A 股投研助手。你只会基于用户提供的结构化数据做复盘解读，"
            "绝不编造数据或数字；数据中未出现的信息一律写 N/A。"
            "你是研究员而非交易顾问：绝不给出\"买入/加仓/补仓/卖出/减仓/止损\"等指令式操作建议，"
            "只给出观察要点、关键位与风险提示。输出精炼的 Markdown。")
        user = (
            f"以下是 {_today()} A 股收盘后的系统数据。\n\n{data_md}\n\n"
            "请输出以下四个小节（每节 2-5 条，简练）:\n"
            "## 今日主线\n（一句话定性 + 普跌/权重护盘等结构判断）\n"
            "## 板块与资金\n（净流入/流出行业背后的可能逻辑，结合涨跌幅，保持审慎措辞）\n"
            "## 系统信号解读\n（regime/评分榜 Top10 异动/战法命中/模拟盘盈亏的解读与验证）\n"
            "## 明日预案与持仓观察\n（列出关键观察指标；对\"你的持仓\"每只给观察要点/风险，不给买卖指令）\n"
            "最后用 **一句话风险提示** 收尾。")
        text = llm.call_llm(system, user, temperature=0.3)
        text = (text or "").strip()
        return "\n" + text if text else ""
    except Exception as e:
        print(f"[daily_report] LLM 解读失败: {e}")
        return "\n> LLM 解读失败（硬数据部分不受影响）"


def run_daily_report(push: bool = True) -> dict:
    """
    生成当日日报：硬数据 + LLM 解读 → 落盘 + 落库 + 推送。
    push=False 时不推企微（测试用）。返回摘要 dict。
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
    if push and not os.environ.get("DAILY_REPORT_NO_PUSH"):
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
