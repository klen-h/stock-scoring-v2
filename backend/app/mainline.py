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

# ★ 2026-09-13 P1-3 主线拥挤度否决：
#   离线验证（scripts/mainline_crowding_backtest.py，630 条主线候选）——命中「拥挤」
#   （ret20>30% / ret60>50% / 距 250 日高点<5% 任一）的候选 T+5 胜率 32.6%/均 -1.411%，
#   显著差于不拥挤组 40.2%/-0.292%（差 -7.6pp / -1.12pt）；T+1 同向（-5.2pp / -0.40pt）。
#   → 拥挤主线**不再给传导链 +1 分**，标签降级「拥挤主线（只减不加）」。
CROWD_RET20 = 30.0               # 20 日涨幅阈值 %
CROWD_RET60 = 50.0               # 60 日涨幅阈值 %
CROWD_NEAR_HIGH = 5.0            # 距 250 日高点阈值 %
CROWD_RATIO_THRESHOLD = 0.5      # 行业内拥挤候选股占比 ≥ 该值 → 行业判为拥挤主线


def _load_bars_for_crowding(codes: list, date: str) -> dict:
    """加载候选股历史收盘价 {code: [(date, close)]}（覆盖 250 交易日）。

    起点取 date 前 400 自然日（足够 250 交易日 + 60 日窗口）。候选股不在
    backtest_prices 的（未回填）自然缺席，由调用方按"可评估样本"处理。
    """
    from datetime import datetime, timedelta
    try:
        start = (datetime.strptime(date, "%Y-%m-%d")
                 - timedelta(days=400)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return {}
    out = {}
    codes = [c for c in codes if c]
    for i in range(0, len(codes), 100):
        chunk = codes[i:i + 100]
        ph = ",".join(["%s"] * len(chunk))
        try:
            rows = db.fetch(
                f"SELECT code, date, close FROM backtest_prices "
                f"WHERE code IN ({ph}) AND date >= %s AND date <= %s "
                f"ORDER BY code, date", (*chunk, start, date))
        except Exception:
            continue
        for r in rows or []:
            if (r.get("close") or 0) > 0:
                out.setdefault(r["code"], []).append(
                    (str(r["date"]), float(r["close"])))
    return out


def _crowding_flags(stocks: list, bars_map: dict, date: str) -> tuple:
    """行业内候选股的拥挤比例 → (crowded:int, ratio:float|None)。

    个股拥挤判据（全部用 date 及之前数据，无前视）：ret20>CROWD_RET20% 或
    ret60>CROWD_RET60% 或 距 250 日高点 <CROWD_NEAR_HIGH%。行业拥挤 = 拥挤股占比
    ≥ CROWD_RATIO_THRESHOLD。无任何可评估样本时返回 (0, None)（不误标拥挤）。
    """
    hit, total = 0, 0
    for s in stocks:
        bars = bars_map.get(s.get("code"))
        if not bars:
            continue
        idx = next((i for i, (d, _c) in enumerate(bars) if d == date), None)
        if idx is None or idx < 60:
            continue
        total += 1
        close = bars[idx][1]

        def _ret(n):
            if idx - n < 0:
                return None
            b = bars[idx - n][1]
            return (close / b - 1) * 100 if b > 0 else None

        r20, r60 = _ret(20), _ret(60)
        lo = max(0, idx - 249)
        high = max(b[1] for b in bars[lo:idx + 1])
        dist = (close / high - 1) * 100 if high > 0 else None
        if ((r20 is not None and r20 > CROWD_RET20)
                or (r60 is not None and r60 > CROWD_RET60)
                or (dist is not None and dist > -CROWD_NEAR_HIGH)):
            hit += 1
    if total == 0:
        return 0, None
    ratio = hit / total
    return (1 if ratio >= CROWD_RATIO_THRESHOLD else 0), round(ratio, 2)


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
            crowded INTEGER DEFAULT 0,
            crowd_ratio REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, industry)
        )
    """)
    # ★ 2026-09-13 P1-3：老表幂等补列（拥挤主线标签）
    for col in ("crowded INTEGER DEFAULT 0", "crowd_ratio REAL"):
        try:
            db.execute(f"ALTER TABLE industry_mainline ADD COLUMN IF NOT EXISTS {col}")
        except Exception:
            pass
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
    # ★ 2026-09-13 P1-3：主线拥挤度判定（个股维度，盘后一次算完落库）
    crowd_codes = [s["code"] for a in agg.values() for s in a["stocks"] if s.get("code")]
    bars_map = _load_bars_for_crowding(crowd_codes, date)
    for a in agg.values():
        a["crowded"], a["crowd_ratio"] = _crowding_flags(a["stocks"], bars_map, date)
    # ★ 全量重算该日：先删旧行再插（行业名可能因映射更新而变更，残留旧名行
    # 会被汇总误判成"伪退出信号"，如农商行Ⅲ 4.0→1.0 实为映射升级非资金流出）
    db.execute("DELETE FROM industry_mainline WHERE date = %s", (date,))
    for ind, a in agg.items():
        a["sum_rank"] = round(a["sum_rank"], 1)
        db.execute("""
            INSERT INTO industry_mainline
                (date, industry, stock_count, sum_rank, stocks_json,
                 crowded, crowd_ratio, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (date, ind, a["stock_count"], a["sum_rank"],
              json.dumps(a["stocks"], ensure_ascii=False),
              a.get("crowded", 0), a.get("crowd_ratio"), now))
    crowded_n = sum(1 for a in agg.values() if a.get("crowded"))
    print(f"[mainline] {date} 完成: {len(agg)} 行业 / 未知 {unknown} 只 / "
          f"拥挤主线 {crowded_n} 个 / {int((time.time() - t0) * 1000)}ms")
    return {"ok": True, "date": date, "industries": len(agg),
            "unknown_stocks": unknown, "crowded": crowded_n,
            "cost_ms": int((time.time() - t0) * 1000)}


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
    # ★ 2026-09-13 走查修复：旧库可能没有 crowded/crowd_ratio 列（SQLite 不支持
    #   `ADD COLUMN IF NOT EXISTS`，`init_mainline_table` 的 ALTER 会静默失败）→
    #   直接查这两列会让**整个主线汇总**抛异常。降级为不含它们的基础查询
    #   （`latest.get("crowded")` 取不到时视为 False，行为与迁移前一致）。
    try:
        rows = db.fetch(
            "SELECT date, industry, stock_count, sum_rank, stocks_json, "
            "crowded, crowd_ratio "
            "FROM industry_mainline WHERE date >= %s ORDER BY date", (dates[0],))
    except Exception:
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
            # ★ 2026-09-13 P1-3：拥挤主线（只减不加，传导链不给 +1）
            "crowded": bool(latest.get("crowded")),
            "crowd_ratio": latest.get("crowd_ratio"),
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
        crowd_tag = "（拥挤主线·只减不加）" if m.get("crowded") else ""
        lines.append(f"**{m['industry']}**{crowd_tag} {arrow.get(m['trend'])} "
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


def get_mainline_performance(days: int = 30, horizons: tuple = (1, 5, 10)) -> dict:
    """主线候选股后续收益 vs 全市场基准——验证「跟主线」是否有效。

    ★ 2026-09-09 新增（诉求：把主线痕迹整合起来逐步验证）。

    口径：
      - 主线候选 = 当日 Top50 中、所属行业当日 stock_count>=2（扎堆）的股票。
        当日扎堆才叫主线，孤零零 1 只不算（避免把噪音当主线）。
      - baseline 组 = 同日全部 Top50 股票（同池对比，剔除入选偏差）。
      - 市场基准 = 沪深300（sh000300）同期指数收益。
      - 每条快照按 T+1/5/10 交易日收盘价算收益（与 BucketStats 同源价格序列）。
    """
    from app.scoring.ranking_history import _load_price_series, _stats

    today = beijing_now().strftime("%Y-%m-%d")
    dates = [r["rank_date"] for r in db.fetch(
        "SELECT DISTINCT rank_date FROM ranking_history WHERE rank_date <= %s "
        "ORDER BY rank_date DESC LIMIT %s", (today, days))]
    dates.reverse()
    if len(dates) < 3:
        return {"ok": False, "error": f"快照天数不足（仅 {len(dates)} 天）"}

    since = dates[0]
    ml_rows = db.fetch(
        "SELECT date, industry, stock_count, stocks_json FROM industry_mainline "
        "WHERE date >= %s", (since,))
    top_rows = db.fetch(
        "SELECT rank_date, code FROM ranking_history WHERE rank_date >= %s",
        (since,))

    # 当日主线行业（扎堆阈值：当日 >=2 只 Top50 命中）
    mainline_pairs = {}          # (date, code) -> industry
    for r in ml_rows or []:
        if (r.get("stock_count") or 0) < 2:
            continue             # 单只不成主线
        try:
            stocks = json.loads(r.get("stocks_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        for s in stocks:
            c = s.get("code")
            if c:
                mainline_pairs[(r["date"], c)] = r["industry"]

    if not mainline_pairs:
        return {"ok": False, "error": "窗口内无主线候选（industry_mainline 数据不足？）"}

    # 价格序列：主线候选 ∪ 全部 Top50 ∪ 沪深300（一次批量加载）
    all_codes = ({c for (_d, c) in mainline_pairs}
                 | {t["code"] for t in top_rows} | {"sh000300"})
    series = _load_price_series(list(all_codes), since)
    date_idx = {c: {d: i for i, (d, _) in enumerate(cl)} for c, cl in series.items()}

    ml = {h: [] for h in horizons}
    base50 = {h: [] for h in horizons}
    hs300 = {h: [] for h in horizons}
    for t in top_rows:
        d, code = t["rank_date"], t["code"]
        cl = series.get(code)
        if not cl:
            continue
        i = date_idx.get(code, {}).get(d)
        if i is None:
            continue
        in_ml = (d, code) in mainline_pairs
        for h in horizons:
            if i + h >= len(cl):
                continue
            b, tgt = cl[i][1], cl[i + h][1]
            if not b or b <= 0 or not tgt:
                continue
            ret = (tgt - b) / b * 100
            base50[h].append(ret)
            if in_ml:
                ml[h].append(ret)

    # 沪深300 指数同期（每个快照日独立算 T+N）
    cl300 = series.get("sh000300")
    if cl300:
        idx300 = {d: i for i, (d, _) in enumerate(cl300)}
        for d in dates:
            i = idx300.get(d)
            if i is None:
                continue
            for h in horizons:
                if i + h < len(cl300):
                    b, tgt = cl300[i][1], cl300[i + h][1]
                    if b and b > 0 and tgt:
                        hs300[h].append((tgt - b) / b * 100)

    return {
        "ok": True,
        "days": len(dates),
        "window": [dates[0], dates[-1]],
        "horizons": list(horizons),
        "mainline_pairs": len(mainline_pairs),
        "mainline": {str(h): _stats(ml[h]) for h in horizons},
        "all_top50": {str(h): _stats(base50[h]) for h in horizons},
        "hs300": {str(h): _stats(hs300[h]) for h in horizons},
        "excess_vs_top50": {str(h): _excess(ml[h], base50[h]) for h in horizons},
        "excess_vs_hs300": {str(h): _excess(ml[h], hs300[h]) for h in horizons},
    }

def _excess(a, b):
    """超额收益差（主组均值 - 基准组均值），任一组无样本返回 None。"""
    if not a or not b:
        return None
    return round(sum(a) / len(a) - sum(b) / len(b), 2)

