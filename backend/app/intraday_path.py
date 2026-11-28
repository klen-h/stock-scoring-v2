# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】指数**日内分时路径**归档 + 读取（P2-1，PLAN_RESERVE_SIGNALS §6.5）
================================================================================
【为什么必须归档（时钟属性，同 `market_amount` 的教训）】
  腾讯分时**只有当日**（实测）⇒ 不落库 = 明天永远没有历史 ⇒ 日内研究无从谈起。
  本项目已多次吃到"晚一天接入就永远少一天历史"的亏（`zzshare_daily` 文件头同款说明）。
  P1-1 的结论里最缺的正是**日内拼图**（日线看不到"早盘杀跌 → 午后拉升"这种 V 型），
  而"午盘前跌>1% → 午后拉升"这个命题**必须先有分时归档才可检验**。

【数据源与口径】
  · 腾讯分时：`https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=<code>`
    —— 与 `market_amount._fetch_intraday` **同一接口**（那边取 `parts[3]`=累计成交额，
       本模块取 `parts[1]`=价；**不新引数据源、不新增依赖**）。
  · 行格式 `"HHMM 价 累计量 累计额"`（实测，见 `market_amount` 文件头）⇒ 原样归档为
    `[[HHMM, price, cum_vol, cum_amount], ...]`，**不做加工**（避免口径在写入侧就漂移）。
  · ⚠️ 分时接口**不带日期**（盘前/休市返回**上一交易日**序列）⇒ 见下方"落库窗口保护"。

【表】intraday_path(date, code, n, first_hhmm, last_hhmm,
                    open_price/close_price/high_price/low_price, path_json, created_at)
  UNIQUE(date, code)；冗余存 OHLC 便于快速查询（由 path 派生，纯便利字段）。

【★ 落库窗口保护（沿用 `market_amount.save_daily` 的既有取舍）】
  分时接口不带日期 ⇒ 若在**盘前**自动落库，会把"昨天"的序列写到"今天"名下（**静默错位**）。
  ⇒ 只允许 **15:55 之后**自动落库（`date=None`）；**补录必须显式传 `date`**。

【范围与边界】
  · 只归档**指数**（市场级反转命题只需指数路径）。个股分时是另一件事（成本高得多：
    5000 只 × 240 点/日 ≈ 120 万点/日 ⇒ 表体积与抓取量都不可同日而语）。
    要扩到个股时，`path_json` 结构与表主键已通用，只需扩 `CODES`/换调用方。
  · 与 `market_amount_daily` 的分工：那张表存**成交额**序列（沪深两市合计口径，用于量能
    判断），本表存**价**路径（用于日内形态/反转研究）——**两张表口径不同、不要互相替代**。
================================================================================
"""
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

# 归档标的：覆盖"大盘 / 深市 / 创业板 / 宽基 / 小盘"五档，便于做交叉验证
CODES = ("sh000001", "sz399001", "sz399006", "sh000300", "sh000852")
CODE_CN = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000852": "中证1000",
}

_TX_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
_TIMEOUT = 10
_GAP = 0.25                 # 请求间隔（5 个请求，温和限速，别惹 WAF）
_BJ = timezone(timedelta(hours=8))
# 自动落库窗口：15:55 之后（收盘定稿；15:00 收盘后还要等数据源收敛）
_AUTO_FROM = (15, 55)
# ★ 2026-11-28：盘中增量采集窗口 —— 09:31 起（09:30 那一刻序列只有 1 点，
#   且盘前调用返回的是**昨日**完整序列，会把昨天写到今天名下）。
_SESSION_FROM = (9, 31)
_SESSION_TO = (15, 0)


def ensure_table() -> None:
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS intraday_path (
                id BIGSERIAL PRIMARY KEY,
                date DATE NOT NULL,
                code VARCHAR(12) NOT NULL,
                n INTEGER,
                first_hhmm VARCHAR(6),
                last_hhmm VARCHAR(6),
                open_price DOUBLE PRECISION,
                close_price DOUBLE PRECISION,
                high_price DOUBLE PRECISION,
                low_price DOUBLE PRECISION,
                path_json JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_intraday_path UNIQUE (date, code)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_intraday_path_code "
                   "ON intraday_path (code, date)")
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS intraday_path (
                date TEXT NOT NULL, code TEXT NOT NULL, n INTEGER,
                first_hhmm TEXT, last_hhmm TEXT,
                open_price REAL, close_price REAL, high_price REAL, low_price REAL,
                path_json TEXT, created_at TEXT,
                UNIQUE (date, code)
            )
        """)


def _fetch(code: str) -> List[list]:
    """腾讯分时 → [[HHMM, price, cum_vol, cum_amount], ...]（原始行，不加工）。"""
    import requests
    s = requests.Session()
    s.headers.update({"user-agent": "Mozilla/5.0", "referer": "https://gu.qq.com/"})
    r = s.get(_TX_URL, params={"code": code}, timeout=_TIMEOUT)
    rows = ((((r.json() or {}).get("data") or {}).get(code) or {})
            .get("data", {}).get("data") or [])
    out: List[list] = []
    for line in rows:
        p = str(line).split()
        if len(p) < 4:
            continue
        try:
            out.append([str(p[0]), float(p[1]), float(p[2]), float(p[3])])
        except (TypeError, ValueError):
            continue
    return out


def _auto_day(now: Optional[datetime] = None) -> Optional[str]:
    """自动落库的日期（不在窗口内返回 None）。"""
    now = now or datetime.now(_BJ)
    if now.weekday() >= 5:                       # 周末：接口给的是上一交易日序列，别自动写
        return None
    if (now.hour, now.minute) >= _AUTO_FROM or now.hour >= 16:
        return now.strftime("%Y-%m-%d")
    return None


def save_day(date: str = None, codes=None, gap: float = _GAP) -> Dict:
    """归档 `date`（缺省=今天）的分时路径。返回 {ok, fail, date, codes}。

    ★ 非收盘窗口拒绝自动落库（见文件头"落库窗口保护"）；补录请**显式传 date**。
    ★ 幂等：UNIQUE(date, code) + upsert ⇒ 重复跑只覆盖同一日。
    """
    if date is None:
        date = _auto_day()
        if not date:
            return {"ok": 0, "fail": [], "date": "",
                    "error": "非收盘落库窗口（交易日 15:55 之后才自动落库）；补录请显式传 date"}
    ensure_table()
    ok, fail = 0, []
    for c in (codes or CODES):
        try:
            path = _fetch(c)
            if not path:
                fail.append("%s:空" % c)
                continue
            prices = [p[1] for p in path]
            payload = json.dumps(path, ensure_ascii=False)
            if db._use_postgres:
                db.execute("""
                    INSERT INTO intraday_path (date, code, n, first_hhmm, last_hhmm,
                                               open_price, close_price, high_price,
                                               low_price, path_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (date, code) DO UPDATE SET
                        n=EXCLUDED.n, first_hhmm=EXCLUDED.first_hhmm,
                        last_hhmm=EXCLUDED.last_hhmm, open_price=EXCLUDED.open_price,
                        close_price=EXCLUDED.close_price, high_price=EXCLUDED.high_price,
                        low_price=EXCLUDED.low_price, path_json=EXCLUDED.path_json
                """, (date, c, len(path), path[0][0], path[-1][0], prices[0],
                      prices[-1], max(prices), min(prices), payload))
            else:
                db.execute("""
                    INSERT OR REPLACE INTO intraday_path
                    (date, code, n, first_hhmm, last_hhmm, open_price, close_price,
                     high_price, low_price, path_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (date, c, len(path), path[0][0], path[-1][0], prices[0],
                      prices[-1], max(prices), min(prices), payload,
                      datetime.now(_BJ).isoformat()))
            ok += 1
        except Exception as e:
            fail.append("%s:%s" % (c, str(e)[:40]))
        time.sleep(gap)
    return {"ok": ok, "fail": fail, "date": date, "codes": len(codes or CODES)}


def _in_session(now: Optional[datetime] = None) -> bool:
    """是否在**盘中采集窗口**（交易日 09:31–15:00）。"""
    now = now or datetime.now(_BJ)
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return _SESSION_FROM <= hm <= _SESSION_TO


def save_intraday(now: Optional[datetime] = None, codes=None, gap: float = _GAP) -> Dict:
    """盘中**增量**落库（2026-11-28）。

    ★ 为什么必须与 `save_day()` 分开（两者不能合并）：
      · `save_day()` —— 15:55 后整段落库，允许 `date=None` 自动；
        **盘中调用会被拒**（它的窗口保护就是为了防"盘前把昨日序列写成今天"）。
      · `save_intraday()` —— 只在 **09:31–15:00** 之间写当日，且**只增不减**：
        库内点数 ≥ 本次返回点数 ⇒ 跳过（防盘中残缺序列覆盖收盘定稿）。

    ★ 为什么需要它：11-27 实测主升发生在 **13:00–13:10**
      （创业板 13:00 +3.79% → 13:01 +5.68% → 13:05 +7.37%），
      而政策快讯 **13:24** 才到 —— 早 19 分钟。但分时是当晚 22:10 才回补的
      ⇒ 当天盘中**根本没有数据可用**，这个信号等于不存在。

    ★ 成本：5 个 code ≈ 36 KB/次。按调度器时点密度约 33 次/日 ⇒ **~1.2 MB/日**。
    """
    now = now or datetime.now(_BJ)
    if not _in_session(now):
        return {"ok": 0, "fail": [], "date": "",
                "error": "非盘中窗口（交易日 09:31–15:00）；补录请用 save_day(date=...)"}
    date = now.strftime("%Y-%m-%d")
    ensure_table()

    # 已有各 code 点数（只增不减的判据）
    have: Dict[str, int] = {}
    try:
        for r in (db.fetch("SELECT code, n FROM intraday_path WHERE date = %s", (date,)) or []):
            have[str(r.get("code"))] = int(r.get("n") or 0)
    except Exception as e:
        print(f"[intraday] intraday existing load failed: {str(e)[:80]}")    # ASCII

    ok, skip, fail = 0, [], []
    for c in (codes or CODES):
        try:
            path = _fetch(c)
            if not path:
                fail.append("%s:空" % c)
                continue
            # ★ 只增不减：盘中残缺序列不许覆盖已完整的当日记录
            if len(path) <= have.get(c, 0):
                skip.append("%s:%d<=%d" % (c, len(path), have.get(c, 0)))
                continue
            prices = [p[1] for p in path]
            payload = json.dumps(path, ensure_ascii=False)
            if db._use_postgres:
                db.execute("""
                    INSERT INTO intraday_path (date, code, n, first_hhmm, last_hhmm,
                                               open_price, close_price, high_price,
                                               low_price, path_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (date, code) DO UPDATE SET
                        n=EXCLUDED.n, first_hhmm=EXCLUDED.first_hhmm,
                        last_hhmm=EXCLUDED.last_hhmm, open_price=EXCLUDED.open_price,
                        close_price=EXCLUDED.close_price, high_price=EXCLUDED.high_price,
                        low_price=EXCLUDED.low_price, path_json=EXCLUDED.path_json
                """, (date, c, len(path), path[0][0], path[-1][0], prices[0],
                      prices[-1], max(prices), min(prices), payload))
            else:
                db.execute("""
                    INSERT OR REPLACE INTO intraday_path
                    (date, code, n, first_hhmm, last_hhmm, open_price, close_price,
                     high_price, low_price, path_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (date, c, len(path), path[0][0], path[-1][0], prices[0],
                      prices[-1], max(prices), min(prices), payload,
                      datetime.now(_BJ).isoformat()))
            have[c] = len(path)
            ok += 1
        except Exception as e:
            fail.append("%s:%s" % (c, str(e)[:40]))
        time.sleep(gap)
    return {"ok": ok, "skip": skip, "fail": fail, "date": date,
            "codes": len(codes or CODES)}


def load(date: str, code: str) -> List[list]:
    """读某一日某指数的分时路径 → [[HHMM, price, cum_vol, cum_amount], ...]（缺返回 []）。"""
    try:
        rows = db.fetch("SELECT path_json FROM intraday_path WHERE date = %s AND code = %s",
                        (date, code))
    except Exception as e:
        print(f"[intraday] 读取失败 {date} {code}: {str(e)[:80]}")     # ASCII（铁律⑥）
        return []
    if not rows:
        return []
    raw = rows[0].get("path_json")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return data or []


def coverage(code: str = "sh000300") -> Dict:
    """归档覆盖（供自检/数据断档看板）：{days, first, last}。"""
    try:
        r = db.fetch_one("SELECT COUNT(*) AS n, MIN(date) AS a, MAX(date) AS b "
                         "FROM intraday_path WHERE code = %s", (code,))
    except Exception:
        return {"days": 0, "first": "", "last": ""}
    r = r or {}
    return {"days": int(r.get("n") or 0),
            "first": str(r.get("a") or "")[:10], "last": str(r.get("b") or "")[:10]}


def days_for(code: str = "sh000300") -> List[str]:
    """该指数已归档的全部交易日（升序）。"""
    try:
        rows = db.fetch("SELECT DISTINCT date FROM intraday_path WHERE code = %s "
                        "ORDER BY date ASC", (code,))
    except Exception as e:
        print(f"[intraday] days_for 失败: {str(e)[:80]}")
        return []
    return [str(r["date"])[:10] for r in (rows or [])]
