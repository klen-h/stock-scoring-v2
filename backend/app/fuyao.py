# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】同花顺金融数据 API（fuyao.aicubes.cn）客户端 —— 外部行情/统计源
================================================================================
【为什么有它】（2026-10-28 实测后接入，来源 docs/fuyao/llms-full.txt）
  本项目原来的全市场数据全靠自建池：`tencent._ALL_CODES` 是"深 5999 + 沪 6000"
  的**猜测代码段**（约 12000 个候选，其中大量空号），刷新要 41 批（增量）/150 批（全量），
  且池子不含北交所、默认排除 ST ⇒ 实测**涨跌家数缺 36%、成交额缺 40%**（tencent.py:33-36）。
  fuyao 实测（2026-10-28）：
    · `prices/snapshot?limit=10000` → **一次请求 2.1s 返回 5579 只全市场快照**（含成交额）；
    · `meta/tickers/list?asset_type=a-share&limit=10000` → **一次请求 5579 只真实代码 + 中文名**；
    · `special-data/limit-up-statistics` → 封板率/连板梯队；`limit-up-pool` → 涨停原因/封单额；
    · `dump/market-dumps/daily-k/download-url` → 10 年全市场日 K Parquet（API Key 可直接下）。
  ⇒ 统计类指标（涨跌家数/成交额/宽度）改读它：**请求 41 批 → 1 次，口径 3236 只 → 5579 只**。

【⚠️ 边界：它**不能**替代什么】
  文档明确「该能力暂未开放外部接入」的：**主力资金 capital-flow**、宽基 ETF 净流入、
  高频动向、市场成交额预测。⇒ `mainflow`/`mainforce_watch`/`etf_share_watch`/
  `market_amount` **一律保持现有数据源**，不要试图接。
  另外 fuyao 快照**不含** `pe/pb/turnover_rate/market_cap/float_cap/volume_ratio/bid_diff`
  ⇒ **不能用它替换 `tencent._cache["stocks"]`**（评分市值因子、异动量比/委比都会归零）。
  它只服务「聚合统计」，个股明细仍走腾讯。

【设计纪律】
  1. **fail-open**：任何异常/业务错误/限流都返回 None，调用方回退原有数据源；
  2. **一键关闭**：`FUYAO_ENABLED=0` 或未配 `FUYAO_API_KEY` ⇒ `enabled()` 为 False；
  3. **Key 安全**：只从环境变量读，**绝不写日志、不入库、不回传前端**；
  4. **限流礼貌**：串行 + 最小间隔 + 进程内 TTL 缓存；429/`code=4001` 进入冷却期，
     冷却期内直接返回 None（不排队、不重试），避免"限流时雪上加霜"；
  5. **口径披露**：切换来源的对外字段必须带 `*_src`（见 `routers/market.py` 的
     `total_amount_src` 先例），前端不静默换口径。
================================================================================
"""
import os
import threading
import time
from typing import Dict, List, Optional

import requests

BASE = "https://fuyao.aicubes.cn"
_API_KEY = (os.environ.get("FUYAO_API_KEY") or "").strip()
_ENABLED_FLAG = (os.environ.get("FUYAO_ENABLED", "1").strip().lower()
                 not in ("0", "false", "no", "off"))

# 最小调用间隔（秒）：全站"不限累计"，但会动态限流；串行 + 轻间隔足够礼貌
_MIN_INTERVAL = float(os.environ.get("FUYAO_MIN_INTERVAL") or 0.25)
# 限流冷却（秒）：命中 429 / code=4001 后静默这么久
_COOLDOWN_SEC = float(os.environ.get("FUYAO_COOLDOWN_SEC") or 60)

_CACHE: Dict[str, tuple] = {}          # cache_key -> (ts, data)
_LOCK = threading.Lock()
_last_call = [0.0]
_cooldown_until = [0.0]
# ★★ 2026-10-29（线上日志：`code=5003 /limit-up-statistics` 每 60s 打一次、一小时几十条）：
#   业务错误（code>=5000，如"该时段无数据 / 无权限"）此前**不进任何冷却** ⇒ 每个轮询周期
#   都真打一次外呼，既浪费配额又刷屏。
#   ⚠️ 不能用**全局**冷却：同一时段 `limit-up-pool` 是正常的（板块连板标签靠它），
#      全局冷却会把好的端点一起挡掉 ⇒ 必须**按 path 冷却**。
_PATH_COOLDOWN_SEC = float(os.environ.get("FUYAO_PATH_COOLDOWN_SEC") or 300)
_path_cooldown: dict = {}       # path → 冷却截止时间戳
_STATS = {"calls": 0, "errors": 0, "last_error": "", "last_ms": 0,
          "last_ok_at": 0.0, "bytes": 0}

_session = requests.Session()
_session.headers.update({"X-api-key": _API_KEY, "User-Agent": "stock-scoring/fuyao"})


def enabled() -> bool:
    """是否启用（开关开 + 配了 Key）。"""
    return bool(_ENABLED_FLAG and _API_KEY)


def _now() -> float:
    return time.time()


def _cache_key(path: str, params: Optional[dict]) -> str:
    if not params:
        return path
    return path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))


def cache_get(path: str, params: Optional[dict], ttl: float):
    """进程内 TTL 缓存读取（缓存的是信封里的 `data`，不是整个响应）。"""
    if ttl <= 0:
        return None
    k = _cache_key(path, params)
    with _LOCK:
        hit = _CACHE.get(k)
        if hit and _now() - hit[0] < ttl:
            return hit[1]
        if hit:
            _CACHE.pop(k, None)
    return None


def _cache_put_key(k: str, data) -> None:
    with _LOCK:
        _CACHE[k] = (_now(), data)
        # 简单上限：只留最近 64 个 key（本项目调用的端点很少）
        if len(_CACHE) > 64:
            for old in sorted(_CACHE, key=lambda x: _CACHE[x][0])[:16]:
                _CACHE.pop(old, None)


def _cache_put(path: str, params: Optional[dict], data) -> None:
    _cache_put_key(_cache_key(path, params), data)


def get(path: str, params: Optional[dict] = None, ttl: float = 60,
        timeout: float = 20) -> Optional[dict]:
    """调用一个 fuyao 端点，返回信封里的 `data`（dict）。

    **fail-open**：未启用 / 冷却中 / 网络异常 / 业务 code!=0 ⇒ 一律 None。
    """
    if not enabled():
        return None
    cached = cache_get(path, params, ttl)
    if cached is not None:
        return cached
    if _now() < _cooldown_until[0]:
        return None                       # 限流冷却期：直接放弃，让调用方走旧路径
    if _now() < _path_cooldown.get(path, 0.0):
        return None                       # 本端点业务错误冷却中（其它端点不受影响）
    # 串行 + 最小间隔（多线程下也保证不并发打满）
    with _LOCK:
        wait = _MIN_INTERVAL - (_now() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = _now()
    t0 = _now()
    try:
        r = _session.get(BASE + path, params=params or {}, timeout=timeout)
    except Exception as e:
        _STATS["errors"] += 1
        _STATS["last_error"] = f"net: {str(e)[:80]}"
        print(f"[fuyao] request failed {path}: {str(e)[:80]}")     # ASCII（铁律⑥）
        return None
    _STATS["calls"] += 1
    _STATS["last_ms"] = int((_now() - t0) * 1000)
    _STATS["bytes"] += len(r.content or b"")
    if r.status_code == 429:
        _cooldown_until[0] = _now() + _COOLDOWN_SEC
        _STATS["errors"] += 1
        _STATS["last_error"] = "http 429 (rate limited)"
        print(f"[fuyao] 429 rate limited -> cooldown {_COOLDOWN_SEC:.0f}s")
        return None
    if r.status_code != 200:
        _STATS["errors"] += 1
        _STATS["last_error"] = f"http {r.status_code}"
        print(f"[fuyao] http {r.status_code} {path}")
        return None
    try:
        body = r.json()
    except Exception:
        _STATS["errors"] += 1
        _STATS["last_error"] = "bad json"
        return None
    code = body.get("code")
    if code == 4001:
        _cooldown_until[0] = _now() + _COOLDOWN_SEC
        _STATS["errors"] += 1
        _STATS["last_error"] = "code=4001 (rate limited)"
        print(f"[fuyao] code=4001 rate limited -> cooldown {_COOLDOWN_SEC:.0f}s")
        return None
    if code != 0:
        _STATS["errors"] += 1
        _STATS["last_error"] = f"code={code} {str(body.get('message') or '')[:60]}"
        # ★ 2026-10-29：服务端业务错误（>=5000）⇒ **按端点**冷却，避免每轮轮询都白打一次；
        #   4001（限流）在上面已走全局冷却，这里只处理"这个端点这段时间不可用"。
        try:
            _is_srv_err = int(code) >= 5000
        except (TypeError, ValueError):
            _is_srv_err = False
        if _is_srv_err:
            _path_cooldown[path] = _now() + _PATH_COOLDOWN_SEC
            print(f"[fuyao] business error code={code} {path} "
                  f"-> path cooldown {_PATH_COOLDOWN_SEC:.0f}s")
        else:
            print(f"[fuyao] business error code={code} {path}")
        return None
    data = body.get("data") or {}
    _STATS["last_ok_at"] = _now()
    _cache_put(path, params, data)
    return data


# ── 代码格式转换（fuyao 用 `600519.SH`，本项目用 `sh600519`）────────────────

def to_thscode(code: str) -> str:
    """项目格式 → thscode：`sh600519` / `600519` → `600519.SH`。

    ⚠️ **带前缀的输入永远优先**（`sh000001` → `000001.SH` 上证指数）。
       纯 6 位只按**个股**板块段推断，`000001` 会被判成 `000001.SZ`（平安银行）——
       指数有歧义（上证指数也是 000001），所以传指数时必须带前缀。
    """
    c = str(code or "").strip()
    if not c:
        return ""
    if "." in c:
        return c.upper()
    low = c.lower()
    if len(low) >= 8 and low[:2] in ("sh", "sz", "bj"):
        return f"{c[2:]}.{low[:2].upper()}"
    if c.startswith("92") or c[0] in ("4", "8"):
        return f"{c}.BJ"
    if c[0] == "6":
        return f"{c}.SH"
    return f"{c}.SZ"


def from_thscode(thscode: str) -> tuple:
    """thscode → (6 位代码, 项目前缀)：`600519.SH` → ("600519", "sh")。"""
    s = str(thscode or "").strip().upper()
    if "." in s:
        ticker, ex = s.split(".", 1)
        return ticker, ex.lower()
    return s, ""


# ── 常用能力（带缓存）──────────────────────────────────────────────────────

def tickers(ttl: float = 43200) -> Dict[str, dict]:
    """全 A 股代码表：`{ "600519": {thscode,name,exchange,list_date,...} }`（默认缓存 12h）。

    ★ 替代 `tencent._ALL_CODES`（12000 个猜测候选）：实测一次 5579 只、含中文名。
    """
    key = "/api/meta/tickers/list"
    params = {"asset_type": "a-share", "limit": 10000, "offset": 0}
    # ⚠️ 转换结果用**独立 cache key**（`#map`）：`get()` 已经把信封 `data` 缓存到
    #   `key?params` 上了，两种结构共用同一个 key 会让下次读到信封（`.get('exchange')`
    #   就炸成 `'int' object has no attribute 'get'`，实测踩过）。
    mkey = key + "#map"
    with _LOCK:
        hit = _CACHE.get(mkey)
        if hit and _now() - hit[0] < ttl:
            return hit[1]
    d = get(key, params, ttl=0)
    if not d:
        return {}
    out: Dict[str, dict] = {}
    for it in (d.get("item") or []):
        if not isinstance(it, dict):
            continue
        t = str(it.get("ticker") or "").strip()
        if t:
            out[t] = it
    if out:
        _cache_put_key(mkey, out)
    return out


def snapshot_all(ttl: float = 45) -> List[dict]:
    """全市场行情快照（一次请求，实测 2.1s / 5579 只）。失败返回 []。"""
    d = get("/api/a-share/prices/snapshot",
            {"limit": 10000, "offset": 0}, ttl=ttl, timeout=40)
    return (d or {}).get("item") or []


def market_width(ttl: float = 45) -> Optional[Dict]:
    """**全市场宽度**：一次快照算出涨跌家数 / 成交额 / 均值中位数。

    ★ 这是 `routers/market.market_overview` 的新数据源（口径 5579 只，含北交所/ST）。
    返回 None ⇒ 调用方回退原有的"遍历内存池"实现（fail-open）。
    """
    def _f(v) -> float:
        """脏值容错：上游个别字段可能是 null/字符串，按 0 计（不抛、不静默 pass）。"""
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    items = snapshot_all(ttl)
    if not items:
        return None
    up = down = flat = suspended = 0
    chgs: List[float] = []
    amount = 0.0
    for it in items:
        vol = it.get("volume") or 0
        chg = it.get("price_change_ratio_pct")
        amount += _f(it.get("turnover"))
        if not vol:
            suspended += 1            # 停牌：有行无成交（不参与涨跌判定，但要披露）
        if chg is None:
            continue
        try:
            c = float(chg)
        except (TypeError, ValueError):
            continue
        chgs.append(c)
        if c > 0:
            up += 1
        elif c < 0:
            down += 1
        else:
            flat += 1
    if not chgs:
        return None
    chgs.sort()
    return {
        "total": len(items),
        "up": up, "down": down, "flat": flat, "suspended": suspended,
        "amount_yuan": round(amount, 2),
        "avg_chg": round(sum(chgs) / len(chgs), 2),
        "median_chg": round(chgs[len(chgs) // 2], 2),
        "with_chg": len(chgs),
        "src": "fuyao",
    }


def limit_up_stats(ttl: float = 60) -> Optional[Dict]:
    """涨停市场统计（封板率 / 涨停家数 / 炸板 / 连板梯队）。

    实测字段（2026-10-28）：`trade_date`、`seal_rate`(0.5)、`limit_up_stock_count`(69)、
    `open_stock_count`(69)、`touched_limit_up_stock_count`(138)、
    `page_boards`([{board_level:"one", stock_count:61, promotion_rate:null}, ...])。
    """
    return get("/api/a-share/special-data/limit-up-statistics", None, ttl=ttl)


def limit_up_pool(ttl: float = 60) -> List[dict]:
    """涨停股名单（含 `limit_up_reason` 涨停原因 / `seal_money` 封单 / `continue_day_cnt` 连板）。

    ⚠️ 该端点**有固定上限 50 条**（实测传 `limit=200` 仍只回 50）⇒ **家数不能用它数**，
       家数请用 `limit_up_stats()['limit_up_stock_count']`；本函数只用于"看名单/原因"。
    """
    d = get("/api/a-share/special-data/limit-up-pool", None, ttl=ttl, timeout=30)
    return (d or {}).get("item") or []


def limit_down_pool(ttl: float = 60) -> List[dict]:
    """跌停股名单（实测字段：thscode/ticker/name/last_price/price_change_ratio_pct/
    first_limit_time/last_limit_time/turnover_ratio_pct）。

    ⚠️ 同样有上限 50 ⇒ 调用方拿到 `len()==50` 时必须视为**可能被截断**（极端千股跌停日），
       不能直接当家数用（见 `limit_stats.counts_best` 的护栏）。
    """
    d = get("/api/a-share/special-data/limit-down-pool", None, ttl=ttl, timeout=30)
    return (d or {}).get("item") or []


def trading_days(start: str = "", end: str = "", ttl: float = 86400) -> List[str]:
    """交易日列表（`YYYY-MM-DD`）。阶段 3 会用（替代手工 HOLIDAYS）。"""
    params = {}
    if start and end:
        params = {"start_date": start.replace("-", ""), "end_date": end.replace("-", "")}
    d = get("/api/a-share/calendar/trading-days", params or None, ttl=ttl)
    out = []
    for it in ((d or {}).get("item") or []):
        v = str(it.get("date") or "")
        if len(v) == 8:
            out.append(f"{v[:4]}-{v[4:6]}-{v[6:]}")
    return out


# ── 自检（`GET /api/system/fuyao-check`）──────────────────────────────────

def selfcheck() -> Dict:
    """逐端点实测：可用性 / 耗时 / 条数 / 错误。**不返回 Key**。"""
    out = {
        "enabled": enabled(),
        "key_present": bool(_API_KEY),
        "base": BASE,
        "cooldown_left_sec": max(0, round(_cooldown_until[0] - _now(), 1)),
        "stats": dict(_STATS),
        "endpoints": [],
    }
    if not enabled():
        out["hint"] = ("未启用：需在环境变量配置 FUYAO_API_KEY，"
                       "且 FUYAO_ENABLED 不为 0")
        return out

    def probe(name: str, path: str, params: Optional[dict], pick):
        t0 = _now()
        d = get(path, params, ttl=0, timeout=40)
        ms = int((_now() - t0) * 1000)
        item = pick(d) if d else None
        out["endpoints"].append({
            "name": name, "path": path,
            "ok": d is not None,
            "ms": ms,
            "count": (len(item) if isinstance(item, (list, dict)) else item),
            "error": "" if d is not None else (_STATS.get("last_error") or "failed"),
        })

    probe("全A股代码表", "/api/meta/tickers/list",
          {"asset_type": "a-share", "limit": 10000, "offset": 0},
          lambda d: d.get("item") or [])
    probe("全市场快照", "/api/a-share/prices/snapshot",
          {"limit": 10000, "offset": 0},
          lambda d: d.get("item") or [])
    probe("涨停统计", "/api/a-share/special-data/limit-up-statistics", None,
          lambda d: 1 if d else 0)
    probe("交易日历", "/api/a-share/calendar/trading-days", None,
          lambda d: d.get("item") or [])
    return out
