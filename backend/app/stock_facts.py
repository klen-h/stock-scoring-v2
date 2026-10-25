# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】选股事实宽表（stock_facts_wide）——「选股工作台」的数据底座
================================================================================
设计：`docs/plans/20261026_选股事实宽表设计_v0.md`（用户 2026-10-26 拍板：
覆盖全宇宙 2136 / G·H 影子列展示）。

三条铁律（违反任何一条 = 违反 R2）：
  ① 只放**事实**与已披露展示工具——任何加权综合分不进表（= 新信号须预登记）；
  ② NULL = 不在榜/无数据，**绝不为 0**（时点诚实）；
  ③ 加列不加义（前端老版本不升级不能崩）。

生成：日批 `task_stock_facts_wide`（DEFAULT_ORDER 里排 sector_linkage/gate_snapshot
之后、daily_report 之前；失败不拖垮整批）。幂等按日覆盖。
数据全部来自**当日既有产物表**（零外呼、零新增计算源）：
  ranking_live（评分/名次/三维分）· mainforce_state（主力事实）·
  gate_snapshot_history.payload（闸门按票拆）· lhb_history（回避标记）·
  pattern_scan_daily（形态命中）· sector_strength_daily（板块）·
  market_snapshot（收盘行情定稿）· 防御组清单（与防御影子榜同源）。
================================================================================
"""
import json
import os
from typing import Dict, List, Optional

from app.database import db

_TABLE = "stock_facts_wide"
_READY = False

_COLS = (
    # A 基础
    "date, code, name, industry, board, close, chg_pct, float_cap_yi, "
    "turnover_pct, vol_ratio, ret5, ret20, dist_from_60d_high, "
    # B 评分（FAIL/KILL 徽章由前端 SIGNAL_STATUS 渲染，不落库）
    "score_total, score_signal, rank_pos, dim_technical, dim_capital, dim_fundamental, "
    # C 主力（事实标签）
    "phase, mainforce_signal, flow5_amt_yi, flow_consec_tier, "
    "chip_price_pos, chip_winner_ratio, "
    # D 闸门（状态展示）
    "gate_ready, gate_missing, gate_pos_label, "
    # E 龙虎榜（PASS-回避侧）
    "lhb_big_net_sell, lhb_latest_date, "
    # F 形态（预登记中）
    "pattern_hit, "
    # G 板块（影子期 PARTIAL）
    "sector_ret5, sector_top30, sector_pass_filter, "
    # H 防御组（H2' 影子中）
    "defensive_group"
)


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    id_ddl = ("id SERIAL PRIMARY KEY" if db.is_postgres
              else "id INTEGER PRIMARY KEY AUTOINCREMENT")
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            {id_ddl},
            date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, industry TEXT, board TEXT,
            close REAL, chg_pct REAL, float_cap_yi REAL, turnover_pct REAL, vol_ratio REAL,
            ret5 REAL, ret20 REAL, dist_from_60d_high REAL,
            score_total REAL, score_signal TEXT, rank_pos INTEGER,
            dim_technical REAL, dim_capital REAL, dim_fundamental REAL,
            phase TEXT, mainforce_signal TEXT, flow5_amt_yi REAL, flow_consec_tier TEXT,
            chip_price_pos REAL, chip_winner_ratio REAL,
            gate_ready INTEGER, gate_missing TEXT, gate_pos_label TEXT,
            lhb_big_net_sell INTEGER, lhb_latest_date TEXT,
            pattern_hit INTEGER,
            sector_ret5 REAL, sector_top30 INTEGER, sector_pass_filter INTEGER,
            defensive_group INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (date, code)
        )
    """)
    _READY = True


def _board(code: str) -> str:
    c = str(code or "")
    if c.startswith(("688", "689")):
        return "科创板"
    if c[:3] in ("300", "301", "302"):
        return "创业板"
    if c.startswith(("43", "82", "83", "87", "88", "92", "920")):
        return "北交所"
    return "主板"


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def build(day: str) -> Dict:
    """合成一日宽表（幂等覆盖）。返回统计。只读各产物表，不外呼。"""
    ensure_table()
    day = str(day)[:10]

    # ── A 基础行情：market_snapshot 收盘定稿（data_date=day）──
    snap = db.fetch_one("SELECT stocks_json FROM market_snapshot "
                        "WHERE data_date = %s ORDER BY saved_at DESC LIMIT 1", (day,))
    stocks = {}
    if snap and snap.get("stocks_json"):
        try:
            stocks = json.loads(snap["stocks_json"]) or {}
        except (ValueError, TypeError):
            stocks = {}

    # 行业映射（唯一入口 industry_map 的底表直读——日批时点缓存已过期无妨）
    imap = {str(r["code"]): str(r.get("main_industry") or "其它行业")
            for r in db.fetch("SELECT code, main_industry FROM stock_industry") or []}

    # 收盘价序列（ret5/ret20/距60日高点）——backtest_prices 窗口读
    # ★★ 2026-10-26（审查 P1-⑥）：窗口 -60 **自然日** ≈ 40 个交易日 ⇒ `closes[-60:]`
    #   实际只有 40 根，"距 60 日高点"名不副实。改 -95 自然日（≈65 交易日）才够 60 根。
    px = {}
    for r in db.fetch(
            "SELECT code, date, close FROM backtest_prices WHERE date >= %s "
            "AND code IN (SELECT DISTINCT code FROM ranking_live WHERE rank_date = %s "
            "UNION SELECT code FROM mainforce_state WHERE date = %s)",
            (_shift(day, -95), day, day)) or []:
        px.setdefault(str(r["code"]), []).append((str(r["date"])[:10], _f(r["close"])))

    # ★★ 2026-10-26（审查 P1-⑤）：`backtest_prices` 只覆盖 ~1032 只 ⇒ 其余 1800 只
    #   `ret5/ret20/dist_from_60d_high` 全为 NULL（实测 1029/2861 = 36%，连工行都缺）。
    #   用 `kline_cache`（2719 只 × 484 根）**分块兜底**，只补缺失 code。
    #   成本 ≈ 1800 × 15KB ≈ 20~27MB/日（Actions 侧 egress）；`FACTS_PX_FALLBACK=0` 可关。
    if os.environ.get("FACTS_PX_FALLBACK", "1").strip() != "0":
        _miss = [str(r["code"]) for r in db.fetch(
            "SELECT code FROM (SELECT code FROM ranking_live WHERE rank_date = %s "
            "UNION SELECT code FROM mainforce_state WHERE date = %s) t",
            (day, day)) or []]
        _miss = [c for c in _miss if c not in px]
        for _i in range(0, len(_miss), 150):
            _chunk = _miss[_i:_i + 150]
            _ph = ",".join(["%s"] * len(_chunk))
            for r in db.fetch(f"SELECT code, kline_data FROM kline_cache "
                              f"WHERE code IN ({_ph})", tuple(_chunk)) or []:
                try:
                    _bars = json.loads(r.get("kline_data") or "[]")
                except (ValueError, TypeError):
                    continue
                _cl = [(str(b.get("date"))[:10], _f(b.get("close")))
                       for b in (_bars or [])[-70:] if isinstance(b, dict)]
                _cl = [(d, c) for d, c in _cl if c]
                if _cl:
                    px[str(r["code"])] = _cl
        print(f"[stock_facts] 行情序列: kline_cache 兜底 {len(_miss)} 只"
              f"（共 {len(px)} 只有序列）")

    def _facts_basic(code, st):
        """A 组 8 个事实（返回 dict，但**调用方按显式键取值**，不依赖 dict 顺序）。"""
        closes = sorted(px.get(code) or [])
        n = len(closes)
        c_now = closes[-1][1] if n else None
        ret5 = (c_now / closes[-6][1] - 1) * 100 if n >= 6 and closes[-6][1] else None
        ret20 = (c_now / closes[-21][1] - 1) * 100 if n >= 21 and closes[-21][1] else None
        hi60 = max(c for _, c in closes[-60:]) if n else None
        dist = (c_now / hi60 - 1) * 100 if c_now and hi60 else None
        # ★★ 2026-10-26（审查 P0-②）：**单位换算**。源头单位与列名不一致，必须在此归一：
        #   · `market_snapshot.float_cap` = **万元**（工行 224,317,000 → 22431.7 亿）
        #   · `mainforce_state.flow5_amt_yuan` = **元**（−46,296,800,000 → −462.97 亿）
        cap_yi = _f(st.get("float_cap"))
        return {"close": st.get("price"), "chg_pct": st.get("change_pct"),
                "float_cap_yi": (cap_yi / 1e4 if cap_yi is not None else None),
                "turnover_pct": st.get("turnover_rate"),
                "vol_ratio": st.get("volume_ratio"),
                "ret5": ret5, "ret20": ret20, "dist_from_60d_high": dist}

    # ── B 评分：ranking_live 当日（宇宙=精算榜全集）──
    rl = {str(r["code"]): r for r in db.fetch(
        "SELECT code, name, total_score, signal, rank_pos, payload FROM ranking_live "
        "WHERE rank_date = %s", (day,)) or []}

    # ── C 主力：mainforce_state 当日 ──
    mf = {str(r["code"]): r for r in db.fetch(
        "SELECT code, name, phase, signal, flow5_amt_yuan, flow_consec, chip_json "
        "FROM mainforce_state WHERE date = %s", (day,)) or []}

    # ── D 闸门：gate_snapshot_history.payload 当日（按票拆）──
    # ★★ 2026-10-26（审查 P0-③）：payload 的真实结构是
    #   `{regime, counts, evaluated, strategy_date, candidates:[...]}`，
    #   原代码读 `.get("items")` ⇒ **恒为空**（2861 行 gate_ready 全 NULL，冒烟也没发现）。
    #   ⚠️ candidates 只含 **ready≥2** 的候选（约 248 只），不是全宇宙 evaluated(2974)
    #   ⇒ 其余行的 gate 列必须是 **NULL（"未达 ready≥2/未评估"），不能填 0**（0 表示"评估过且 0/3"）。
    gate = {}
    g = db.fetch_one("SELECT payload FROM gate_snapshot_history WHERE date = %s "
                     "ORDER BY created_at DESC LIMIT 1", (day,))
    if g and g.get("payload"):
        try:
            _pl = json.loads(g["payload"]) or {}
            for it in (_pl.get("candidates") or []):
                gate[str(it.get("code"))] = it
        except (ValueError, TypeError) as e:
            print(f"[stock_facts] 闸门 payload 解析失败（gate 列置空）: {str(e)[:80]}")

    # ── E 龙虎榜：近 10 日**最近一次**上榜是否「大额净卖」──
    # ★★ 2026-10-26（审查 P2）：原判据是 `net_buy < 0`（任何净卖）⇒ 18 只里混进
    #   小额卖出，与 `lhb.recent_summary` 的"大额净卖（big_sell）"口径不一致。
    #   现复用**同一事实源** `_event_bucket`（优先用行内 circ_mktcap，零额外取数；
    #   缺该列才回退本地包 → 与详情页徽章完全同口径）。
    lhb = {}
    try:
        from app.mainforce.lhb import _event_bucket as _lhb_bucket
    except Exception as e:
        _lhb_bucket = None
        print(f"[stock_facts] 龙虎榜分档不可用（该列置空）: {str(e)[:80]}")
    for r in db.fetch(
            "SELECT DISTINCT ON (code) code, date, net_buy, sell_total, circ_mktcap "
            "FROM lhb_history WHERE date >= %s AND date <= %s ORDER BY code, date DESC",
            (_shift(day, -10), day)) or []:
        code = str(r["code"])
        bucket = (_lhb_bucket(code, str(r["date"])[:10], r.get("net_buy"),
                              r.get("circ_mktcap")) if _lhb_bucket else None)
        lhb[code] = {"lhb_big_net_sell": 1 if bucket == "big_sell" else 0,
                     "lhb_latest_date": str(r["date"])[:10]}

    # ── F 形态：pattern_scan_daily 当日命中 ──
    pat = {str(r["code"]) for r in db.fetch(
        "SELECT code FROM pattern_scan_daily WHERE date = %s", (day,)) or []}

    # ── G 板块：sector_strength_daily（is_strong=强势前30%∧ret5>0∧成员≥5）──
    sec = {str(r.get("industry") or ""): r for r in db.fetch(
        "SELECT industry, ret5, is_strong FROM sector_strength_daily WHERE date = %s",
        (day,)) or []}
    # 个股级"影子过滤保留"：当日平行榜 ranking_history_sector 有行 = 保留
    sector_keep = {str(r["code"]) for r in db.fetch(
        "SELECT code FROM ranking_history_sector WHERE rank_date = %s", (day,)) or []}

    # ── H 防御组：宽防御组当日清单（银行 ∪ 高股息代理 ∪ 低波）──
    # ★★ 2026-10-26（审查 P0-④）：原代码读 `payload` 列，但该表**没有 payload**
    #   （实际列 date/code/name/grp/vol60/mktcap/industry）⇒ 异常被 `except: pass`
    #   吞掉后**静默回退**成"名称含银行"的 38 只，与 144 只宽组口径分叉（冒烟也未察觉）。
    #   现直接读 `code` 列；**当日无行 ⇒ 该列全部 NULL（无数据），不再拿银行顶替**
    #   （顶替会让本表与防御影子榜对同一概念给出两个答案）。
    defen = set()
    try:
        defen = {str(r["code"]) for r in db.fetch(
            "SELECT code FROM defensive_group_daily WHERE date = %s", (day,)) or []}
    except Exception as e:
        print(f"[stock_facts] 防御组读取失败（该列置空）: {str(e)[:80]}")

    # ── 合成（宇宙 = 精算榜 ∪ 主力状态 = 全评分宇宙 ~2136）──
    universe = sorted(set(rl) | set(mf))
    rows = []
    for code in universe:
        r = rl.get(code) or {}
        m = mf.get(code) or {}
        st = stocks.get(code) or {}
        name = r.get("name") or m.get("name") or st.get("name") or code
        ind = imap.get(code, "其它行业")
        srow = sec.get(ind) or {}
        chip = {}
        try:
            chip = json.loads(m.get("chip_json") or "{}") or {}
        except (ValueError, TypeError):
            pass
        gt = gate.get(code) or {}
        lh = lhb.get(code) or {}
        # 三维分：ranking_live payload.dimensions（若为 dict 拆列；口径以产物为准）
        dims = {}
        try:
            dims = (json.loads(r.get("payload") or "{}") or {}).get("dimensions") or {}
            if isinstance(dims, list):      # 兼容数组形态 [{name,score}]
                dims = {str(d.get("name")): d.get("score") for d in dims if isinstance(d, dict)}
        except (ValueError, TypeError):
            pass
        fb = _facts_basic(code, st)
        _f5 = _f(m.get("flow5_amt_yuan"))
        # 闸门的"还差什么"：payload.candidates 里就是 `missing: [label...]`（已算好，别重推）
        _gmiss = gt.get("missing")
        if isinstance(_gmiss, list):
            _gmiss = "、".join(str(x) for x in _gmiss) or None
        elif _gmiss:
            _gmiss = str(_gmiss)
        else:
            _gmiss = None
        rows.append((
            day, code, name, ind, _board(code),
            fb["close"], fb["chg_pct"], fb["float_cap_yi"], fb["turnover_pct"],
            fb["vol_ratio"], fb["ret5"], fb["ret20"], fb["dist_from_60d_high"],
            r.get("total_score"), r.get("signal"), r.get("rank_pos"),
            _f(dims.get("技术面")), _f(dims.get("资金面")), _f(dims.get("基本面")),
            m.get("phase"), m.get("signal"),
            (_f5 / 1e8 if _f5 is not None else None),      # 元 → 亿（审查 P0-②）
            m.get("flow_consec"), _f(chip.get("price_pos")), _f(chip.get("winner_ratio")),
            gt.get("ready"), _gmiss, gt.get("label"),
            lh.get("lhb_big_net_sell"), lh.get("lhb_latest_date"),
            1 if code in pat else 0,
            _f(srow.get("ret5")),
            1 if srow.get("is_strong") else (0 if srow else None),
            (1 if code in sector_keep else None),
            # 无当日防御组数据 ⇒ NULL（无数据 ≠ 不在组内）
            (1 if code in defen else (0 if defen else None)),
        ))

    # 幂等覆盖写入
    n = 0
    db.execute(f"DELETE FROM {_TABLE} WHERE date = %s", (day,))
    marks = ",".join(["%s"] * len(rows[0]))   # 列数按首行元组自适应（35）
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        db.execute(f"INSERT INTO {_TABLE} ({_COLS}) VALUES "
                   + ",".join(f"({marks})" for _ in chunk),
                   tuple(v for row in chunk for v in row))
        n += len(chunk)
    return {"date": day, "rows": n, "universe": len(universe)}


def _shift(day: str, n: int) -> str:
    from datetime import date, timedelta
    y, m, d = map(int, day.split("-"))
    return (date(y, m, d) + timedelta(days=n)).isoformat()


def latest(day: Optional[str] = None, filters: Optional[Dict] = None,
           order_by: str = "rank_pos", limit: int = 100, offset: int = 0,
           desc: bool = False, min_cap: Optional[float] = None) -> Dict:
    """只读查询：宽表当日行（分页 + 单列排序 + 等值筛选 + 最小市值）。**不做任何加权。**"""
    ensure_table()
    if not day:
        r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE}")
        day = (r or {}).get("d")
    if not day:
        return {"date": None, "rows": [], "total": 0}
    where, params = ["date = %s"], [day]
    # ★ 2026-10-26（审查 P2）：接口早就声明了 min_cap 却没用 ⇒ 前端传了也无效（死参数）
    if min_cap is not None:
        where.append("float_cap_yi >= %s")
        params.append(float(min_cap))
    ALLOWED = {c for c in _COLS.replace(" ", "").split(",")}
    for k, v in (filters or {}).items():
        if k in ALLOWED:
            where.append(f"{k} = %s")
            params.append(v)
    total = (db.fetch_one(f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE "
                          + " AND ".join(where), tuple(params)) or {}).get("n", 0)
    ob = order_by if order_by in ALLOWED else "rank_pos"
    direction = "DESC" if desc else "ASC"
    rows = db.fetch(f"SELECT {_COLS} FROM {_TABLE} WHERE " + " AND ".join(where)
                    + f" ORDER BY {ob} {direction} NULLS LAST LIMIT %s OFFSET %s",
                    tuple(params) + (limit, offset)) or []
    return {"date": day, "total": total, "rows": rows}
