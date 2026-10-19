# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】行业强度（强势板块判定）—— 评分排行榜「板块联动筛选」的数据层
================================================================================
判据**已冻结**：`PLAN_SECTOR_LINKAGE.md` v1.0（2026-10-19）。
改参数 = 让预登记失效 ⇒ 必须在那份文档里新开一节并说明"不是事后调参"。

核心口径（与冻结文档逐条对应）：
  · 行业维度 = `stock_industry.main_industry`（新浪 49 类，**唯一可与个股精确关联**的
    taxonomy；zzshare 104 板块与它只有 4 个同名 ⇒ 刻意不桥接）；
  · 行业指数 = 行业内成分股**日收益等权均值**（当日无价/停牌剔出分母；缺失≠0）；
  · 强度 = 最近 **5 个交易日**复利累计 `ret5`；
  · 强势 = `ret5 > 0` 且横截面分位 ≥ 70%（Top 30%），且成分股 ≥ 5 只；
  · 普涨守卫 = 连续 3 日「池内上涨家数占比 ≥ 70%」⇒ 当日过滤失效；
  · 任何"取不到/不够" ⇒ **fail-open**（放行 + 显式 reason），绝不产出空榜。

★ 生产路径只在**日批**算一次（`task_sector_strength`），榜单/接口只读表；
  价源 `backtest_prices`（989 只池，池偏差见冻结文档 §6），一次 `load_bars_bulk` 窗口读。
================================================================================
"""
import os
import time
from typing import Dict, List, Optional, Tuple

from app.database import db

# ── 冻结参数（PLAN_SECTOR_LINKAGE.md v1.0）──────────────────────────────────
RET_WINDOW = 5              # 主窗口（交易日）
RET_WINDOW_LONG = 10        # 参考窗口（只展示，不参与判定）
TOP_PCT = 0.30              # 强势分位：前 30%
MIN_MEMBERS = 5             # 成分股少于此数不参评
BULL_RATIO = 0.70           # 普涨守卫：上涨家数占比
BULL_DAYS = 3               # 连续 N 日
MIN_INDUSTRIES = 10         # 参评行业少于此数 ⇒ 数据不足
COVER_MIN = 800             # 某交易日覆盖 < 800 只视为"回填不完整"，不参与计算
STALE_DAYS = 5              # 强度表回退最大自然日跨度

_TABLE = "sector_strength_daily"
_CACHE = {"ts": 0.0, "day": None, "val": None}
_CACHE_TTL = 300.0


# ── 开关 ────────────────────────────────────────────────────────────────────
def mode() -> str:
    """`off|shadow|on`（默认 shadow）。非法值按 shadow 处理并留日志。"""
    v = (os.environ.get("SECTOR_LINKAGE_MODE") or "shadow").strip().lower()
    if v not in ("off", "shadow", "on"):
        print(f"[sector_strength] 非法 SECTOR_LINKAGE_MODE={v!r} ⇒ 按 shadow 处理")
        return "shadow"
    return v


def enabled() -> bool:
    """是否**计算并落库**（off 时连强度表都不写）。"""
    return mode() != "off"


# ── 表 ──────────────────────────────────────────────────────────────────────
def ensure_table() -> None:
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT, industry TEXT,
            ret5 TEXT, ret10 TEXT, rank_pos INTEGER,
            is_strong INTEGER, member_n INTEGER,
            market_up_ratio TEXT, filter_active INTEGER,
            reason TEXT, created_at TEXT,
            PRIMARY KEY (date, industry)
        )
    """)


# ── 计算 ────────────────────────────────────────────────────────────────────
def _industry_of() -> Dict[str, str]:
    from app.industry_map import get_industry_map
    return get_industry_map()


def _code6(x) -> str:
    """归一化成 6 位数字码（`sz159825` / `000001.SZ` → 6 位）后再查行业映射。

    ★ 2026-10-19：`backtest_prices` 里 ETF 带前缀、研究库是 `000001.SZ` 形式，
      不归一化时这些票会被判成"未映射"（在榜单里表现为 fail-open 放行，静默偏松）。
    """
    import re
    digits = re.sub(r"\D", "", str(x or ""))
    return digits[-6:] if len(digits) >= 6 else digits


def _pct(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _cum(seq: List[float]) -> Optional[float]:
    """复利累计（%）；任一日缺失由调用方保证长度足够。"""
    v = 1.0
    for x in seq:
        v *= (1 + x / 100.0)
    return (v - 1) * 100.0


def compute(day: Optional[str] = None, write: bool = True) -> dict:
    """计算某交易日的行业强度并幂等落库。

    参数 `day`：'YYYY-MM-DD'，默认由 `latest_completed_trading_day()` 给出。
    返回：{ok, date, industries, strong, strong_n, rated_n, filter_active, reason,
           market_up_ratio, bull_streak, cost_ms}
    """
    t0 = time.time()
    from app.flash.rules import latest_completed_trading_day
    day = str(day or latest_completed_trading_day())[:10]
    if not enabled():
        return {"ok": True, "date": day, "industries": [], "strong": [],
                "strong_n": 0, "rated_n": 0, "filter_active": False,
                "reason": "mode_off", "market_up_ratio": None, "bull_streak": 0}

    ensure_table()
    ind_map = _industry_of()
    if not ind_map:
        return {"ok": False, "date": day, "reason": "no_industry_map",
                "industries": [], "strong": [], "strong_n": 0, "rated_n": 0,
                "filter_active": False, "market_up_ratio": None, "bull_streak": 0}

    # 一次窗口读：RET_WINDOW_LONG + 余量（含 day 之前的历史）
    # ★ 显式给 `start`：load_bars_bulk 默认回退 400 自然日（为形态扫描设计），
    #   这里只需 14 根 ⇒ 扫描量降一个量级（实测 91s → 秒级）。
    try:
        from datetime import datetime, timedelta
        from app.pattern_launch import load_bars_bulk, latest_trading_date
        _last = latest_trading_date() or day
        try:
            _start = (datetime.strptime(str(_last)[:10], "%Y-%m-%d")
                      - timedelta(days=RET_WINDOW_LONG * 3 + 15)).strftime("%Y-%m-%d")
        except ValueError:
            _start = None
        bars = load_bars_bulk(tail=RET_WINDOW_LONG + 4, start=_start)
    except Exception as e:
        print(f"[sector_strength] 取价失败: {str(e)[:80]}")      # ASCII（铁律⑥）
        bars = {}
    if not bars:
        return {"ok": False, "date": day, "reason": "no_prices",
                "industries": [], "strong": [], "strong_n": 0, "rated_n": 0,
                "filter_active": False, "market_up_ratio": None, "bull_streak": 0}

    # date -> [pct...]（全池，用于普涨守卫）／(industry, date) -> [pct...]
    by_date: Dict[str, List[float]] = {}
    ind_date: Dict[Tuple[str, str], List[float]] = {}
    for code, seq in bars.items():
        ind = ind_map.get(_code6(code))
        for b in seq:
            p = _pct(b.get("pct"))
            if p is None:
                continue
            d = str(b.get("date") or "")[:10]
            if not d or d > day:
                continue
            by_date.setdefault(d, []).append(p)
            if ind:
                ind_date.setdefault((ind, d), []).append(p)

    # 只保留覆盖完整的交易日（回填逐股进行 ⇒ 当天可能只有 ~150 只）
    dates = sorted(d for d, v in by_date.items() if len(v) >= COVER_MIN)
    if len(dates) < RET_WINDOW + 1:
        return {"ok": False, "date": day, "reason": "insufficient_days",
                "industries": [], "strong": [], "strong_n": 0, "rated_n": 0,
                "filter_active": False, "market_up_ratio": None, "bull_streak": 0}

    win5 = dates[-RET_WINDOW:]
    win10 = dates[-RET_WINDOW_LONG:] if len(dates) >= RET_WINDOW_LONG else []

    # 普涨守卫：最近 BULL_DAYS 日的上涨家数占比
    up_ratios = []
    for d in dates[-BULL_DAYS:]:
        vals = by_date[d]
        up_ratios.append(sum(1 for x in vals if x > 0) / len(vals) if vals else 0.0)
    bull_streak = len(up_ratios) if (len(up_ratios) == BULL_DAYS
                                     and all(r >= BULL_RATIO for r in up_ratios)) else 0
    today_up_ratio = up_ratios[-1] if up_ratios else None

    # 各行业 ret5 / ret10（要求窗口内每天都有数据，否则不参评）
    rows = []
    for ind in sorted({k[0] for k in ind_date}):
        seq5, seq10, ok5, ok10 = [], [], True, True
        for d in win5:
            vals = ind_date.get((ind, d)) or []
            if not vals:
                ok5 = False
                break
            seq5.append(sum(vals) / len(vals))
        if win10:
            for d in win10:
                vals = ind_date.get((ind, d)) or []
                if not vals:
                    ok10 = False
                    break
                seq10.append(sum(vals) / len(vals))
        member_n = len(ind_date.get((ind, dates[-1])) or [])
        rows.append({"industry": ind,
                     "ret5": round(_cum(seq5), 2) if ok5 else None,
                     "ret10": round(_cum(seq10), 2) if (ok10 and win10) else None,
                     "member_n": member_n})

    rated = [r for r in rows if r["ret5"] is not None and r["member_n"] >= MIN_MEMBERS]
    rated.sort(key=lambda r: -r["ret5"])
    for i, r in enumerate(rated):
        r["rank_pos"] = i + 1

    if len(rated) < MIN_INDUSTRIES:
        strong, reason, active = [], "insufficient_industries", False
    elif bull_streak >= BULL_DAYS:
        strong, reason, active = [], "bull_market", False
    else:
        k = max(1, int(round(len(rated) * TOP_PCT + 0.4999)))
        strong = [r["industry"] for r in rated[:k] if (r["ret5"] or 0) > 0]
        if not strong:
            reason, active = "no_strong", False
        else:
            reason, active = "ok", True

    cost = int((time.time() - t0) * 1000)
    out = {"ok": True, "date": day, "industries": rated, "strong": strong,
           "strong_n": len(strong), "rated_n": len(rated),
           "filter_active": active, "reason": reason,
           "market_up_ratio": (round(today_up_ratio * 100, 1)
                               if today_up_ratio is not None else None),
           "bull_streak": bull_streak, "cost_ms": cost}

    if write:
        try:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            for r in rated:
                db.upsert(_TABLE, {
                    "date": day, "industry": r["industry"],
                    "ret5": str(r["ret5"]), "ret10": str(r["ret10"]) if r["ret10"] is not None else None,
                    "rank_pos": int(r["rank_pos"]), "is_strong": 1 if r["industry"] in strong else 0,
                    "member_n": int(r["member_n"]),
                    "market_up_ratio": str(out["market_up_ratio"]) if out["market_up_ratio"] is not None else None,
                    "filter_active": 1 if active else 0,
                    "reason": reason, "created_at": now,
                }, conflict_columns=["date", "industry"])
        except Exception as e:
            print(f"[sector_strength] 落库失败: {str(e)[:100]}")
            out["ok"] = False
            out["write_error"] = str(e)[:100]
        _CACHE.update(ts=0.0, day=None, val=None)

    print(f"[sector_strength] {day} 评 {len(rated)} 行业 / 强势 {len(strong)}"
          f"（{reason}，上涨占比 {out['market_up_ratio']}%，{cost}ms）")
    return out


def _read_rows(day: str) -> Tuple[List[dict], str]:
    """读某日强度行；当日无行则**回退到最近一次**（≤ day，且不超 STALE_DAYS 自然日）。

    回退是刻意的：盘中/日批前，榜单需要一份"最近已知"的行业强度（用上一交易日口径，
    本身就是**避免前视**的正确做法）。超过 STALE_DAYS ⇒ 视为不可用（fail-open）。
    """
    ensure_table()
    rows = db.fetch(f"SELECT * FROM {_TABLE} WHERE date = %s", (day,)) or []
    as_of = day
    if not rows:
        r = db.fetch_one(
            f"SELECT date FROM {_TABLE} WHERE date <= %s ORDER BY date DESC LIMIT 1", (day,))
        if not r:
            return [], ""
        as_of = str(r.get("date"))[:10]
        from datetime import datetime
        try:
            gap = (datetime.strptime(day, "%Y-%m-%d")
                   - datetime.strptime(as_of, "%Y-%m-%d")).days
        except ValueError:
            gap = 999
        if gap > STALE_DAYS:
            return [], ""
        rows = db.fetch(f"SELECT * FROM {_TABLE} WHERE date = %s", (as_of,)) or []
    return rows, as_of


def load(day: Optional[str] = None) -> dict:
    """读当日强度（带 5 分钟缓存）。返回：
    {available, as_of, active, reason, strong[], industries[], strong_n, market_up_ratio}
    """
    from app.flash.rules import latest_completed_trading_day
    day = str(day or latest_completed_trading_day())[:10]
    now = time.time()
    if (_CACHE["val"] is not None and _CACHE["day"] == day
            and now - _CACHE["ts"] < _CACHE_TTL):
        return _CACHE["val"]

    rows, as_of = _read_rows(day)
    if not rows:
        val = {"available": False, "as_of": None, "active": False,
               "reason": "unavailable", "strong": [], "industries": [],
               "strong_n": 0, "market_up_ratio": None}
    else:
        first = rows[0]
        industries = sorted(
            ({"industry": r.get("industry"), "ret5": _pct(r.get("ret5")),
              "ret10": _pct(r.get("ret10")), "rank_pos": r.get("rank_pos"),
              "is_strong": bool(r.get("is_strong")), "member_n": r.get("member_n")}
             for r in rows),
            key=lambda x: (x["rank_pos"] is None, x["rank_pos"] or 999))
        strong = [x["industry"] for x in industries if x["is_strong"]]
        val = {"available": True, "as_of": as_of,
               "active": bool(first.get("filter_active")),
               "reason": first.get("reason") or "ok",
               "strong": strong, "industries": industries,
               "strong_n": len(strong),
               "market_up_ratio": _pct(first.get("market_up_ratio"))}
    _CACHE.update(ts=now, day=day, val=val)
    return val


# ── 榜单过滤（排名可见性层；不动分数）────────────────────────────────────────
def _code_of(x) -> str:
    """兼容 dict（ranking_live/在线路径的 dict）与对象（ScoreResult/SimpleNamespace）。"""
    if isinstance(x, dict):
        return str(x.get("code") or "")
    return str(getattr(x, "code", "") or "")


def filter_ranked(stocks: list, day: Optional[str] = None,
                  limit: Optional[int] = None) -> Tuple[list, dict]:
    """按强势行业过滤**已排序**的个股列表，并补满 `limit`。

    规则（冻结文档 §3）：
      · off / 表不可用 / 过滤失效 ⇒ 原样返回（fail-open，不产空榜）；
      · 映射缺失的个股 **保留**（缺失 ≠ 不满足），计入 `unmapped_n`；
      · 强势行业内不足 limit ⇒ 用其余个股按原顺序补足，标 `short=true`。
    """
    m = mode()
    total = len(stocks)
    lim = int(limit or total or 0)
    meta = {"mode": m, "active": False, "reason": "mode_off", "as_of": None,
            "strong_n": 0, "excluded_n": 0, "backfilled_n": 0,
            "unmapped_n": 0, "short": False, "total": total}
    if m == "off":
        return stocks[:lim], meta
    if not stocks:
        meta["reason"] = "empty"
        return [], meta

    st = load(day)
    meta.update({"as_of": st.get("as_of"), "strong_n": st.get("strong_n", 0),
                 "reason": st.get("reason") or "unavailable"})
    if not st.get("available") or not st.get("active"):
        return stocks[:lim], meta                 # fail-open
    strong = set(st.get("strong") or [])
    if not strong:
        return stocks[:lim], meta

    ind_map = _industry_of()
    base_codes = {_code_of(s) for s in stocks[:lim]}
    kept, rest = [], []
    unmapped = 0
    for s in stocks:
        code = _code_of(s)
        ind = ind_map.get(_code6(code))
        if not ind:
            unmapped += 1
            kept.append(s)                        # 缺失 ⇒ 放行
        elif ind in strong:
            kept.append(s)
        else:
            rest.append(s)

    final = kept[:lim]
    short = len(final) < lim
    if short:
        final = final + rest[:lim - len(final)]

    meta.update({
        "active": True,
        "excluded_n": len(rest),
        "unmapped_n": unmapped,
        "backfilled_n": sum(1 for s in final if _code_of(s) not in base_codes),
        "short": short,
    })
    return final, meta


# ── 过滤版榜单落库（平行表，原 ranking_history 一字不动）─────────────────────
_RANK_TABLE = "ranking_history_sector"


def ensure_ranking_table() -> None:
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_RANK_TABLE} (
            rank_date TEXT, code TEXT, name TEXT, rank_pos INTEGER,
            total_score REAL, signal TEXT, sector_reason TEXT, created_at TEXT,
            PRIMARY KEY (rank_date, code)
        )
    """)


def save_ranking(day: str, items: list, reason: str = "") -> int:
    """写过滤版 Top N（先删当日再插，幂等）。items 支持 dict/对象。"""
    ensure_ranking_table()
    day = str(day)[:10]
    try:
        db.execute(f"DELETE FROM {_RANK_TABLE} WHERE rank_date = %s", (day,))
    except Exception as e:
        print(f"[sector_strength] 清理过滤版榜单失败（继续）: {str(e)[:80]}")
    payload = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for i, x in enumerate(items):
        if isinstance(x, dict):
            code, name = x.get("code"), x.get("name")
            score, sig = x.get("total_score"), x.get("signal")
            rank = x.get("rank") or x.get("rank_pos") or (i + 1)
        else:
            code, name = getattr(x, "code", None), getattr(x, "name", None)
            score = getattr(x, "total_score", None)
            sig = getattr(x, "signal", None)
            rank = getattr(x, "rank", None) or (i + 1)
        if not code:
            continue
        payload.append({"rank_date": day, "code": str(code), "name": name,
                        "rank_pos": int(i + 1), "total_score": score,
                        "signal": sig, "sector_reason": reason, "created_at": now})
    if not payload:
        return 0
    try:
        return db.upsert_many(_RANK_TABLE, payload, ["rank_date", "code"])
    except Exception as e:
        print(f"[sector_strength] 过滤版榜单写入失败: {str(e)[:80]}")
        return 0


def snapshot_from_live(day: Optional[str] = None) -> str:
    """日批任务：用当日 `ranking_live` 全量榜 + 强度表，生成过滤版 Top50 落平行表。

    ★ 只在 shadow/on 模式下写（off 连表都不建）；强度表不可用 ⇒ 跳过（fail-open，
      不留"空榜"这种会误导回查的数据）。
    """
    from app.flash.rules import latest_completed_trading_day
    day = str(day or latest_completed_trading_day())[:10]
    if not enabled():
        return "板块联动榜: mode=off，跳过"
    st = load(day)
    if not st.get("available"):
        return f"板块联动榜: 强度表不可用（{st.get('reason')}），跳过"
    from app.scoring.live_ranking import load as _load_live
    live = _load_live(limit=1000, use_cache=False)
    rows = live.get("data") or []
    if not rows:
        return "板块联动榜: ranking_live 为空，跳过"
    # 只用当日榜：日批正常路径下 rank_live 在前一步已写当日
    picked, meta = filter_ranked(rows, day=day, limit=50)
    n = save_ranking(day, picked, meta.get("reason") or "")
    return (f"板块联动榜: {n} 条（active={meta['active']} "
            f"排除 {meta['excluded_n']} / 递补 {meta['backfilled_n']} / "
            f"未映射 {meta['unmapped_n']}）")
