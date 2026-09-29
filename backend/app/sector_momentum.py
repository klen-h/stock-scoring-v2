"""
================================================================================
【文件作用】板块动量与异动侦测（2026-09-30，P1）
================================================================================
背景（为什么需要它）：
  2026-09-30 用户问「前几天的地产、今天的金属拉升，项目似乎察觉不到？」
  排查结论（见当日记忆《诊断》）：**不是没数据，是没有规则** ——
    · `plate_daily_zz`（zzshare 104 板块，含涨跌幅/资金/成交额）**已在库**，
      却只被用来算「**板块分化度**」（`sector_zz.dispersion`：std_dev / up_ratio）；
    · `mainline`（行业主线）走的是**评分 Top50 的行业扎堆**，与"板块涨跌幅"是**两回事**
      ⇒ 万科 09-18/09-21 两连涨停时，房地产在 Top50 里只有 **1 只**（价格事实与评分扎堆脱节）。
  ⇒ 本模块补的就是那一层：**从板块价格/资金序列里认出"哪个板块在动"**，
    并输出成日报/复盘可直接引用的事实（描述层，**不是买卖信号**）。

数据源：`plate_daily_zz`（zzshare，`plates_rank` 落库；由日批 `task_sector_snapshot`
        每晚写入 + `task_data_gap` 自愈兜底）。**与东财 `sector_daily` 独立** ——
        东财 push2 长期风控、缺行且不可回补，故本模块只依赖 zzshare。

━━━━━━━━━━━ 口径（先定死，改口径要新开一节并注明日期）━━━━━━━━━━━
· `ret1/ret3/ret5` = 板块**自身涨跌幅的复利累计**（口径 = 板块指数涨跌，
  **不是**成份股等权 —— zzshare 只给板块聚合值）。
· **异动判定 = 双口径，各管一类事实，缺一不可**：
    ① **动量口径**：3 日 ≥ +5.0% 或 5 日 ≥ +8.0%（板块级趋势，慢变量）
    ② **当日相对口径**：当日涨幅居**同日前 5** 且 ≥ +2.0%（"今天最强的方向"，快变量）
  实测为何不能只留一条：只有 ① ⇒ **广普涨日几乎不触发**（09-29 有 82.7% 板块上涨，
  金属类当日 +1.4~2.0% 也进不了任何绝对阈值，正是用户说"察觉不到"的那天）；
  只有 ② ⇒ 每天必然有 5 个"最强"，噪声大、无从判断"是否新起"。
· **`first_day`（新进入）**：连续命中只报**首日**（同 E2 `CLUSTER_GAP_DAYS` /
  `regime_alert` "只在切换当日推一次"的簇去重纪律）。
  实测：`元件` 在 27 个交易日里 **9 天**命中「3 日 ≥+5%」⇒ 不去重必然刷屏。
· **反向同样报**：3 日 ≤ −5.0% ⇒ 弱势异动（"哪个板块在杀跌"与"哪个在拉升"同等重要）。
· `amt_ratio` = 当日成交额 / 前 5 日均成交额（放量倍数）。**不用**成交**占比**：
  `sector_amount_daily`（占比序列）2026-09-30 时**只有 2 天**，且其 taxonomy 是东财最细分
  （与 zzshare 104 不兼容）⇒ 改用板块自身 `trade_money`，口径自洽且立刻可用。

━━━━━━━━━━━ 已知限制（诚实标注，勿粉饰）━━━━━━━━━━━
· **不能关联个股/评分**：zzshare 104 粗分 与 `stock_industry.main_industry`（新浪 49 类）
  实测**只有 4 个同名**（塑料制品/房地产/医疗器械/仪器仪表）⇒ 强行按名称模糊匹配会
  产出**误导性的"板块内 0 只上榜"**（缺失 ≠ 0，与全项目纪律一致）。
  ⇒"板块价格"（本模块）与"评分扎堆"（`mainline`）是**两套 taxonomy**，读者需自行并列看。
· 序列起点 **2026-08-21**（zzshare 接入日）⇒ 当前 ~27 个交易日；
  **样本以"交易日"计**（板块间同期相关极高，104 个板块不是 104 份独立样本）
  ⇒ 做"板块动量能否当因子"的验证需 ≥1 年：见 `scripts/sector_momentum_edge_check.py`
  （**预登记**，先落位、靠日批逐日攒样本）。
· 本模块**只出事实、不推送**：未经上面因子验证前不进推送/决策链（项目纪律：
  新信号先验证"会不会过吵 + 响了有没有用"）。
================================================================================
"""
from typing import Dict, List, Optional

KIND = "industry"
MOM3_STRONG = 5.0          # 3 日累计 ≥ 该值（%）⇒ 动量口径命中
MOM5_STRONG = 8.0          # 5 日累计 ≥ 该值（%）
MOM3_WEAK = -5.0           # 3 日累计 ≤ 该值（%）⇒ 弱势异动
DAY_TOP_N = 5              # 当日相对口径：涨幅前 N
DAY_MIN = 2.0              # 当日相对口径：最低涨幅（%）
SCAN_DAYS = 20             # 扫描/展示窗口（交易日）
AMT_BASE_N = 5             # 放量倍数基准：前 N 日均成交额
# 同日异动 ≥ 该数 ⇒ 判为**系统性**行情（普涨/普跌）而合并成一句，不罗列。
#   实测：09-11 命中 21 项、09-28 命中 23 项（都是普跌日）；日常单日多在 1-8 项。
BULK_N = 8


_CACHE = {"ts": 0.0, "val": None}
_CACHE_TTL = 300.0      # 5 分钟：板块序列一天只变一次（日批 15:10 后落库），无需实时


def _load() -> Dict[str, List[dict]]:
    """一次读全表并按板块名分组（升序）。失败返回 {}（上层当作不可用）。

    ★ 5 分钟进程缓存：全表 ~8k 行、每次 `snapshot()` 至少要它两次（本函数 + `moves_by_date`），
      而**复盘接口每次开页都会调** ⇒ 直接读库会把 egress 放大（本项目 egress 按账号计费）。
      与本模块的"数据一天只变一次"的性质匹配；`refresh` 由 TTL 兜底。
    """
    import time
    now = time.time()
    if _CACHE["val"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["val"]
    from app.database import db
    try:
        rows = db.fetch(
            "SELECT date, name, change_pct, net_inflow, trade_money FROM plate_daily_zz "
            "WHERE kind = %s ORDER BY date", (KIND,))
    except Exception as e:
        print(f"[sector_momentum] load failed: {str(e)[:80]}")     # ASCII（铁律⑥）
        return {}
    by: Dict[str, List[dict]] = {}
    for r in rows or []:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        by.setdefault(name, []).append({
            "date": str(r.get("date"))[:10],
            "chg": float(r.get("change_pct") or 0.0),
            "net": float(r.get("net_inflow") or 0.0),
            "amt": float(r.get("trade_money") or 0.0),
        })
    # 只有**读成功**才写缓存（失败/空结果不缓存 ⇒ 下一轮会重试，别把"库异常"缓存 5 分钟）
    if by:
        _CACHE["val"], _CACHE["ts"] = by, now
    return by


def _cum(seq: List[dict], i: int, n: int) -> Optional[float]:
    """含当日在内、最近 n 根的复利累计涨幅（%）；不足 n 根返回 None（缺失 ≠ 0）。"""
    if i < n - 1:
        return None
    v = 1.0
    for x in seq[i - n + 1:i + 1]:
        v *= (1 + x["chg"] / 100.0)
    return (v - 1) * 100.0


def _streak(seq: List[dict], i: int) -> int:
    """连续同向天数（正=连涨、负=连跌）。"""
    n, v = 0, 0
    while i - n >= 0 and (seq[i - n]["chg"] > 0) == (seq[i]["chg"] > 0):
        n += 1
    return n if seq[i]["chg"] > 0 else -n


def _net3_yi(seq: List[dict], i: int) -> Optional[float]:
    if i < 2:
        return None
    return sum(x["net"] for x in seq[i - 2:i + 1]) / 1e8


def _amt_ratio(seq: List[dict], i: int) -> Optional[float]:
    """当日成交额 / 前 N 日均额；不足返回 None。"""
    if i < AMT_BASE_N:
        return None
    base = sum(x["amt"] for x in seq[i - AMT_BASE_N:i]) / AMT_BASE_N
    return round(seq[i]["amt"] / base, 2) if base > 0 else None


def _metrics(seq: List[dict], i: int, day_rank: Optional[int] = None) -> dict:
    return {
        "date": seq[i]["date"],
        "industry": None,                      # 上层补
        "ret1": round(seq[i]["chg"], 2),
        "ret3": None if (c := _cum(seq, i, 3)) is None else round(c, 2),
        "ret5": None if (c := _cum(seq, i, 5)) is None else round(c, 2),
        "net1_yi": round(seq[i]["net"] / 1e8, 2),
        "net3_yi": None if (n := _net3_yi(seq, i)) is None else round(n, 2),
        "amt_ratio": _amt_ratio(seq, i),
        "streak": _streak(seq, i),
        "day_rank": day_rank,                  # 当日涨幅排名（1=最强）
    }


def _hit(m: dict) -> Optional[str]:
    """是否构成异动 + 原因文案（动量口径 / 当日相对口径）。"""
    r3, r5 = m.get("ret3"), m.get("ret5")
    if r3 is not None and r3 >= MOM3_STRONG:
        return f"3日 {r3:+.1f}%"
    if r5 is not None and r5 >= MOM5_STRONG:
        return f"5日 {r5:+.1f}%"
    if (m.get("day_rank") is not None and m["day_rank"] <= DAY_TOP_N
            and m["ret1"] >= DAY_MIN):
        return f"当日第{m['day_rank']} {m['ret1']:+.1f}%"
    return None


def _weak_hit(m: dict) -> Optional[str]:
    r3 = m.get("ret3")
    if r3 is not None and r3 <= MOM3_WEAK:
        return f"3日 {r3:+.1f}%"
    return None


def _rows_on(by: Dict[str, List[dict]], day: str) -> List[dict]:
    """某日全板块指标（含当日涨幅排名）。"""
    items = []
    for name, seq in by.items():
        i = next((k for k in range(len(seq) - 1, -1, -1) if seq[k]["date"] <= day), None)
        if i is None or seq[i]["date"] != day:
            continue
        items.append((name, seq, i))
    items.sort(key=lambda t: -t[1][t[2]]["chg"])
    out = []
    for rank, (name, seq, i) in enumerate(items, 1):
        m = _metrics(seq, i, day_rank=rank)
        m["industry"] = name
        out.append(m)
    return out


def moves_by_date(days: int = SCAN_DAYS) -> Dict[str, List[dict]]:
    """按日返回**异动首日**清单（连续命中只报首日）。返回 {date: [move...]}。

    移动窗口 = 全序列最后一个日期往前 `days` 个交易日。
    """
    by = _load()
    if not by:
        return {}
    dates = sorted({x["date"] for s in by.values() for x in s})[-days:]
    out: Dict[str, List[dict]] = {}
    prev_hit: Dict[str, bool] = {}
    for d in dates:
        rows = _rows_on(by, d)
        for m in rows:
            hit = _hit(m) or _weak_hit(m)
            if not hit:
                prev_hit[m["industry"]] = False
                continue
            first = not prev_hit.get(m["industry"], False)
            prev_hit[m["industry"]] = True
            if first:
                m = dict(m, reason=hit,
                         kind="weak" if (m.get("ret3") is not None
                                         and m["ret3"] <= MOM3_WEAK) else "strong")
                out.setdefault(d, []).append(m)
        # 同日按强度排序（3 日累计强的在前；不足 3 日的排最后）
        if d in out:
            out[d].sort(key=lambda x: -(x.get("ret3") if x.get("ret3") is not None else -999))
    return out


def snapshot(days: int = SCAN_DAYS) -> dict:
    """最新交易日的板块动量快照（日报/复盘/前端共用）。

    返回 {available, as_of, days, sectors, day_top, strong, weak, moves, note}。
    """
    by = _load()
    if not by:
        return {"available": False, "note": "板块序列不可读（plate_daily_zz 空或库异常）"}
    dates = sorted({x["date"] for s in by.values() for x in s})
    if not dates:
        return {"available": False, "note": "板块序列为空"}
    as_of = dates[-1]
    rows = _rows_on(by, as_of)
    if not rows:
        return {"available": False, "note": f"{as_of} 无板块数据"}
    hits = moves_by_date(days).get(as_of) or []
    ranked = [r for r in rows if r.get("ret3") is not None]
    strong = sorted(ranked, key=lambda x: -x["ret3"])[:6]
    weak = sorted(ranked, key=lambda x: x["ret3"])[:5]
    # 5 日口径榜（周复盘用："本周板块"——与日频的 3 日榜区分开，别看串）
    ranked5 = [r for r in rows if r.get("ret5") is not None]
    strong5 = sorted(ranked5, key=lambda x: -x["ret5"])[:6]
    weak5 = sorted(ranked5, key=lambda x: x["ret5"])[:5]
    day_top = sorted(rows, key=lambda x: -x["ret1"])[:DAY_TOP_N]
    return {
        "available": True, "as_of": as_of, "days": len(dates), "sectors": len(rows),
        "day_top": [{"industry": r["industry"], "ret1": r["ret1"],
                     "net1_yi": r["net1_yi"], "amt_ratio": r["amt_ratio"]}
                    for r in day_top],
        "strong": strong,
        "weak": [r for r in weak if (r.get("ret3") or 0) < 0],
        "strong5": strong5,
        "weak5": [r for r in weak5 if (r.get("ret5") or 0) < 0],
        "moves": hits,
        "note": ("口径：zzshare 104 粗分板块（**与「行业主线」的评分扎堆口径不是一套**）；"
                 f"序列 {len(dates)} 个交易日（自 {dates[0]}）；"
                 "异动=3日≥+5%/5日≥+8% 或当日前5且≥+2%，**连续命中只报首日**；"
                 "本模块只出事实、不推送、不进决策链（因子验证见 "
                 "scripts/sector_momentum_edge_check.py）。"),
    }


def format_lines(res: dict, limit: int = 6) -> List[str]:
    """日报/复盘用的事实行（markdown，无 LLM）。不可用时返回空列表（缺失不占位）。"""
    if not res.get("available"):
        return []
    out = []
    top = res.get("day_top") or []
    if top:
        # ⚠️ 口径说明由调用方在标题里给（日报/复盘都已注明），此处不重复，避免同屏两次
        out.append("**当日涨幅前 5**：" + "、".join(
            f"{r['industry']}({r['ret1']:+.1f}%)" for r in top))
    mv = [m for m in (res.get("moves") or []) if m.get("kind") == "strong"]
    wk = [m for m in (res.get("moves") or []) if m.get("kind") == "weak"]
    bulk = len(mv) + len(wk) >= BULK_N
    if bulk:
        # ★ 批量触发 = **系统性**行情，不是"个别板块异动"。
        #   实测（2026-09-30）：09-11 命中 21 项、09-28 命中 23 项 —— 那两天是**普跌日**，
        #   罗列 20 项既过吵、又把真相（"今天不是选板块的问题，是仓位/β 的问题"）埋掉。
        #   ⇒ 合并成一句 + 只举 3 例；**这本身是更有用的结论**。
        #   ⚠️ 但**不 return** —— 普跌日更要看"3 日动量榜"（还有谁在挺）与弱势 Top5。
        lead = mv if len(mv) >= len(wk) else wk
        flag = "普涨" if len(mv) >= len(wk) else "普跌"
        sample = "、".join(f"{m['industry']}({m['reason']})" for m in lead[:3])
        out.append(f"**板块异动 {len(mv) + len(wk)} 项 ≈ 全市场{flag}**（非个别板块问题，"
                   f"仓位/β 比选板块更重要）：{sample} 等")
    if mv and not bulk:
        out.append("**异动·新进入**（连续命中只报首日）：" + "、".join(
            f"{m['industry']}({m['reason']}"
            + (f"，3日资金{m['net3_yi']:+.1f}亿" if m.get("net3_yi") is not None else "")
            + (f"，量能{m['amt_ratio']}×" if m.get("amt_ratio") else "") + ")"
            for m in mv[:limit]))
    if wk and not bulk:
        out.append("**异动·转弱**：" + "、".join(
            f"{m['industry']}({m['reason']}，3日资金"
            f"{m['net3_yi']:+.1f}亿)" if m.get("net3_yi") is not None
            else f"{m['industry']}({m['reason']})" for m in wk[:4]))
    st = res.get("strong") or []
    if st:
        out.append("**3 日动量榜**：" + "、".join(
            f"{r['industry']}(3日{r['ret3']:+.1f}%/5日"
            f"{('%+.1f' % r['ret5']) if r.get('ret5') is not None else '—'}%)"
            for r in st[:limit]))
    return out
