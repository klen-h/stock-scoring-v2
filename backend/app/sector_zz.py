# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】zzshare 板块数据源 —— **独立体系**的板块快照 / 分化度
================================================================================
【为什么独立成表，而不是并入 `sector_daily`（东财）】
  ★★ 两套板块 taxonomy **完全不兼容**（2026-09-27 实测）：
    · 东财：`BK1325` 前缀，**496 个**细分行业（带 Ⅱ/Ⅲ 后缀），如「半导体材料」「非金属材料Ⅲ」
    · zzshare：`881101` 前缀，**104 个**粗分行业（同花顺体系），如「种植业与林业」「煤炭开采加工」
  ⇒ 若混入同一张表，同一个板块会出现两个 key、历史序列直接断裂 ——
    正是 `sector_industry.take_snapshot()` 当初**刻意跳过新浪**所避免的坑。
  ⇒ 故本模块用**独立表 `plate_daily_zz`**，与东财 `sector_daily` 并存，互不干扰。

【为什么不直接用东财（而是新增 zzshare 源）】
  东财 push2 长期不稳（封 IP 24~48h），而快照**一旦错过当天就永久缺失**（clist 只给当前快照，
  历史无法回补）⇒ 实测 `sector_daily` 停在 2026-09-23（09-24 东财被封，当天快照丢失）。
  而 zzshare `plates_rank(plate_type, date1)` **支持任意历史日期** ⇒ 既可每日积累，**也可回填**。

【数据源】`app/zzshare_client.get_api().plates_rank(plate_type, date1, limit)`
  · plate_type：**14=行业、15=概念**（17=?；传 1/2 会 HTTP 400）
  · 字段：rate(涨跌幅%) / money_leader(主力净流入元) / trade_money(成交额元) /
          market_cap_cir(流通市值) / score(板块评分) / speed(涨速) / volume_ration
  · ⚠️ zzshare **无** 涨跌家数 / 领涨股（东财独有）—— 本表也不存这两项。

【接口】
  take_snapshot(date)      记录某交易日快照（幂等，可回填任意历史日）
  get_snapshot(date, kind) 某日全部板块（按涨跌幅降序）
  dispersion(date, kind)   板块分化度（涨跌幅标准差 / 最强最弱差距 / 上涨板块占比）
  stats()                  快照表概况（已积累天数 / 最新日期）
================================================================================
"""
import time
from typing import List, Optional

from app.database import db

# plate_type 映射（zzshare 既定值，传错会 400）
_KIND2PT = {"industry": 14, "concept": 15}
_TABLE_READY = False


def _ensure_table() -> None:
    """板块快照表（独立于东财 sector_daily；幂等）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS plate_daily_zz (
            date TEXT NOT NULL,
            kind TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            change_pct REAL,
            net_inflow REAL,
            trade_money REAL,
            market_cap_cir REAL,
            score INTEGER,
            speed REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, kind, code)
        )
    """)
    _TABLE_READY = True


def fetch_rank(kind: str = "industry", date: str = None) -> list:
    """拉某交易日的板块排名（zzshare 原生字段）。失败返回 []。"""
    from app.zzshare_client import get_api
    from app.flash.rules import latest_completed_trading_day
    pt = _KIND2PT.get(kind, 14)
    d = date or latest_completed_trading_day()
    try:
        rows = get_api().plates_rank(plate_type=pt, date1=d, limit=200) or []
    except Exception as e:
        print(f"[sector_zz] plates_rank 失败 kind={kind} date={d}: {e}")   # ASCII（铁律⑥）
        return []
    out = []
    for r in rows:
        code = str(r.get("plate_code") or "").strip()
        if not code:
            continue
        out.append({
            "code": code,
            "name": r.get("plate_name") or "",
            "change_pct": float(r.get("rate") or 0.0),
            "net_inflow": float(r.get("money_leader") or 0.0),
            "trade_money": float(r.get("trade_money") or 0.0),
            "market_cap_cir": float(r.get("market_cap_cir") or 0.0),
            "score": int(r.get("score") or 0),
            "speed": float(r.get("speed") or 0.0),
        })
    return out


def take_snapshot(date: str = None, kinds: tuple = ("industry", "concept")) -> dict:
    """记录某交易日板块快照（幂等，可回填任意历史日）。

    返回 {date, written, kinds:{kind: n}, skipped:[...]}
    """
    from app.flash.rules import latest_completed_trading_day
    d = date or latest_completed_trading_day()
    out = {"date": d, "written": 0, "kinds": {}, "skipped": []}
    _ensure_table()
    for kind in kinds:
        rows = fetch_rank(kind, d)
        if not rows:
            out["skipped"].append(f"{kind}: 空（zzshare 无数据或限流）")
            continue
        n = 0
        for s in rows:
            try:
                db.upsert("plate_daily_zz", {
                    "date": d, "kind": kind, "code": s["code"], "name": s["name"],
                    "change_pct": s["change_pct"], "net_inflow": s["net_inflow"],
                    "trade_money": s["trade_money"], "market_cap_cir": s["market_cap_cir"],
                    "score": s["score"], "speed": s["speed"],
                }, conflict_columns=["date", "kind", "code"])
                n += 1
            except Exception as e:
                print(f"[sector_zz] 写入失败 {kind}/{s['code']}: {e}")
        out["kinds"][kind] = n
        out["written"] += n
    return out


def get_snapshot(date: str, kind: str = "industry", limit: int = 500) -> list:
    """某交易日全部板块快照（按涨跌幅降序）。"""
    _ensure_table()
    return db.fetch(
        "SELECT * FROM plate_daily_zz WHERE date = %s AND kind = %s "
        "ORDER BY change_pct DESC LIMIT %s", (date, kind, limit)) or []


def get_history(code: str, days: int = 60) -> list:
    """单板块历史序列（日期正序，最新在后）—— 对应东财版 `/sector/history/{code}`。"""
    _ensure_table()
    rows = db.fetch("SELECT * FROM plate_daily_zz WHERE code = %s "
                    "ORDER BY date DESC LIMIT %s", (code, days)) or []
    return list(reversed(rows))


def ma_state(code: str, kind: str = "industry", days: int = 60) -> dict:
    """板块 MA20 状态 + **有效信号判定**（等价点位口径）。

    ★★ 2026-11-23：`plate_daily_zz` 表**只有 change_pct，没有收盘点位**。
      但用 change_pct 从旧到新**累乘构造等价点位**（设最新 = 1000），
      算出的「距 MA20」与真实点位**几乎完全一致**：

        881168 累乘口径  -8.19%
        雪球 BK0068 真实 -8.20%      ⇒ 差 0.01pp

      ⇒ **不必新增数据源/新表**，现有数据就够。（比例类指标与基准无关）

    ★ 信号口径来自 800 根回测（见 memory 2026-11-23）：
      「站上 MA20 且当日涨 ≥1.5%」T+20 +2.41%、胜率 62%，是**全场最佳**；
      而「缩量企稳」T+20 -0.76%、胜率 43%，**是负向信号**（下跌中缩量 = 没人接盘）。
      ⇒ 本函数只认「收复 MA20」，**不认缩量**。
    """
    if days < 21:
        return {"ok": False, "error": "days 至少 21（算 MA20 要 20 根 + 当日）"}
    rows = get_history(code, days)
    calc = _calc_ma(rows)
    if not calc:
        return {"ok": False, "code": code,
                "error": f"历史不足 21 天（当前 {len(rows)} 天，需每天落库积累）",
                "bars": len(rows)}
    out = {"ok": True, "code": code, "kind": kind,
           "caliber": "等价点位口径（由 change_pct 累乘，最新=1000）；比例类指标与基准无关"}
    out.update(calc)
    return out


def _calc_ma(rows: list) -> Optional[dict]:
    """**纯计算**：给定按日期升序的板块序列，算 MA20 状态。不足 21 根返回 None。

    ★ 2026-11-23：`ma_state`（单板块）与 `ma_scan`（全板块）**共用这一份**。
      项目踩过 `_bench_code` / `SPREAD_THRESHOLD` 两处各写一份漂移的事故，
      所以宁可多抽一层。
    """
    if not rows or len(rows) < 21:
        return None

    # ── 等价点位：设最新 = 1000，从旧到新累乘 ──
    lvl = [1000.0]
    for x in rows[1:]:
        pct = x.get("change_pct")
        try:
            pct = float(pct) if pct is not None else 0.0
        except (TypeError, ValueError):
            pct = 0.0
        lvl.append(lvl[-1] * (1 + pct / 100.0))
    if not lvl[-1]:
        return None
    k = 1000.0 / lvl[-1]
    lvl = [v * k for v in lvl]

    cur = lvl[-1]
    ma20 = sum(lvl[-20:]) / 20.0

    # ── 量比：当日成交额 / 前 20 日均额 ──
    amts = []
    for x in rows:
        try:
            amts.append(float(x.get("trade_money") or 0))
        except (TypeError, ValueError):
            amts.append(0.0)
    amt_ratio = None
    if len(amts) >= 21:
        base = sum(amts[-21:-1]) / 20.0
        if base > 0 and amts[-1] > 0:
            amt_ratio = round(amts[-1] / base, 2)

    ret1 = None
    try:
        ret1 = round(float(rows[-1].get("change_pct")), 2)
    except (TypeError, ValueError):
        pass

    return _judge(cur, ma20, ret1, amt_ratio,
                  name=rows[-1].get("name"), date=rows[-1].get("date"),
                  bars=len(rows))


def _judge(cur: float, ma20: float, ret1, amt_ratio,
           name=None, date=None, bars: int = 0) -> dict:
    """由**已算好的**点位/均线/涨幅/量比 → 判定结果。

    ★ 2026-11-23：`_calc_ma`（内存逐根）与 `ma_scan`（SQL 聚合下推）**共用这一份**，
      否则两条路径的 signal/note 会漂移（项目同类事故已发生过两次）。
    """
    dist = (cur / ma20 - 1) * 100 if ma20 else None
    on_ma = bool(ma20 and cur > ma20)
    # 回测最佳信号：站上 MA20 **且** 当日涨 ≥1.5%（放量不是必要条件）
    signal = bool(on_ma and ret1 is not None and ret1 >= 1.5)

    if signal:
        note = "站上MA20 且当日涨≥1.5% —— 回测 T+20 +2.41% / 胜率 62%"
    elif on_ma:
        note = "在 MA20 上方，但当日涨幅不足 1.5%"
    elif ret1 is not None and ret1 >= 1.5:
        note = "当日反弹但仍在 MA20 下方 —— 按回测不构成有效信号"
    else:
        note = "MA20 下方且无有效反弹"

    return {
        "name": name, "date": date, "bars": bars,
        "level": round(cur, 2),          # 等价点位（最新=1000），**不是真实点位**
        "ma20": round(ma20, 2),
        "dist_pct": round(dist, 2) if dist is not None else None,
        "amt_ratio": amt_ratio,
        "ret1": ret1,
        "on_ma20": on_ma,
        "signal": signal,
        "note": note,
    }


# ★★ 2026-11-23：全板块扫描缓存。
#   【为什么必须缓存】104 个板块逐个 `ma_state()` = 104 次往返；
#     1 次全表查询虽然行数相近（104×60≈6k 行），但**往返次数少 100 倍**，
#     且加上 5 分钟缓存后，一天最多算 288 次 → 实际远低于此。
#   【TTL 5 分钟】板块序列一天只变一次（收盘后日批落库），与 `sector_momentum._CACHE` 同口径。
_SCAN_CACHE = {"ts": 0.0, "key": None, "val": None}
_SCAN_TTL = 300.0


def ma_scan(kind: str = "industry", days: int = 60) -> dict:
    """**全板块 MA20 扫描** —— 一次查库、内存分组，返回触发/接近 MA20 的板块。

    返回 {ok, kind, date, sectors, signals[], near[], rows[]}
      signals  站上MA20 且当日涨≥1.5%（回测 T+20 +2.41% / 62%）
      near     MA20 下方 3% 以内（即将收复，观察用）
      rows     全部板块（按 dist_pct 降序）
    """
    if days < 21:
        return {"ok": False, "error": "days 至少 21（算 MA20 要 20 根 + 当日）"}
    now = time.time()
    key = (kind, days)
    if (_SCAN_CACHE["val"] and _SCAN_CACHE["key"] == key
            and now - _SCAN_CACHE["ts"] < _SCAN_TTL):
        return _SCAN_CACHE["val"]

    mx = latest_date()
    if not mx:
        return {"ok": False, "error": "板块快照表为空"}

    # ★★ 2026-11-23（实测带宽瓶颈）：Supabase 免费档下行**恒定 8.4 KB/s**
    #   （100 行 430ms / 1000 行 5s / 6344 行 28.9s，完全线性）。
    #   ⇒ 拉 6,344 行回来在 Python 里算，等于让用户等 29 秒。
    #   改为**聚合下推**：SQL 里算好等价点位/MA20/量比，只返回 104 行（≈0.4s）。
    #   等价点位 = 1000 / prod_{j>i}(1+pct_j/100) ⇒ 用 EXP(SUM(LN(...))) 逆序累乘。
    _ensure_table()
    # date 是 TEXT ⇒ 先算字符串下界（21 交易日 ≈ 30 自然日，放 60 天留足余量）
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    try:
        cutoff = (_dt.strptime(mx, "%Y-%m-%d")
                  - _td(days=60)).strftime("%Y-%m-%d")
    except ValueError:
        return {"ok": False, "error": f"最新日期格式异常: {mx}"}

    rows = db.fetch("""
        WITH src AS (
            SELECT code, name, date, change_pct, trade_money
            FROM plate_daily_zz
            WHERE kind = %s AND date >= %s
        ),
        r AS (
            SELECT code, name, date, change_pct, trade_money,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
            FROM src
        ),
        f AS (
            SELECT code, name, date, change_pct, trade_money, rn,
                   COALESCE(
                     EXP(SUM(LN(GREATEST(1 + COALESCE(change_pct, 0) / 100.0, 0.01)))
                         OVER (PARTITION BY code ORDER BY date DESC
                               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)),
                     1.0) AS fwd
            FROM r WHERE rn <= 21
        )
        SELECT code,
               MAX(name) AS name,
               AVG(CASE WHEN rn <= 20 THEN 1000.0 / fwd END) AS ma20,
               1000.0 / MAX(CASE WHEN rn = 1 THEN fwd END) AS cur_level,
               MAX(CASE WHEN rn = 1 THEN change_pct END) AS ret1,
               MAX(CASE WHEN rn = 1 THEN trade_money END) AS amt_now,
               AVG(CASE WHEN rn BETWEEN 2 AND 21 THEN trade_money END) AS amt_base,
               COUNT(*) AS bars
        FROM f
        GROUP BY code
        HAVING COUNT(*) >= 21
    """, (kind, cutoff)) or []

    out = []
    for r in rows:
        try:
            cur_v = float(r.get("cur_level") or 0)
            ma20 = float(r.get("ma20") or 0)
        except (TypeError, ValueError):
            continue
        if not cur_v or not ma20:
            continue
        ret1 = r.get("ret1")
        try:
            ret1 = round(float(ret1), 2) if ret1 is not None else None
        except (TypeError, ValueError):
            ret1 = None
        amt_now = float(r.get("amt_now") or 0)
        amt_base = float(r.get("amt_base") or 0)
        amt_ratio = (round(amt_now / amt_base, 2)
                     if amt_base > 0 and amt_now > 0 else None)
        j = _judge(cur_v, ma20, ret1, amt_ratio,
                   name=r.get("name"), date=mx,
                   bars=int(r.get("bars") or 0))
        j["code"] = r.get("code")
        out.append(j)

    out.sort(key=lambda x: -(x.get("dist_pct")
                             if x.get("dist_pct") is not None else -999))
    res = {
        "ok": True, "kind": kind, "date": mx, "sectors": len(out),
        "bars_used": days,
        "signals": [x for x in out if x.get("signal")],
        "near": [x for x in out
                 if x.get("dist_pct") is not None and -3.0 <= x["dist_pct"] < 0],
        "rows": out,
        "caliber": ("等价点位口径；signals=站上MA20且涨≥1.5%（回测 T+20 +2.41%/62%），"
                    "不认缩量（缩量 T+20 -0.76%/43%，是负向信号）"),
    }
    _SCAN_CACHE.update(ts=now, key=key, val=res)
    return res


def format_lines_ma(limit: int = 5) -> List[str]:
    """日报用事实行：站上 MA20 的板块 / 即将收复的。不可用 ⇒ 空列表（缺失不占位）。

    ★ 2026-11-24（用户："ma_scan 结果接到日报"）：
      口径与 `sector_momentum.format_lines` 一致 —— **只出事实、不进决策链**。
      ⚠️ `ma_scan` 首次 5~8 秒（SQL 聚合），日报里调用会拖慢日批同等时间；
         但它有 5 分钟缓存，且日批本来就要跑几分钟，可接受。
    """
    res = ma_scan("industry")
    if not res.get("ok"):
        return []
    out = []
    sig = res.get("signals") or []
    near = res.get("near") or []

    if sig:
        out.append("**站上 MA20 且当日涨≥1.5%**（回测 T+20 +2.41% / 胜率 62%）：")
        for x in sig[:limit]:
            amt = x.get("amt_ratio")
            out.append("　%s　距MA20 %+.1f%%　当日 %+.2f%%　量比 %s"
                       % (x.get("name"), x.get("dist_pct") or 0,
                          x.get("ret1") or 0,
                          amt if amt is not None else "—"))
    else:
        out.append("今日**无**板块出现「站上MA20 + 涨≥1.5%」信号"
                   "（缩量企稳不算信号：回测 T+20 -0.76% / 胜率 43%，是负向的）")

    if near:
        seg = "、".join("%s %+.1f%%" % (x.get("name"), x.get("dist_pct") or 0)
                        for x in near[:limit])
        out.append("即将收复（MA20 下方 3%% 内，共 %d 个）：%s"
                   % (len(near), seg))
    return out


def latest_date() -> str:
    """表内最新日期（无数据返回 None）。"""
    try:
        _ensure_table()
        r = db.fetch_one("SELECT MAX(date) AS d FROM plate_daily_zz")
        return (r or {}).get("d")
    except Exception:
        return None


def dispersion(date: str = None, kind: str = "industry") -> dict:
    """板块分化度：当日各板块涨跌幅的离散程度。

    ★ 与东财版（`sector_industry.dispersion`）**同一算法、不同 taxonomy**：
      本版 104 个粗分板块，东财版 496 个细分板块 ⇒ **标准差绝对值不可直接横向比较**，
      趋势（今天比昨天更分化）仍可比。
    `date` 缺省取**表内最新日期**而非"今天" —— 否则休市/盘前会因当日无数据而误报"快照不足"。
    """
    import statistics
    d = date or latest_date()
    if not d:
        return {"date": None, "kind": kind, "error": "无快照数据（请先 take_snapshot / 回填）"}
    rows = get_snapshot(d, kind)
    if len(rows) < 5:
        return {"date": d, "kind": kind, "error": f"当日快照不足 5 个板块（{len(rows)}）"}
    chg = [float(r["change_pct"] or 0) for r in rows]
    up = sum(1 for c in chg if c > 0)
    down = sum(1 for c in chg if c < 0)
    return {
        "date": d, "kind": kind, "source": "zzshare",
        "sector_count": len(chg),
        "std_dev": round(statistics.pstdev(chg), 3),
        # ★ 字段名与东财版（`sector_industry.dispersion`）**逐一对齐** ——
        #   前端 SectorView 已按那套命名消费，同名可零改动复用其指标卡/解读逻辑。
        "max_spread": round(max(chg) - min(chg), 3),
        "mean_change": round(sum(chg) / len(chg), 3),
        "up_sectors": up, "down_sectors": down,
        "up_ratio": round(up / len(chg), 3),
        "top": [{"name": r["name"], "change_pct": r["change_pct"]} for r in rows[:5]],
        "bottom": [{"name": r["name"], "change_pct": r["change_pct"]} for r in rows[-5:]],
    }


def stats() -> dict:
    """快照表概况（已积累天数 / 最新日期 / 总行数）。"""
    try:
        _ensure_table()
        rows = db.fetch("SELECT DISTINCT date FROM plate_daily_zz ORDER BY date DESC LIMIT 10")
        total = db.fetch_one("SELECT COUNT(*) AS n FROM plate_daily_zz")
        dates = [str(r["date"])[:10] for r in (rows or [])]
        return {"latest_dates": dates, "days": len(dates),
                "total_rows": (total or {}).get("n", 0),
                "latest": dates[0] if dates else None}
    except Exception as e:
        return {"latest_dates": [], "days": 0, "total_rows": 0, "error": str(e)}
