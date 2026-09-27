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
