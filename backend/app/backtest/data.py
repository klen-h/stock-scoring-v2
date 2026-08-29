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


def save_prices(code: str, name: str, rows: list) -> int:
    """批量幂等写入（code+date 冲突跳过），返回写入行数。"""
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
        if db._use_postgres:
            sql = (f"INSERT INTO backtest_prices (code, name, date, open, high, low, close, volume) "
                   f"VALUES {values_sql} ON CONFLICT (code, date) DO NOTHING")
        else:
            sql = (f"INSERT OR IGNORE INTO backtest_prices (code, name, date, open, high, low, close, volume) "
                   f"VALUES {values_sql}")
        db.execute(sql, tuple(params))
        n += len(batch)
    return n


def load_prices(code: str, start: str = None, end: str = None) -> list:
    """读取日线（升序）。start/end 形如 '2023-08-01'。"""
    sql = "SELECT * FROM backtest_prices WHERE code = %s"
    params = [code]
    if start:
        sql += " AND date >= %s"
        params.append(start)
    if end:
        sql += " AND date <= %s"
        params.append(end)
    sql += " ORDER BY date ASC"
    rows = db.fetch(sql, tuple(params))
    return [{"date": r["date"], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]}
            for r in rows]


def get_all_codes() -> list:
    """已回填的代码列表（含名称）。"""
    rows = db.fetch("SELECT DISTINCT code, name FROM backtest_prices ORDER BY code")
    return [{"code": r["code"], "name": r["name"]} for r in rows]
