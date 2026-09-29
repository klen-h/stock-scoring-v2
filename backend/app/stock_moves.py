"""
================================================================================
【文件作用】个股级"放量拉升"侦测 + 行业聚合（2026-09-30，P1 续）
================================================================================
背景（为什么需要它）：P1 交付了 `sector_momentum`（**板块层**侦测），但实测留下一个洞 ——
  2026-09-29 用户感知到"金属拉升"（云铝 **+4.34%**、赣锋 **+3.53%**、中国铝业 +3.00%），而：
    · 板块层：zzshare 104 粗分下 工业金属仅 +2.02%、能源金属 +1.60% ⇒ **进不了任何异动阈值**；
    · 涨停层：这几只**都没涨停** ⇒ `zz_daily_snapshots.uplimit_stocks` / 工作台「涨停复盘」
      只覆盖**封板股** ⇒ **看不到它们**。
  ⇒ 两个既有视角之间存在空白，本模块补的就是这一层。

★ 与既有能力的分工（**别重复造**）
  · 板块层（哪个板块在动）→ `app/sector_momentum.py`（104 粗分板块，只看板块指数）
  · 封板结构层（连板梯队 / 题材归因）→ `/api/market/limit-review`（zzshare，工作台「涨停复盘」卡）
    ⚠️ 该卡给的是**结构**（几进几、题材扎堆），实测个股清单**不完整**（上游有重复行，去重后
       常只剩个位数）⇒ 本模块**纳入涨停股**并标记，补上"完整名录"这一维度（**维度不同不算重复**）。
  · **本模块 = 全市场"显著上涨 + 显著成交"的个股名录 + 行业聚合** ⇒ 直接回答
    "今天**哪个行业**有一批股票在放量上攻"（= 用户说的"金属拉升"那种感知）。
  ★ 本模块**不依赖 zzshare 板块名对齐**（那是 P1 的死结：104 粗分 vs 新浪 49 类仅 4 个同名）——
    个股自带 `stock_industry.main_industry` 标签 ⇒ 行业聚合天然可用。

━━━━━━━━━━ 口径（描述层；阈值是**经验值**、可调，非预登记判据）━━━━━━━━━━
数据源：`market_snapshot`（腾讯 15:05 收盘快照落库；实测 5251 只、字段 100% 覆盖，
        含 `change_pct` / `turnover_rate` / `amount` / `float_cap` / `amplitude` / `open`）。
入选：`change_pct ≥ MIN_CHG`（默认 +3.0%）且非新股/异常、非 ST/退，然后分两类：
  ① **涨停股**（按板幅判：主板 9.8 / 创业板·科创板 19.8 / 北交所 29.8）⇒ **直接纳入**
     —— 涨停当日常缩量（一字板尤其）⇒ 不设量能门槛；标 `limit_up`，一字板另标 `one_word`
     （振幅 ≈0 或 开=高=低=收 ⇒ **买不进**，诚实标注，别让人以为是机会）。
  ② **非涨停** ⇒ 需通过**放量双路径**（见下）。
【★ 为什么放量要"双路径"】首版只要求 换手率 ≥5%，实测**系统性歧视大市值**（2026-09-29 标定）：
  涨幅≥3% 且流通≥300亿的 40 只**换手均值仅 3.52%**（≥5% 只剩 8 只）；流通<100亿的 371 只
  均值 **6.10%**（≥5% 有 154 只）⇒ 云铝（换手仅 **1.97%**、成交额却 **17.7亿**）被误杀。
  ⇒ 改用**或**：`turnover ≥ MIN_TURN(5%)`（小盘口径）**或**
     `amount ≥ MIN_AMOUNT_YI(15亿) 且 turnover ≥ MIN_TURN_BIG(1.5%)`（大盘口径，防"巨额但常态"）。
  ⚠️ 为什么不用"量比/5 日均量"：那需历史成交量，而 `market_snapshot` 只存**最新一份**（单行）、
     `backtest_prices` 只覆盖池内 ⇒ 用不了。换手率/成交额由快照直接给出、零依赖。
聚合：按 `stock_industry.main_industry`（最细子板块，一票一行业不重复计）⇒
     同行业 ≥ `MIN_GROUP`（2）只 ⇒ 标 `is_sector=True`（"行业性放量"）。
⚠️ **描述层，非买卖信号**：不进决策链、不推送、不改仓位/闸门（与 `sector_momentum` 同纪律）。
================================================================================
"""
from typing import Dict, List

MIN_CHG = 3.0            # 涨幅门槛（%）
MIN_TURN = 5.0           # 放量路径①：换手率 ≥（%）—— 小盘口径
MIN_AMOUNT_YI = 15.0     # 放量路径②：成交额 ≥（亿元）—— 大盘口径
MIN_TURN_BIG = 1.5       # 放量路径②的换手下限（%）：防"巨额但常态"（如指标股）
MIN_CAP_YI = 50.0        # 流通市值门槛（亿元）：剔微盘噪音
MIN_GROUP = 2            # 同行业 ≥ 该只数 ⇒ 标"行业性"
MAX_ABS_CHG = 40.0       # |涨幅| > 该值 ⇒ 视作新股/异常，剔除（实测上市首日 +653%）
MAX_AMPLITUDE = 60.0     # 振幅 > 该值 ⇒ 同上
_CACHE = {"ts": 0.0, "val": None}
_CACHE_TTL = 300.0       # 5 分钟（快照一天只变一次；日报/复盘/接口可能连读 ⇒ 护 egress）


def _limit_pct(code: str) -> float:
    """该股票的涨停幅度（%）：主板 10 / 创业板·科创板 20 / 北交所 30。"""
    c = str(code)
    if c.startswith(("300", "301", "688", "689")):
        return 20.0
    if c.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def _is_limit_up(code: str, chg: float) -> bool:
    """是否涨停（留 0.2pp 余量，避开 9.97/19.98 这类精度差）。"""
    return chg >= _limit_pct(code) - 0.2


def _industry_map() -> Dict[str, str]:
    """{code: main_industry}（最细子板块）。失败返回 {}（则聚合为空，只出个股名录）。"""
    from app.database import db
    try:
        rows = db.fetch("SELECT code, main_industry FROM stock_industry")
        return {str(r["code"]): str(r["main_industry"])
                for r in (rows or []) if r.get("main_industry")}
    except Exception as e:
        print(f"[stock_moves] industry map failed: {str(e)[:70]}")    # ASCII（铁律⑥）
        return {}


def snapshot(force: bool = False) -> dict:
    """当日"放量拉升个股"名录 + 行业聚合。

    返回 {available, as_of, scanned, n, limit_up_n, items, by_industry, note}
    每次读一次 `market_snapshot`（单行 ~1.8MB 文本）⇒ 5 分钟 TTL 缓存。
    """
    import time
    now = time.time()
    if not force and _CACHE["val"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["val"]
    from app.flash import store
    snap = store.load_market_snapshot() or {}
    stocks = snap.get("stocks") or {}
    if isinstance(stocks, list):
        stocks = {s.get("code"): s for s in stocks}
    if not stocks:
        return {"available": False, "note": "收盘快照未就绪（market_snapshot 为空）"}
    as_of = str(snap.get("saved_at") or "")[:19]
    ind_map = _industry_map()

    items = []
    for code, s in stocks.items():
        try:
            name = str(s.get("name") or "").strip()
            chg = float(s.get("change_pct") or 0.0)
            turn = float(s.get("turnover_rate") or 0.0)
            cap_yi = float(s.get("float_cap") or 0.0) / 1e4      # 万元 → 亿元
            amp = float(s.get("amplitude") or 0.0)
            price = float(s.get("price") or 0.0)
            amount_yi = float(s.get("amount") or 0.0) / 1e8
        except (TypeError, ValueError):
            continue
        if not price or not cap_yi:
            continue                       # 停牌/无价（如 PT 股）
        if chg < MIN_CHG:
            continue
        if abs(chg) > MAX_ABS_CHG or amp > MAX_AMPLITUDE:
            continue                       # 新股上市初期/异常
        if "ST" in name.upper() or "退" in name:
            continue
        if cap_yi < MIN_CAP_YI:
            continue                       # 微盘噪音
        is_lu = _is_limit_up(code, chg)
        # 涨停 ⇒ 直接纳入（当日常缩量）；非涨停 ⇒ 需通过放量双路径
        if not is_lu and not (turn >= MIN_TURN
                              or (amount_yi >= MIN_AMOUNT_YI and turn >= MIN_TURN_BIG)):
            continue
        row = {
            "code": str(code), "name": name, "chg": round(chg, 2),
            "turnover": round(turn, 2), "cap_yi": round(cap_yi, 1),
            "amount_yi": round(amount_yi, 2),
            "industry": ind_map.get(str(code)) or "",
            "limit_up": is_lu,
        }
        if is_lu:
            # 一字板判据：振幅≈0，或 开=高=低=收（**买不进**，如实标注）
            o, h, low = (float(s.get(k) or 0) for k in ("open", "high", "low"))
            row["one_word"] = (amp <= 0.01 or (o and o == h == low == price))
        items.append(row)
    items.sort(key=lambda x: (-1 if x["limit_up"] else 0, -x["chg"]))

    groups: Dict[str, List[dict]] = {}
    for it in items:
        if it["industry"]:
            groups.setdefault(it["industry"], []).append(it)
    by_industry = [{
        "industry": ind, "n": len(v),
        "avg_chg": round(sum(x["chg"] for x in v) / len(v), 2),
        "limit_up_n": sum(1 for x in v if x["limit_up"]),
        "stocks": [f"{x['name']}({x['chg']:+.1f}%)" for x in v[:6]],
        "is_sector": len(v) >= MIN_GROUP,
    } for ind, v in groups.items()]
    by_industry.sort(key=lambda x: (-x["n"], -x["avg_chg"]))

    res = {
        "available": True, "as_of": as_of, "scanned": len(stocks), "n": len(items),
        "limit_up_n": sum(1 for x in items if x["limit_up"]),
        "items": items, "by_industry": by_industry,
        "unmapped": sum(1 for x in items if not x["industry"]),
        "note": (f"口径：涨幅 ≥{MIN_CHG:.0f}%，且（**涨停**）或（非涨停但放量：换手 "
                 f"≥{MIN_TURN:.0f}%，或 成交额 ≥{MIN_AMOUNT_YI:.0f}亿且换手 "
                 f"≥{MIN_TURN_BIG:.1f}%），流通市值 ≥{MIN_CAP_YI:.0f}亿、剔新股/ST。"
                 f"⚠️ 大盘股换手天然低 ⇒ 故设「成交额」路径（实测大票换手均值仅 3.5%，"
                 f"单用换手会误杀）；换手是绝对活跃度、不含个股自身量比（快照不存量史）。"
                 f"**描述层事实，非买卖信号**；涨停的**连板梯队/题材归因**见工作台「涨停复盘」。"),
    }
    if items:
        _CACHE["val"], _CACHE["ts"] = res, now       # 空结果不缓存（快照未就绪时下轮重试）
    return res


def format_lines(res: dict, limit: int = 5) -> List[str]:
    """日报用事实行（markdown，无 LLM）。不可用 ⇒ 空列表（缺失不占位）。"""
    if not res.get("available") or not res.get("n"):
        return []
    out = []
    sec = [g for g in (res.get("by_industry") or []) if g.get("is_sector")]
    if sec:
        out.append("**行业性上攻（同行业 ≥2 只）**：" + "；".join(
            f"{g['industry']} {g['n']} 只（均 {g['avg_chg']:+.1f}%"
            + (f"，涨停 {g['limit_up_n']}" if g.get("limit_up_n") else "")
            + "）：" + "、".join(g["stocks"][:4]) for g in sec[:limit]))
    named = {g["industry"] for g in sec[:limit]}
    rest = [x for x in (res.get("items") or []) if x.get("industry") not in named]
    if rest:
        out.append(f"**其他放量拉升**（全市场共 {res['n']} 只，含涨停 {res.get('limit_up_n', 0)} 只）："
                   + "、".join(f"{x['name']}({x['chg']:+.1f}%，换手{x['turnover']:.1f}%)"
                               for x in rest[:8]))
    ow = [x for x in (res.get("items") or []) if x.get("one_word")]
    if ow:
        out.append("⚠️ **一字板**（涨停但买不进，仅记录）："
                   + "、".join(f"{x['name']}" for x in ow[:8]))
    return out
