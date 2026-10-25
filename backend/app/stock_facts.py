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
    # F2 战法命中（★ 2026-10-26 新增）：英文 key 逗号分隔，如 "ma_pullback,single_yang_unbroken"；
    #    NULL = 当日该股未被任何战法扫中（不是"没扫"—— 扫描宇宙见 strategy_results）
    "strategy_hit, "
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
            strategy_hit TEXT,
            sector_ret5 REAL, sector_top30 INTEGER, sector_pass_filter INTEGER,
            defensive_group INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (date, code)
        )
    """)
    # ★ 2026-10-26（加列不加义）：`CREATE TABLE IF NOT EXISTS` **不会**给已存在的表补列 ——
    #   线上表早已建好，新列必须显式 ALTER，否则 `INSERT` 立刻报列数不匹配（整批失败）。
    try:
        if db.is_postgres:
            db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS strategy_hit TEXT")
        else:
            _cols = {str(r.get("name")) for r in db.fetch(f"PRAGMA table_info({_TABLE})") or []}
            if "strategy_hit" not in _cols:
                db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN strategy_hit TEXT")
    except Exception as e:
        print(f"[stock_facts] 补列 strategy_hit 失败（将致写入异常）: {str(e)[:100]}")
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


def _jload(v):
    """JSONB / TEXT 双兼容解析（★ 2026-10-26）。

    Postgres 的 `jsonb` 列经 psycopg2 返回的就是 **dict/list**；`json.loads(dict)`
    抛 `TypeError` 且被调用方 `except ...: pass` 吞掉 ⇒ **静默变空**。
    实测事故：`mainforce_state.chip_json` 是 jsonb ⇒ 宽表 `chip_price_pos` /
    `chip_winner_ratio` 全 0（2861 行无一非空），其它列都用 TEXT 才幸免。
    所有 JSON 列一律过这里，不再假设字符串。
    """
    if v is None:
        return {}
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v) or {}
    except (ValueError, TypeError):
        return {}


def build(day: str) -> Dict:
    """合成一日宽表（幂等覆盖）。返回统计。只读各产物表，不外呼。"""
    ensure_table()
    day = str(day)[:10]

    # ── A 基础行情：market_snapshot 收盘定稿（data_date=day）──
    snap = db.fetch_one("SELECT stocks_json FROM market_snapshot "
                        "WHERE data_date = %s ORDER BY saved_at DESC LIMIT 1", (day,))
    stocks = _jload(snap.get("stocks_json")) if snap else {}

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
            # ★★ 2026-10-26：优先读**紧凑列** `closes61`（≈450B/行）；只有历史行还没补齐
            #   （closes61 IS NULL）时才回退读 15KB 的 kline_data ⇒ 日常 egress 27MB → ~0.9MB。
            for r in db.fetch(
                    f"SELECT code, closes61, CASE WHEN closes61 IS NULL "
                    f"THEN kline_data END AS kd FROM kline_cache "
                    f"WHERE code IN ({_ph})", tuple(_chunk)) or []:
                _cl = []
                if r.get("closes61"):
                    _cl = [("", float(c)) for c in _jload(r["closes61"])
                           if isinstance(c, (int, float))]
                elif r.get("kd"):
                    _bars = _jload(r["kd"])
                    _cl = [(str(b.get("date"))[:10], _f(b.get("close")))
                           for b in (_bars or [])[-70:] if isinstance(b, dict)]
                    _cl = [(d, c) for d, c in _cl if c]
                if _cl:
                    px[str(r["code"])] = _cl
        print(f"[stock_facts] 行情序列: kline_cache 兜底 {len(_miss)} 只"
              f"（共 {len(px)} 只有序列）")

    def _facts_basic(code, st):
        """A 组 8 个事实（返回 dict，但**调用方按显式键取值**，不依赖 dict 顺序）。"""
        # `closes61` 兜底路径没有日期（date=""）⇒ **保持原顺序**（kline 本就是升序）；
        # 有日期的（backtest_prices）才按日期排序。不能对 ("", close) 排序——那会按价格重排。
        _series = px.get(code) or []
        closes = sorted(_series) if _series and _series[0][0] else list(_series)
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
    if g:
        for it in (_jload(g.get("payload")).get("candidates") or []):
            if isinstance(it, dict) and it.get("code"):
                gate[str(it["code"])] = it

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
    # ★★ 2026-10-26（用户问"形态命中加入的难点"）：难点不在字段，在**上游产物太稀疏**——
    #   ① `pattern_launch.scan_latest()` 只在 `backtest_prices`（~1032 只）宇宙里扫，
    #      评分宇宙另外 ~1800 只**结构上不可能命中**；
    #   ② 形态判据苛刻（缩量调整+放量启动+max_results 截断）⇒ 单日 0~1 只（实测 10-22 仅 1 只、
    #      10-23 为 0）⇒ 勾上「形态命中」经常是空表，看着像坏了；
    #   ③ `persist_scan` 旧实现在 **0 命中时直接不落库** ⇒ 事实表无法区分
    #      "扫过但没中"（0）与"根本没扫"（NULL）。
    #   ⇒ 现改为：`sync_meta['pattern_scan'].note` 记当日（persist_scan 已补留痕），
    #      当日扫过才写 0/1，否则 **NULL（无数据）**。
    pat = {str(r["code"]) for r in db.fetch(
        "SELECT code FROM pattern_scan_daily WHERE date = %s", (day,)) or []}
    _pat_scanned = False
    try:
        _pm = db.fetch_one("SELECT note FROM sync_meta WHERE key = 'pattern_scan'")
        _pat_scanned = str((_pm or {}).get("note") or "")[:10] == day
    except Exception as e:
        print(f"[stock_facts] 形态扫描留痕读取失败（pattern_hit 全部置空）: {str(e)[:80]}")

    # ── F2 战法命中（★ 2026-10-26 新增，用户："还有加个战法命中"）──
    #   源头 = `strategy_results`（日批各战法扫描产物，`scan_date` = 最近已完成交易日）。
    #   ⚠️ `results_json` 里**带 klines**（当日 10 个战法合计 ≈190KB）⇒ 只取 `code`，
    #      不必也不该把整包塞进事实表；命中战法以**英文 key** 逗号分隔落库
    #      （前端 `strategyShort()` 负责显示中文，未登记就回退英文，不隐藏）。
    strat = {}
    try:
        for r in db.fetch("SELECT strategy_name, results_json FROM strategy_results "
                          "WHERE scan_date = %s", (day,)) or []:
            _items = _jload(r.get("results_json"))
            if not isinstance(_items, list):
                continue
            _sn = str(r.get("strategy_name") or "")
            for it in _items:
                c = str((it or {}).get("code") or "") if isinstance(it, dict) else ""
                if c:
                    strat.setdefault(c, set()).add(_sn)
    except Exception as e:
        print(f"[stock_facts] 战法结果读取失败（该列置空）: {str(e)[:80]}")

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
        # ★ 2026-10-26：`chip_json` 是 **jsonb**（psycopg2 返回 dict）⇒ 必须走 `_jload`；
        #   旧代码 `json.loads(dict)` 抛 TypeError 被吞 ⇒ chip_price_pos/winner_ratio 全空（实测事故）。
        chip = _jload(m.get("chip_json"))
        gt = gate.get(code) or {}
        lh = lhb.get(code) or {}
        # 三维分：ranking_live payload.dimensions（若为 dict 拆列；口径以产物为准）
        # 三维分：ranking_live payload.dimensions（若为 dict 拆列；口径以产物为准）
        dims = _jload(r.get("payload")).get("dimensions") or {}
        if isinstance(dims, list):      # 兼容数组形态 [{name,score}]
            dims = {str(d.get("name")): d.get("score") for d in dims if isinstance(d, dict)}
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
            # 形态：扫过才写 0/1；当日未产出扫描 ⇒ NULL（无数据 ≠ 未命中）
            (1 if code in pat else (0 if _pat_scanned else None)),
            (",".join(sorted(strat.get(code) or [])) or None),
            _f(srow.get("ret5")),
            1 if srow.get("is_strong") else (0 if srow else None),
            (1 if code in sector_keep else None),
            # 无当日防御组数据 ⇒ NULL（无数据 ≠ 不在组内）
            (1 if code in defen else (0 if defen else None)),
        ))

    # 幂等覆盖写入
    n = 0
    db.execute(f"DELETE FROM {_TABLE} WHERE date = %s", (day,))
    marks = ",".join(["%s"] * len(rows[0]))   # 列数按首行元组自适应（36）
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
           desc: bool = False, min_cap: Optional[float] = None,
           where_extra: Optional[List] = None) -> Dict:
    """只读查询：宽表当日行（分页 + 单列排序 + 等值筛选 + 最小市值）。**不做任何加权。**

    `where_extra`：额外的 `[(SQL 片段, 参数序列), …]` —— 用于等值筛选表达不了的谓词
    （如 `strategy_hit IS NOT NULL` / `strategy_hit LIKE %x%`）。
    ⚠️ 片段**只能由后端代码构造**，绝不接受前端原始 SQL（防注入）。
    """
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
    for frag, prm in (where_extra or []):
        where.append(frag)
        params.extend(list(prm or []))
    total = (db.fetch_one(f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE "
                          + " AND ".join(where), tuple(params)) or {}).get("n", 0)
    ob = order_by if order_by in ALLOWED else "rank_pos"
    direction = "DESC" if desc else "ASC"
    rows = db.fetch(f"SELECT {_COLS} FROM {_TABLE} WHERE " + " AND ".join(where)
                    + f" ORDER BY {ob} {direction} NULLS LAST LIMIT %s OFFSET %s",
                    tuple(params) + (limit, offset)) or []
    return {"date": day, "total": total, "rows": rows}
