"""
================================================================================
【文件作用】回测数据层：backtest_prices 表读写 + 历史日线拉取（东财+腾讯双源）
================================================================================
数据表 backtest_prices(code, name, date, open, high, low, close, volume)，
UNIQUE(code, date)，幂等写入。回测统一从这里读数据（不依赖实时接口）。

拉取策略（东财为主、腾讯兜底）：
  - 东财 push2his 接口免鉴权，一次返回全历史日线（约 3 年 750 条）
    字段顺序：date,open,close,high,low,volume,amount,振幅,涨跌幅,涨跌额,换手率
  - 东财对连续请求会断连（RemoteDisconnected），失败自动降级腾讯 fqkline
    （count=800 可一次拉全 3 年；ETF/股票返回 qfqday，指数返回 day）
  - 回填脚本可通过 DISABLE_EASTMONEY 全局跳过东财（连续失败时）
================================================================================
"""

import bisect
import time
from datetime import datetime, timedelta

import requests

from app.database import db

_EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
})

_MAX_RETRIES = 2          # 单源拉取失败重试次数
_RETRY_BACKOFF = [2, 4]   # 重试等待（秒），东财对连续请求会断连
DISABLE_EASTMONEY = False # 东财连续失败后置 True，全局走腾讯源
_em_failures = 0          # 东财连续失败计数（>=3 自动禁用东财）
_tencent_blocked_until = 0.0      # 腾讯 WAF 冷却截止时间（时间戳）
_TENCENT_WAF_COOLDOWN = 300       # 腾讯 WAF 拦截后的冷却秒数


def to_secid(code: str) -> str:
    """腾讯风格代码（sh510300/sz159915）或裸 6 位数字 → 东财 secid。"""
    c = code.lower()
    if c.startswith(("sh", "sz", "bj")):
        return ("1." if c[:2] == "sh" else "0.") + c[2:]
    if len(c) == 6:
        return ("1." if c[0] == "6" else "0.") + c
    return c


def _tencent_code(code: str) -> str:
    """转腾讯风格代码：sh510300 → sh510300；裸 6 位 → 首字符 6→sh 否则 sz。"""
    c = code.lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if len(c) == 6:
        return ("sh" if c[0] == "6" else "sz") + c
    return c


def _parse_klines(code: str, raw_items: list) -> list:
    """腾讯 K 线条目 [date, open, close, high, low, volume, ...] → dict 列表。"""
    out = []
    for item in raw_items or []:
        if len(item) < 6:
            continue
        try:
            out.append({
                "date": item[0],
                "open": float(item[1]), "close": float(item[2]),
                "high": float(item[3]), "low": float(item[4]),
                "volume": float(item[5]),
            })
        except (ValueError, IndexError):
            continue
    return out


def fetch_history_tencent(code: str, years: int = 3, start: str = None) -> list:
    """腾讯 fqkline 兜底源：拉取日线（count=800；start 给定时只取该日之后做增量）。
    返回 [{date, open, high, low, close, volume}]（升序）。

    WAF 保护：腾讯对连续 K 线请求返回 501（防火墙拦截），此时进入全局冷却，
    冷却期内直接放弃请求——否则数百只股票连续重试会把 IP 封得更久。
    """
    global _tencent_blocked_until
    if time.time() < _tencent_blocked_until:
        return []
    tc = _tencent_code(code)
    beg = start or (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    params = {"param": f"{tc},day,{beg},{end},800,qfq"}
    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            r = _SESSION.get(_TENCENT_KLINE_URL, params=params, timeout=15)
            # 501 = 腾讯 WAF 拦截：触发全局冷却，冷却期内不再打腾讯
            if r.status_code == 501:
                _tencent_blocked_until = time.time() + _TENCENT_WAF_COOLDOWN
                print(f"[backtest] {code} 腾讯WAF拦截(501)，全局冷却 {_TENCENT_WAF_COOLDOWN}s")
                return []
            r.raise_for_status()
            raw = ((r.json() or {}).get("data") or {}).get(tc, {})
            # ETF/股票返回 qfqday，指数返回 day
            items = raw.get("qfqday") or raw.get("day") or []
            if items:
                return _parse_klines(code, items)
            last_err = "空数据"
        except Exception as e:
            last_err = e
        if attempt < _MAX_RETRIES - 1:
            time.sleep(_RETRY_BACKOFF[attempt])
    print(f"[backtest] {code} 腾讯兜底失败（重试 {_MAX_RETRIES} 次）: {last_err}")
    return []


def fetch_history(code: str, years: int = 3, start: str = None) -> list:
    """拉取日线（前复权）：东财为主，失败自动降级腾讯。
    start 给定时只取该日之后（增量回填）；返回升序 dict 列表。"""
    global DISABLE_EASTMONEY, _em_failures
    if not DISABLE_EASTMONEY:
        beg = (start.replace("-", "") if start
               else time.strftime("%Y%m%d", time.localtime(time.time() - years * 365 * 86400)))
        params = {
            "secid": to_secid(code),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101", "fqt": "1", "beg": beg, "end": "20500101",
        }
        last_err = None
        for attempt in range(_MAX_RETRIES):
            try:
                r = _SESSION.get(_EASTMONEY_KLINE_URL, params=params, timeout=15)
                r.raise_for_status()
                klines = ((r.json() or {}).get("data") or {}).get("klines") or []
                if klines:
                    _em_failures = 0
                    return _parse_klines(code, [line.split(",") for line in klines])
                last_err = "空数据"
            except Exception as e:
                last_err = e
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
        _em_failures += 1
        if _em_failures >= 3:
            DISABLE_EASTMONEY = True
            print("[backtest] 东财连续失败，后续全部走腾讯源")
        else:
            print(f"[backtest] {code} 东财拉取失败（重试 {_MAX_RETRIES} 次）: {last_err}，降级腾讯")
        return fetch_history_tencent(code, years, start)
    return fetch_history_tencent(code, years, start)


def save_prices(code: str, name: str, rows: list, replace: bool = False) -> int:
    """批量写入，返回**提交的行数**（不区分插入/更新/跳过；`DO NOTHING` 下跳过的不计入实际变更）。

    replace=False（默认，**增量回填语义**）：`(code, date)` 冲突**跳过** —— 只补新日期，
      不触碰已有行。快，且不会因源抖动误改历史。
    replace=True（**修复语义**）：冲突则**覆盖**（`DO UPDATE SET ...` / `INSERT OR REPLACE`）。

    ★ 2026-09-18（重要更正）：本函数原先只支持 `DO NOTHING`，而**方案 C / `--rebuild`
      都依赖"全量重拉即覆盖"** —— 那是错的：已存在的行永远不会被更新，旧价改不掉。
      故新增 `replace` 参数。需要覆盖的仅有两条路径：
        · `backfill_history.rebuild_all_full()`（一次性重建）
        · `backfill_history.backfill()` 里 `_basis_changed()` 命中的分支
      正常增量回填仍用默认 False。
    """
    if not rows:
        return 0
    n = 0
    BATCH = 500
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        values_sql = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s)"] * len(batch))
        params = []
        for r in batch:
            params += [code, name, r["date"], r["open"], r["high"], r["low"],
                       r["close"], r["volume"]]
        cols = ("(code, name, date, open, high, low, close, volume)")
        if db._use_postgres:
            if replace:
                sql = (f"INSERT INTO backtest_prices {cols} VALUES {values_sql} "
                       f"ON CONFLICT (code, date) DO UPDATE SET "
                       f"name=EXCLUDED.name, open=EXCLUDED.open, high=EXCLUDED.high, "
                       f"low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume")
            else:
                sql = (f"INSERT INTO backtest_prices {cols} VALUES {values_sql} "
                       f"ON CONFLICT (code, date) DO NOTHING")
        else:
            verb = "INSERT OR REPLACE INTO" if replace else "INSERT OR IGNORE INTO"
            sql = f"{verb} backtest_prices {cols} VALUES {values_sql}"
        db.execute(sql, tuple(params))
        n += len(batch)
    if n:
        invalidate_price_caches()   # ★ 审查 P2-⑳：回填落库即失效进程内价格缓存
    return n


# ★ 进程内缓存（2026-09-11 egress 治理）：`SELECT * FROM backtest_prices WHERE
#   code = $1` 是 Supabase 21 天里返回行数最多的语句（18037 次调用 / 1112 万行，
#   单次均值 617 行）——排行榜/详情/战法闸门会在同一次页面访问里反复要同一只
#   股票的日线。日线每日只回填一次，30 分钟内复用完全安全。
#   只缓存「结果不大」的（≤ 900 根），避免长驻进程内存被大批量请求顶爆。
_PRICES_CACHE = {}        # {(code, start, end): (ts, bars)}
_PRICES_TTL = 1800        # 30 分钟
_PRICES_CACHE_MAX = 200


def invalidate_price_caches() -> None:
    """★ 审查 P2-⑳：backtest_prices 落库（save_prices/回填）后调用——
    本模块 30min 缓存与 strategies 模块 6h 缓存此前都无「回填即失效」，
    晚间撮合/白名单基准最长读到回填前的旧价。strategies 用局部导入防循环。"""
    _PRICES_CACHE.clear()
    try:
        from app.backtest import strategies as _st
        _st.invalidate_prices_cache()
    except Exception:
        pass


def _prices_cache_get(key):
    hit = _PRICES_CACHE.get(key)
    if not hit:
        return None
    ts, bars = hit
    if time.time() - ts > _PRICES_TTL:
        _PRICES_CACHE.pop(key, None)
        return None
    return bars


def _prices_cache_put(key, bars):
    if len(bars) > 900:                 # 超大结果不缓存（内存优先）
        return
    if len(_PRICES_CACHE) >= _PRICES_CACHE_MAX:
        # 简单淘汰：丢最旧插入的 1/4（dict 保序）
        for k in list(_PRICES_CACHE)[: _PRICES_CACHE_MAX // 4]:
            _PRICES_CACHE.pop(k, None)
    _PRICES_CACHE[key] = (time.time(), bars)


def load_prices(code: str, start: str = None, end: str = None) -> list:
    """读取日线（升序）。start/end 形如 '2023-08-01'。"""
    key = (code, start, end)
    cached = _prices_cache_get(key)
    if cached is not None:
        return cached
    # ★ DATA_SOURCE=pack/local：读数据包（零 Supabase 流量）。
    #   ★ pack 未命中（[]）必须回退 DB：数据包只覆盖股票池（6 位代码），
    #     指数基准（sh000300）与宏观 ETF 不在包内——漏了这一步，pack/local
    #     模式下 regime 判定直接报"沪深300 历史数据不足"（2026-09-06 实测）
    try:
        from app import pack_source
        if pack_source.enabled():
            bars = pack_source.get_prices(code, start, end)
            if bars:
                _prices_cache_put(key, bars)
                return bars
    except Exception:
        pass

    # ★ 显式列（2026-09-11）：原来是 SELECT * —— id/code/name 纯属白烧 egress，
    #   一次 600+ 行的查询里这三列占传输量约 1/3。
    sql = ("SELECT date, open, high, low, close, volume FROM backtest_prices "
           "WHERE code = %s")
    params = [code]
    if start:
        sql += " AND date >= %s"
        params.append(start)
    if end:
        sql += " AND date <= %s"
        params.append(end)
    sql += " ORDER BY date ASC"
    rows = db.fetch(sql, tuple(params))
    bars = [{"date": r["date"], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]}
            for r in rows]
    _prices_cache_put(key, bars)
    return bars


# ── ★ 2026-09-18：「源数据异常日」读侧接口 ──────────────────────────────────
# 背景（体检发现）：120 只（16.3%）存在**制度上不可能**的单日涨跌 —— |chg| 超过该品种
#   涨跌停上限，共 334 处 / 0.062% 的 bar（2024 年占 75%）。已确认**在源里就存在**
#   （直接抓腾讯原始 `qfq` 序列同样是 −12.18% / +16.00%，同期别的股票正常），
#   与拼接/解析/复权计算无关 ⇒ 属**不可修复的源问题**。
# 处置（最保守）：**标记而非改数据**。清单由
#   `python scripts/audit_backtest_data.py --write-anomalies` 落库到 `price_anomalies`；
#   回测可**自愿**调用下面的函数，识别「持仓窗口跨越异常日」的样本并剔除/告警。
#   **不调用则行为与从前完全一致** —— 本项目不擅自改变既有回测结论的口径。
def anomalies_in(code: str = None, start: str = None, end: str = None) -> list:
    """查询「源数据异常日」（code=None 表示全市场）。

    返回 [{code, date, chg, limit_pct}, ...]（升序）。
    **fail-open**：表不存在 / 查询异常一律返回 []，绝不因它中断回测。
    """
    try:
        sql = "SELECT code, date, chg, limit_pct FROM price_anomalies WHERE 1=1"
        params = []
        if code:
            sql += " AND code = %s"
            params.append(code)
        if start:
            sql += " AND date >= %s"
            params.append(start)
        if end:
            sql += " AND date <= %s"
            params.append(end)
        sql += " ORDER BY code, date"
        rows = db.fetch(sql, tuple(params)) if params else db.fetch(sql)
        return [{"code": r["code"], "date": str(r["date"]),
                 "chg": r["chg"], "limit_pct": r["limit_pct"]} for r in (rows or [])]
    except Exception:
        return []


def has_anomaly(code: str, start: str, end: str) -> bool:
    """`code` 在 [start, end] 内是否有源数据异常日（供回测剔除跨窗口样本用）。

    ⚠️ **逐样本调用会被 N 倍放大**（每次都发一条 SQL）。回测样本动辄数万条
    ⇒ 请改用在 `anomaly_index()` 上一次取回、内存判定的 `window_has_anomaly()`。
    """
    return bool(anomalies_in(code, start, end))


_ANOMALY_INDEX = None


def anomaly_index() -> dict:
    """{code: [date, ...]（升序）} —— 全市场异常日索引（进程内缓存，只查一次库）。

    异常日全表只有数百行（2026-09-18 体检：120 只 / 334 处）⇒ 一次取回、
    内存二分判定，才是回测侧的正确用法（对比：逐样本 `has_anomaly()` = N 条 SQL）。

    **fail-open**：表不存在 / 查询失败返回 {} ⇒ 语义等同"无异常日"，
    回测行为与接入前**完全一致**（既不中断、也不改变既有结论口径）。
    """
    global _ANOMALY_INDEX
    if _ANOMALY_INDEX is None:
        idx = {}
        for a in anomalies_in():
            idx.setdefault(a["code"], []).append(a["date"])
        for v in idx.values():
            v.sort()
        _ANOMALY_INDEX = idx
    return _ANOMALY_INDEX


def window_has_anomaly(idx: dict, code: str, start: str, end: str) -> bool:
    """`code` 在 [start, end]（含端点）内是否有源异常日。

    与 `has_anomaly()` 同语义但**不查库**（`idx` 由 `anomaly_index()` 一次构建）。
    日期是 `YYYY-MM-DD` 定长字符串 ⇒ 字典序即时间序，可用 bisect。
    """
    days = idx.get(code)
    if not days:
        return False
    i = bisect.bisect_left(days, start)
    return i < len(days) and days[i] <= end


def get_all_codes() -> list:
    """已回填的代码列表（含名称）。"""
    # ★ DATA_SOURCE=pack/local：数据包的代码清单；空则回退 DB（同 load_prices）
    try:
        from app import pack_source
        if pack_source.enabled():
            codes = pack_source.get_codes()
            if codes:
                return [{"code": c, "name": pack_source.get_name_cap(c)[0]}
                        for c in codes]
    except Exception:
        pass

    rows = db.fetch("SELECT DISTINCT code, name FROM backtest_prices ORDER BY code")
    return [{"code": r["code"], "name": r["name"]} for r in rows]
