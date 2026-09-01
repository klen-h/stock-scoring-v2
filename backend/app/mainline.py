"""
================================================================================
【文件作用】行业主线/共振分析 —— 行业映射补齐（新浪全量）后的核心决策功能
================================================================================

数据源：
  - ranking_history（每日评分 Top50 快照，rank_pos<=50）
  - stock_industry（新浪全市场行业映射，Top50 命中率 84%）

核心产出：
  1. 主线榜：连续多日在 Top50 扎堆的行业（占比 + 趋势 + 候选股）
  2. 风格切换信号：行业占比相对窗口前半段突变（如 医药 6只→1.2只退出、
     金融/有色接棒）——这是"静态榜单"看不到的资金切换信号

调度：scheduler.mainline_loop 每交易日 16:05 跑（ranking_history 已落库），
      分析落库 industry_mainline 并推送企微日报。
================================================================================
"""
import json
import time

from app.database import db
from app.flash.rules import beijing_now

MAINLINE_WINDOW = 8          # 趋势对比窗口（交易日）
MAINLINE_MIN_APPEAR = 0.5    # 行业出现率 ≥ 窗口一半才够格候选
MAINLINE_MIN_AVG = 1.5       # 日均在 Top50 里 ≥ 1.5 只才够格候选
SWITCH_DELTA = 1.5           # 风格切换判定：后1/4段日均 - 前段日均 ≥ 该值（只）


def init_mainline_table():
    """每日行业共振结果表（幂等，模块导入即建表）。"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS industry_mainline (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            industry TEXT NOT NULL,
            stock_count INTEGER,
            sum_rank REAL,
            stocks_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, industry)
        )
    """)
    print("[mainline] industry_mainline 表初始化完成")


init_mainline_table()


def compute_mainline(date: str = None) -> dict:
    """分析指定交易日（默认今天）的行业共振，落库 industry_mainline（幂等）。

    返回 {ok, date, industries, unknown_stocks, cost_ms}；失败不破坏旧数据。
    """
    t0 = time.time()
    if date is None:
        date = beijing_now().strftime("%Y-%m-%d")
    rows = db.fetch(
        "SELECT code, name, rank_pos FROM ranking_history "
        "WHERE rank_date = %s AND rank_pos <= 50 ORDER BY rank_pos", (date,))
    if not rows:
        return {"ok": False, "error": f"{date} 无 Top50 数据（先跑评分快照）"}
    ind_map = {r["code"]: r["main_industry"]
               for r in db.fetch(
                   "SELECT code, main_industry FROM stock_industry")}
    agg = {}
    unknown = 0
    for r in rows:
        ind = ind_map.get(r["code"])
        if not ind:
            unknown += 1
            continue
        a = agg.setdefault(ind, {"stock_count": 0, "sum_rank": 0.0, "stocks": []})
        a["stock_count"] += 1
        a["sum_rank"] += r["rank_pos"]
        a["stocks"].append({"code": r["code"], "name": r["name"],
                            "rank": r["rank_pos"]})
    if not agg:
        return {"ok": False, "error": "当日 Top50 全部无法映射行业（检查 stock_industry）"}
    now = beijing_now().isoformat(timespec="seconds")
    # ★ 全量重算该日：先删旧行再插（行业名可能因映射更新而变更，残留旧名行
    # 会被汇总误判成"伪退出信号"，如农商行Ⅲ 4.0→1.0 实为映射升级非资金流出）
    db.execute("DELETE FROM industry_mainline WHERE date = %s", (date,))
    for ind, a in agg.items():
        a["sum_rank"] = round(a["sum_rank"], 1)
        db.execute("""
            INSERT INTO industry_mainline
                (date, industry, stock_count, sum_rank, stocks_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (date, ind, a["stock_count"], a["sum_rank"],
              json.dumps(a["stocks"], ensure_ascii=False), now))
    print(f"[mainline] {date} 完成: {len(agg)} 行业 / 未知 {unknown} 只 / "
          f"{int((time.time() - t0) * 1000)}ms")
    return {"ok": True, "date": date, "industries": len(agg),
            "unknown_stocks": unknown, "cost_ms": int((time.time() - t0) * 1000)}


def get_mainline_summary(days: int = 12) -> dict:
    """最近 N 个交易日主线汇总：主线榜 + 风格切换信号。

    主线榜：出现率 ≥ 一半、日均 ≥ 1.5 只；趋势 = 窗口后半段 vs 前半段日均。
    切换信号：后 1/4 段日均 vs 前 3/4 段日均，变化 ≥ SWITCH_DELTA 只。
    """
    today = beijing_now().strftime("%Y-%m-%d")
    dates = [r["rank_date"] for r in db.fetch(
        "SELECT DISTINCT rank_date FROM ranking_history "
        "WHERE rank_date <= %s ORDER BY rank_date DESC LIMIT %s",
        (today, days))]
    dates.reverse()
    if not dates:
        return {"ok": False, "error": "无历史 Top50 数据"}
    rows = db.fetch(
        "SELECT date, industry, stock_count, sum_rank, stocks_json "
        "FROM industry_mainline WHERE date >= %s ORDER BY date", (dates[0],))
    seq = {}   # industry -> {date: row}
    for r in rows or []:
        seq.setdefault(r["industry"], {})[r["date"]] = r
    N = len(dates)
    split = N // 2
    early, late = dates[:split], dates[split:]
    mains = []
    for ind, dd in seq.items():
        if ind == "其它行业":
            continue          # 兜底杂桶（未细分股聚合），无主线决策意义
        appear = len(dd)
        if appear < max(2, N * MAINLINE_MIN_APPEAR):
            continue
        tot = sum(dd[d]["stock_count"] for d in dd)
        avg = tot / appear
        if avg < MAINLINE_MIN_AVG:
            continue
        ea = (sum(dd[d]["stock_count"] for d in early if d in dd)
              / max(1, len([d for d in early if d in dd])))
        la = (sum(dd[d]["stock_count"] for d in late if d in dd)
              / max(1, len([d for d in late if d in dd])))
        ar = sum(dd[d]["sum_rank"] for d in dd) / max(1, tot)
        trend = "up" if la > ea + 0.3 else ("down" if la < ea - 0.3 else "flat")
        latest = dd.get(dates[-1]) or {}
        stocks = (json.loads(latest.get("stocks_json") or "[]")
                  if latest else [])
        mains.append({
            "industry": ind, "appear": f"{appear}/{N}", "appear_num": appear,
            "avg": round(avg, 1), "early": round(ea, 1), "recent": round(la, 1),
            "avg_rank": round(ar), "trend": trend,
            "latest_count": latest.get("stock_count", 0),
            "latest_stocks": stocks[:8],
        })
    mains.sort(key=lambda x: (-x["recent"], -x["appear_num"], x["avg_rank"]))
    # 风格切换信号
    recent_n = max(2, N // 4)
    rd = dates[-recent_n:]
    pd = dates[:-recent_n] or dates[:recent_n]
    switches = []
    for ind, dd in seq.items():
        if ind == "其它行业":
            continue          # 兜底杂桶不产生切换信号
        recent_avg = (sum(dd[d]["stock_count"] for d in rd if d in dd)
                      / max(1, len([d for d in rd if d in dd])))
        prior_avg = (sum(dd[d]["stock_count"] for d in pd if d in dd)
                     / max(1, len([d for d in pd if d in dd])))
        delta = recent_avg - prior_avg
        if recent_avg >= 1.5 and delta >= SWITCH_DELTA:
            switches.append({"industry": ind, "action": "in",
                             "from": round(prior_avg, 1),
                             "to": round(recent_avg, 1)})
        elif prior_avg >= 1.5 and delta <= -SWITCH_DELTA:
            switches.append({"industry": ind, "action": "out",
                             "from": round(prior_avg, 1),
                             "to": round(recent_avg, 1)})
    switches.sort(key=lambda x: -abs(x["from"] - x["to"]))
    return {"ok": True, "days": N, "dates": [dates[0], dates[-1]],
            "mainlines": mains[:10], "switches": switches[:5],
            "unknown_latest": _latest_unknown()}


def _latest_unknown() -> int:
    """最新一天 Top50 中未映射行业的股票数（监控映射质量）。"""
    today = beijing_now().strftime("%Y-%m-%d")
    row = db.fetch_one(
        "SELECT date FROM industry_mainline "
        "WHERE date <= %s ORDER BY date DESC LIMIT 1", (today,))
    if not row:
        return -1
    date = row["date"]
    inds = {r["code"] for r in db.fetch(
        "SELECT code FROM stock_industry")}
    rows = db.fetch(
        "SELECT code FROM ranking_history WHERE rank_date = %s AND rank_pos <= 50",
        (date,))
    return sum(1 for r in rows or [] if r["code"] not in inds)


def push_mainline_report(days: int = 12) -> dict:
    """推送今日主线日报到企微（受业务推送开关限制）。"""
    s = get_mainline_summary(days)
    if not s.get("ok"):
        return {"ok": False, "error": s.get("error")}
    if not s["mainlines"]:
        return {"ok": False, "error": "窗口内无达标主线（数据不足？）"}
    lines = [f"> 窗口 {s['dates'][0]} ~ {s['dates'][1]}（{s['days']} 个交易日）\n"]
    lines.append("### 📈 当前主线榜（Top50 行业占比）")
    arrow = {"up": "▲", "down": "▼", "flat": "→"}
    for m in s["mainlines"]:
        stocks = "、".join(f"{x['name']}({x['rank']})"
                           for x in m["latest_stocks"][:5])
        lines.append(f"**{m['industry']}** {arrow.get(m['trend'])} "
                     f"出现{m['appear']} 近日均**{m['recent']}**只(早{m['early']}) "
                     f"均排名{m['avg_rank']}")
        if stocks:
            lines.append(f"  └ {stocks}")
    if s["switches"]:
        lines.append("\n### 🔄 风格切换信号")
        for w in s["switches"]:
            act = "流入" if w["action"] == "in" else "退出"
            lines.append(f"- **{w['industry']}** {act}：{w['from']}只 → {w['to']}只")
    if s["unknown_latest"] > 0:
        lines.append(f"\n> ⚠️ 最新一天 {s['unknown_latest']} 只 Top50 股票无行业映射")
    from app.flash.wechat import push_markdown_batched
    push_markdown_batched("🧭 行业主线日报", "\n".join(lines))
    return {"ok": True, "mainlines": len(s["mainlines"]),
            "switches": len(s["switches"])}
