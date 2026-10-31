# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】主力行为日批状态表：全池计算 mainforce overlay → mainforce_state
================================================================================

为什么是"日批 + 查表"而不是端点现算：
  - Render 0.1 CPU：筹码分布对每只要滚 750 根 × 150 档，请求路径算不动
  - 数据依赖都是盘后落库的（backtest_prices 16:10 回填、mainflow_history 17:00）
  - 主力行为是慢信号（筹码结构日内几乎不变），日频足够

表：mainforce_state(code, name, date, phase, signal, mult, chip_json, flow5_amt)
  UNIQUE(code, date) 幂等。score_top 一次 IN 查询挂到榜单。

调度：mainforce_state_refresh_loop（17:30，资金流日更 17:00 之后）。
================================================================================
"""

import json
import time
from datetime import datetime

from app.database import db

from app.mainforce.flow import (get_float_shares_from_snapshot,
                                load_flow_map)
from app.mainforce.overlay import mainforce_overlay
# ★ 2026-09-22：阶段中文名的唯一事实源（详情页/榜单/观察池共用）。
from app.mainforce.phases import PHASE_CN

# 日线回看窗口（交易日数）。主力状态计算需求：chip warmup 60 / MA120 量代理 /
# ret60 / phase ≥70 —— 260 根留足余量；再深对结果无增益，纯烧 Supabase egress。
BARS_WINDOW = 260

# ★★ 2026-10-10（egress：Actions 冷启根治）：**资金流窗口**（交易日数）。
#   本模块只需要**近 5 日**资金流（`overlay` 里就是 `flow_rows[-5:]` ⇒ flow5_amt /
#   flow5_amt_yuan），但原来调 `load_flow_map()` **整表读 146 个交易日/只**
#   （pg_stat_statements 实测 12.9MB / 120,850 行 / 次，是全库单条语句第一名）。
#   而 **Actions 每次都是全新 runner ⇒ `backend/data/flow-map.db` 本机缓存天然不存在**
#   ⇒ 日批每个交易日白付 12.9MB。20 = 5 的 4 倍余量；
#   语义边界见 `flow.load_flow_map` docstring（flow5_* 逐值不变；
#   flow_consec 上限=窗口天数，且全库无任何消费方）。
FLOW_WINDOW_DAYS = 20


def ensure_table() -> None:
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS mainforce_state (
                id BIGSERIAL PRIMARY KEY,
                code VARCHAR(12) NOT NULL,
                name VARCHAR(32),
                date DATE NOT NULL,
                phase VARCHAR(16),
                signal VARCHAR(16),
                mult DOUBLE PRECISION DEFAULT 1.0,
                chip_json JSONB,
                flow5_amt DOUBLE PRECISION,
                flow5_amt_yuan DOUBLE PRECISION,
                flow_consec INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_mainforce_state UNIQUE (code, date)
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS mainforce_state (
                code TEXT NOT NULL, name TEXT, date TEXT NOT NULL,
                phase TEXT, signal TEXT, mult REAL DEFAULT 1.0,
                chip_json TEXT, flow5_amt REAL, flow5_amt_yuan REAL,
                flow_consec INTEGER,
                UNIQUE (code, date)
            )
        """)
    # ★★ 2026-10-25（egress 体检，`egress_report` 首次可用发现）：补 `date` **单列**索引。
    #   表上只有 `(code, date)` 唯一索引 ⇒ 按日期过滤（`missing_codes` / `thin_codes` /
    #   日批对账 / 面板取当日快照）只能**整表 seq scan**：实测一次 27,745 行 ≈ 8.85MB，
    #   日批每天扫 2 次 ≈ 17MB 纯浪费。加索引后同一查询 ≈0.9MB（只读当日 2854 行）。
    #   `CREATE INDEX IF NOT EXISTS` 两个方言都支持 ⇒ 无需分支。
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_mf_state_date "
                   "ON mainforce_state(date)")
    except Exception as e:
        print(f"[mainforce_state] date 索引创建失败（不影响主流程）: {str(e)[:80]}")

    # ★ 2026-09-17：已有表增列 flow5_amt_yuan（5 日主力净流入金额，元）。
    #   幂等 ALTER：PG 支持 IF NOT EXISTS；SQLite 退回普通 ADD COLUMN + 异常兜底
    #   （列已存在时报错即忽略）。
    try:
        db.execute("ALTER TABLE mainforce_state ADD COLUMN IF NOT EXISTS "
                   "flow5_amt_yuan DOUBLE PRECISION")
    except Exception:
        try:
            db.execute("ALTER TABLE mainforce_state ADD COLUMN flow5_amt_yuan REAL")
        except Exception:
            pass
    # ★★ 2026-10-10（P1-2）：增列 `flow_consec`（连续主力净流入天数）。
    #   此前它是**孤儿字段**——`overlay.mainforce_overlay` 算出来了（`overlay.py:98-107`），
    #   但日批只落 6 个字段 ⇒ 详情页/观察池走"表命中"路径时**拿不到**（只有表被判定
    #   不新鲜、现场算 overlay 时才有），前端更无任何消费方 ⇒ 白算。
    #
    #   ⚠️ **口径上限（必读，写进文档与标签语义）**：本列的值受 `refresh_all` 读取窗口
    #   `FLOW_WINDOW_DAYS`（=20 交易日，P0-1 为省 egress 设的窗口）**隐式封顶**
    #   —— 资金流回看窗口 ~31 个交易日（`_flow_window_cutoff` 的 1.7 倍余量），
    #   所以极端连续流入（>31 天）会被截断在窗口长度上。
    #   ⇒ 因此**对外标签只分档、不报精确值**：2~4 / 5~9 / ≥10 天（10 < 窗口下限，
    #     任何档位判定都不受封顶影响）；预登记检验（`scripts/flow_consec_edge_check.py`）
    #     用**同一分档**（`min(consec,10)`）⇒ 口径与生产同源。
    #   ⇒ 若将来要用**精确值**，必须放宽 `FLOW_WINDOW_DAYS`（直接加 egress）或按 code
    #     单独查（见 `flow.load_flow_map` docstring 的语义边界）。
    try:
        db.execute("ALTER TABLE mainforce_state ADD COLUMN IF NOT EXISTS "
                   "flow_consec INTEGER")
    except Exception:
        try:
            db.execute("ALTER TABLE mainforce_state ADD COLUMN flow_consec INTEGER")
        except Exception:
            pass


def _load_bars_all() -> dict:
    try:
        from app.scoring.kline_cache import ensure_kline_cache_table
        ensure_kline_cache_table()   # ★ 审查 P1-18：直查 kline_cache 前确保表存在
    except Exception:
        pass
    """
    合并两个日线来源（优先 backtest_prices，kline_cache 补覆盖）：
      - backtest_prices：544+ 只 × 750 根，16:10 每日回填（最深最新）
      - kline_cache：评分精算路径写回的全量 K 线（~2200 只），
        但存在战法短拉取污染的 30 根行 → 只收 kline_count ≥ 250 的行
    返回 {code: (bars, name)}，冲突时取更长的一方（名字随最长来源）。

    ★ egress 控制（2026-09-05）：本函数每天 17:30 全量跑一次，原先把
      backtest_prices 整表（~71MB）+ kline_cache 大 JSON（~19MB）拖下来，
      实测是 Supabase 出站流量的大头。而主力状态计算只需 ~120-250 根
      （chip warmup 60 / MA120 量代理 / ret60 / phase ≥70），故：
      ① backtest_prices 按日期窗口截断（BARS_WINDOW≈260 个交易日）；
      ② kline_cache 回退只补 backtest_prices 没覆盖的代码 —— 覆盖了的
         原来 750>500 必胜出，读了也白读（纯烧流量）。
    """
    bars: dict = {}

    # ★ DATA_SOURCE=pack/local：整段直接读数据包（零 Supabase 流量）。
    #   包内 500 根 ≥ 主力状态需求（~120-250 根），无需再合并 kline_cache。
    try:
        from app import pack_source
        if pack_source.enabled():
            for code in pack_source.get_codes():
                b = pack_source.get_klines(code)
                if b and len(b) >= 120:
                    nm, _cap = pack_source.get_name_cap(code)
                    bars[code] = (b, nm)
            print(f"[mainforce_state] 数据源=pack: {len(bars)} 只")
            return bars
    except Exception as e:
        print(f"[mainforce_state] pack 读取失败，回退数据库: {e}")

    def _put(code, rows, name=""):
        if rows and (code not in bars or len(rows) > len(bars[code][0])):
            bars[code] = (rows, name)

    from datetime import timedelta
    # 260 交易日 ≈ 372 个日历日（*8/5），再放宽到 380 防长假断层
    cutoff = (datetime.now() - timedelta(days=max(BARS_WINDOW * 8 // 5, 380))
              ).strftime("%Y-%m-%d")

    try:
        cn = db.fetch("SELECT code, MAX(name) AS name FROM backtest_prices "
                      "GROUP BY code ORDER BY code")
        name_by = {r["code"]: (r.get("name") or "") for r in cn}
        codes = [r["code"] for r in cn]
        for i in range(0, len(codes), 120):
            chunk = codes[i:i + 120]
            rows = db.fetch(
                "SELECT code, date, open, high, low, close, volume FROM backtest_prices "
                "WHERE code = ANY(%s) AND date >= %s ORDER BY code, date ASC",
                (chunk, cutoff))
            tmp = {}
            for r in rows:
                tmp.setdefault(r["code"], []).append({
                    "date": str(r["date"]), "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"], "volume": r["volume"]})
            for c, b in tmp.items():
                _put(c, b, name_by.get(c, ""))
    except Exception as e:
        print(f"[mainforce_state] backtest_prices 读取失败：{e}")

    try:
        # kline_data 是大 JSON（整表一次查会 statement timeout）→ 分块 + 限列
        # ★ 只补 backtest_prices 没覆盖的代码：被覆盖的原先 750>500 必胜出，
        #   读了也白读（每行 ~9KB 的 kline_data 纯烧 egress）
        cnts = db.fetch("SELECT code FROM kline_cache WHERE kline_count >= 250 "
                        "ORDER BY code")
        kcodes = [r["code"] for r in cnts if r["code"] not in name_by]
        for i in range(0, len(kcodes), 150):
            chunk = kcodes[i:i + 150]
            rows = db.fetch("SELECT code, name, kline_data FROM kline_cache "
                            "WHERE code = ANY(%s)", (chunk,))
            for r in rows:
                try:
                    data = r["kline_data"]
                    arr = json.loads(data) if isinstance(data, str) else data
                    if arr and len(arr) >= 120:
                        _put(r["code"], arr, r.get("name") or "")
                except (TypeError, ValueError):
                    continue
    except Exception as e:
        print(f"[mainforce_state] kline_cache 读取失败：{e}")
    return bars


# ★ 2026-09-13（审查 P0-3）：load_latest 读缓存 —— mainforce_state 是日批日更表，
#   此前榜单每 3min 重算会全池回源（~2000 行 chip_json ≈ 0.6MB/次 ≈ 40-55MB/天）。
#   只查缓存缺失的 code；本进程 _save/refresh_all 落库即失效（跨进程由 TTL 兜底）。
_LATEST_TTL_SEC = 1800
_latest_cache = {"ts": 0.0, "data": {}}   # {code: state_dict}


def refresh_all(codes: list = None, regime: str = None, verbose_every: int = 100) -> dict:
    """全池计算当日主力行为状态（幂等覆盖当日）。

    ★★ 2026-09-23：加**断点续传 + 逐只容错**。起因：线上连续两天企微报
      「主力行为状态计算失败 —— SSL connection has been closed unexpectedly」
      （Supabase 断开长过程中的连接），而本函数要**逐只写 ~2098 次**（历史耗时 ≈20 分钟）
      ⇒ 20 分钟里断一次就整体失败；scheduler 每 10 分钟重试一轮、**且每轮都从零开始**
      ⇒ 永远跑不完（`schedule_state` 里因此**始终没有 `mainforce_state` 这一行** ——
      该标记只在成功后才写）。两层防护：
        ① **断点续传**：`_save` 逐条自动提交 ⇒ 已写入的行不会丢。开跑前先查出
           「今日已写入的 code」并跳过 ⇒ 第 N 次重试只补未写的，**累计必然收敛**
           （首轮 20 分钟 → 后续轮次几十秒）。
        ② **逐只容错**：单只解析/写入异常只跳过该只并计数，不再让 2000 只白算
           （「跳过单只」≠「用错数据」，与项目既有取舍一致）。但**失败率 >20% 仍抛错**
           —— 那是连接/数据源级故障，不能"大面积缺失却标记成功"。
    """
    ensure_table()
    _latest_cache.update({"ts": 0.0, "data": {}})   # 写入即失效
    bars_map = _load_bars_all()
    if codes:
        bars_map = {c: bars_map[c] for c in codes if c in bars_map}
    fs_map = get_float_shares_from_snapshot()
    # ★★ 2026-10-10（egress）：只读**最近 FLOW_WINDOW_DAYS 个交易日**的资金流窗口
    #   —— 下面只用得到近 5 日（`mainforce_overlay` 内是 `flow_rows[-5:]`），
    #   原来整表读 146 个交易日/只（12.9MB / 120,850 行 / 次），而 Actions 每次是
    #   全新 runner、本机缓存不存在 ⇒ 每个交易日白付一次。详见本文件顶部
    #   `FLOW_WINDOW_DAYS` 与 `flow.load_flow_map` 的语义边界说明。
    flow_map = load_flow_map(days=FLOW_WINDOW_DAYS)

    # ── 断点续传：先确定本轮交易日（各只取自己末根），再查已写入的 code ──
    no_today = None
    for _c, (_b, _n) in bars_map.items():
        if _b:
            no_today = _b[-1]["date"]
            break
    done = set()
    if no_today:
        try:
            rows = db.fetch("SELECT code FROM mainforce_state WHERE date = %s", (no_today,))
            done = {r["code"] for r in (rows or [])}
            if done:
                print(f"[mainforce_state] 断点续传：{no_today} 已写入 {len(done)} 只，本轮跳过")
        except Exception as e:
            print(f"[mainforce_state] 已写入 code 读取失败（本轮按全量重算）: {e}")

    today = None
    n = skipped = failed = 0
    buf = []          # ★ 2026-10-27：批量写缓冲（见 `_save_many` 的性能说明）
    t0 = time.time()

    def _flush():
        nonlocal n
        if not buf:
            return
        n += _save_many(buf)
        buf.clear()

    for i, (code, (bars, name)) in enumerate(sorted(bars_map.items()), 1):
        if not bars:
            continue
        today = bars[-1]["date"]
        if code in done:
            skipped += 1
        else:
            try:
                ov = mainforce_overlay(bars, flow_rows=flow_map.get(code),
                                       float_shares=fs_map.get(code), regime=regime)
                if ov:
                    buf.append((code, name, today, ov))
                    if len(buf) >= BATCH_SAVE:
                        _flush()
            except Exception as e:
                failed += 1
                if failed <= 5:      # 只打前 5 条，避免刷屏；数量看汇总
                    print(f"[mainforce_state] {code} 计算/写入失败（跳过该只）: "
                          f"{type(e).__name__}: {e}")
        if i % verbose_every == 0:
            print(f"[mainforce_state] {i}/{len(bars_map)} n={n} skip={skipped} "
                  f"fail={failed} ({time.time() - t0:.0f}s)")
    _flush()          # 尾批

    total = len(bars_map)
    # ★ 2026-10-24（BK-12）：空跑必须显式失败——bars_map 全空 = 上游数据断档，
    #   旧实现 `if total and ...` 短路 ⇒ 空跑**静默标记成功**（表面全绿实际没产出，
    #   与"日批任务失败必须显式 raise"纪律冲突）。
    if total == 0:
        raise RuntimeError(
            "主力行为状态计算空跑：bars_map 为空（上游数据断档？）——本轮不标记完成")
    if failed > total * 0.2:
        # 大面积失败 ⇒ 连接/数据源级问题：**不标记完成**，让下一轮断点续传接着补
        raise RuntimeError(
            f"主力行为状态计算失败率过高：{failed}/{total}（已写入 {n}、跳过已存在 "
            f"{skipped}）—— 疑似连接/数据源异常，本轮不标记完成，下轮续传")
    return {"codes": total, "saved": n, "skipped": skipped, "failed": failed,
            "date": today, "seconds": round(time.time() - t0, 1)}


BATCH_SAVE = 500      # 每批写多少行（10 参数/行 × 500 = 5000，远低于 Postgres 65535 上限）


def _save_many(items: list) -> int:
    """批量写当日状态（多行 VALUES）。返回提交行数。

    ★★ 2026-10-27（实测：单只 `_save` **1207ms** vs overlay 计算 **22ms** ⇒ 写库占 95%+）：
      2863 只逐条往返在 Actions→Supabase 上要 20+ 分钟；改批量后 6 次往返 ≈ 10 秒。
      断点续传语义不变：每批**立即提交**，进程中断最多丢一批（下次续传补）。
    """
    if not items:
        return 0
    cols = ("(code, name, date, phase, signal, mult, chip_json, "
            "flow5_amt, flow5_amt_yuan, flow_consec)")
    values_sql = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(items))
    params = []
    for code, name, date, ov in items:
        params += [code, name, date, ov.get("phase"), ov.get("signal"),
                   ov.get("mult", 1.0),
                   json.dumps(ov.get("chip") or {}, ensure_ascii=False),
                   ov.get("flow5_amt"), ov.get("flow5_amt_yuan"), ov.get("flow_consec")]
    if db._use_postgres:
        sql = (f"INSERT INTO mainforce_state {cols} VALUES {values_sql} "
               f"ON CONFLICT (code, date) DO UPDATE SET "
               f"name=EXCLUDED.name, phase=EXCLUDED.phase, signal=EXCLUDED.signal, "
               f"mult=EXCLUDED.mult, chip_json=EXCLUDED.chip_json, "
               f"flow5_amt=EXCLUDED.flow5_amt, "
               f"flow5_amt_yuan=EXCLUDED.flow5_amt_yuan, "
               f"flow_consec=EXCLUDED.flow_consec")
    else:
        sql = f"INSERT OR REPLACE INTO mainforce_state {cols} VALUES {values_sql}"
    db.execute(sql, tuple(params))
    for code, _n, _d, _ov in items:
        _latest_cache["data"].pop(code, None)      # 写入即失效（读缓存）
    return len(items)


def _save(code: str, name: str, date: str, ov: dict) -> None:
    chip_json = json.dumps(ov.get("chip") or {}, ensure_ascii=False)
    if db._use_postgres:
        db.execute("""
            INSERT INTO mainforce_state (code, name, date, phase, signal, mult,
                                         chip_json, flow5_amt, flow5_amt_yuan,
                                         flow_consec)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code, date) DO UPDATE SET
                name=EXCLUDED.name, phase=EXCLUDED.phase, signal=EXCLUDED.signal,
                mult=EXCLUDED.mult, chip_json=EXCLUDED.chip_json,
                flow5_amt=EXCLUDED.flow5_amt,
                flow5_amt_yuan=EXCLUDED.flow5_amt_yuan,
                flow_consec=EXCLUDED.flow_consec
        """, (code, name, date, ov.get("phase"), ov.get("signal"),
              ov.get("mult", 1.0), chip_json, ov.get("flow5_amt"),
              ov.get("flow5_amt_yuan"), ov.get("flow_consec")))
    else:
        db.execute("""
            INSERT OR REPLACE INTO mainforce_state (code, name, date, phase, signal,
                                                    mult, chip_json, flow5_amt,
                                                    flow5_amt_yuan, flow_consec)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (code, name, date, ov.get("phase"), ov.get("signal"),
              ov.get("mult", 1.0), chip_json, ov.get("flow5_amt"),
              ov.get("flow5_amt_yuan"), ov.get("flow_consec")))
    _latest_cache["data"].pop(code, None)   # 写入即失效（读缓存）


def load_latest(codes: list) -> dict:
    """{code: state_dict}——每只取最新日期的一条（30min 进程缓存，见模块注释）。"""
    if not codes:
        return {}
    # ★ 2026-10-10（P1-2）：连续净流入分档的**唯一映射源**在 `flow`（与本模块的
    #   `PHASE_CN` 同思路——在"唯一数据入口"统一补齐，避免各消费方各写一套）。
    from app.mainforce.flow import consec_label as _consec_label
    from app.mainforce.flow import consec_tier as _consec_tier
    now = time.time()
    if _latest_cache["ts"] and now - _latest_cache["ts"] > _LATEST_TTL_SEC:
        _latest_cache.update({"ts": 0.0, "data": {}})
    cached = _latest_cache["data"]
    missing = [c for c in dict.fromkeys(codes) if c not in cached]
    if missing:
        rows = db.fetch("""
            SELECT DISTINCT ON (code) code, name, date, phase, signal, mult,
                   chip_json, flow5_amt, flow5_amt_yuan, flow_consec
            FROM mainforce_state WHERE code = ANY(%s)
            ORDER BY code, date DESC
        """, (missing,))
        for r in rows or []:
            try:
                chip = (json.loads(r["chip_json"])
                        if isinstance(r["chip_json"], str) else (r["chip_json"] or {}))
            except (TypeError, ValueError):
                chip = {}
            cached[r["code"]] = {
                "date": str(r["date"]), "phase": r["phase"], "signal": r["signal"],
                # ★ 2026-09-22：补 `phase_cn` —— 表里只存英文枚举（`phase`），而
                #   详情页/榜单/观察池都要中文名；原先只有 overlay 路径带 `phase_cn`
                #   ⇒ 走"表命中"路径的消费方拿不到（观察池直接渲染英文 `sideways`）。
                #   在**唯一数据入口**统一补齐（`PHASE_CN` 是唯一映射源）。
                #   `phase` 英文保留：程序判定/回测按枚举比较（如 gate 判 markup）。
                "phase_cn": PHASE_CN.get(r["phase"] or "", ""),
                "mult": r["mult"] or 1.0, "chip": chip, "flow5_amt": r["flow5_amt"],
                "flow5_amt_yuan": r.get("flow5_amt_yuan"),
                # ★ 2026-10-10（P1-2）：连续净流入（原孤儿字段）。`flow_consec` 原始值
                #   一并带上（研究/审计用），但**对外标签只认 tier/cn 两个键**（封顶语义）。
                "flow_consec": r.get("flow_consec"),
                "flow_consec_tier": _consec_tier(r.get("flow_consec")),
                "flow_consec_cn": _consec_label(r.get("flow_consec")),
            }
        _latest_cache["ts"] = now
    return {c: cached[c] for c in codes if c in cached}


# signal → 中文（与 `overlay.py:137/145` 的文案一致；前端 tooltip 也用这个）
_SIGNAL_CN = {"distribution": "出货嫌疑", "accum": "主力吸筹区"}


def load_latest_for_pack(codes: list) -> dict:
    """{code: mainforce}——**供产包使用**的完整结构（与 `overlay` 输出同构）。

    与 `load_latest()` 的差别只有一处：把**前端渲染要、但表里没存**的字段补齐。
    表只存 `phase/signal/mult/chip_json/flow5_amt/flow5_amt_yuan/flow_consec`，
    而 `ScoreRank.vue:970` 的 tooltip 直接读 `mainforce.price_pos` /
    `mainforce.winner_ratio`（overlay 是把 chip 里的值提升到顶层才传出去的）。

    ★★ 2026-10-31（用户："评分排行榜的主力行为怎么没有了"）：
      产包链路 `scripts/update_backend_pack_incremental.py` 对 `mainforce`
      **只继承、不计算**（`if p.get("mainforce"): ind["mainforce"] = p["mainforce"]`），
      而唯一会计算它的 `generate_backend_pack.py::compute_mainforce_overlay`
      在 `backend-pack.yml` 里**从未被调用**（workflow 只跑
      `daily_batch --tasks mainflow` 与 `rebuild_pack_struct.py`）。
      ⇒ 一旦某一份包丢了这个字段，之后每天"继承"到的都是"没有"，
        **永远不恢复** —— 单向棘轮，且全程不报错（只有前端那一列变成 `-`）。
      本函数就是这道缺口的**兜底数据源**：`mainforce_state` 里有，就能补回包里。

    ★ 为什么放在这里而不是产包脚本里：`load_latest()` 是本项目约定的
      mainforce **唯一数据入口**（`phase_cn` 补齐、`chip_json` 解析都在此）。
      兜底若各写一套字段映射，迟早再次分叉（`phase_cn` 那次就是这么出问题的）。
    """
    out = {}
    for code, v in (load_latest(codes) or {}).items():
        chip = v.get("chip") or {}
        d = dict(v)
        # 提升到顶层（overlay 同款做法；chip 原样保留，两处都能取到）
        d["price_pos"] = chip.get("price_pos")
        d["winner_ratio"] = chip.get("winner_ratio")
        d["signal_cn"] = _SIGNAL_CN.get(v.get("signal") or "", "")
        # overlay 有、当前无消费方；给默认值只为结构一致（fail-closed 定案）
        d.setdefault("reason", "")
        d.setdefault("active", False)
        out[code] = d
    return out


if __name__ == "__main__":
    import sys
    regime = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(refresh_all(regime=regime), ensure_ascii=False))
