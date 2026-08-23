"""
================================================================================
【文件作用】东方财富新闻数据源 + 内存缓存层（7×24 快讯 / 个股新闻）
================================================================================

后端的「消息面」数据源（阶段 1：东财快讯，不接财联社）。

数据来源（在线验证可用，2026-08）：
  - 7×24 快讯：np-listapi.eastmoney.com/comm/web/getFastNewsList
    每条快讯自带 stockList（关联标的，格式 "市场.代码"）：
      "0.300308" = 深市A股 300308，"1.600519" = 沪市A股 600519，
      "90.BK0xxx" = 板块，其余为港美股/债券/指数（忽略）。
  - 个股搜索：search-api-web.eastmoney.com/search/jsonp（关键词=代码）
    覆盖个股深度报道（业绩说明会/研报/公告解读，快讯里没有的内容）。
    注意：① 必须用 http（https 返回错乱缓存响应）；② 响应是 JSONP 壳
    需剥掉；③ 按相关度排序而非时间，时效性由打分侧 72h 衰减兜底。
  个股新闻 = 双源合并：搜索接口为主源 + 快讯过滤为辅（代码/名称匹配），
  搜索接口失败自动降级为纯快讯方案（零额外请求）。

对外函数（路由层调用，均带缓存 + 降级）：
  get_global_news(limit)    最新 7×24 快讯（归一化结构）
  get_stock_news(code)      某只股票的相关快讯（代码精确匹配 + 名称匹配）

设计要点（与 eastmoney.py 保持一致的风格）：
  - requests.Session 复用 + UA/Referer 头
  - 内存缓存 + TTL + threading.Lock
  - 全程 try/except：抓取/解析失败 → 返回空列表，绝不向上层抛异常
================================================================================
"""

import requests
import json
import time
import threading
from urllib.parse import urlencode

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.eastmoney.com/",
})

# 在线验证（2026-08）：https 当前稳定；响应为 UTF-8 JSON，
# 用 r.content 显式 utf-8 解码，避免 requests 编码猜测偏差。
_FAST_NEWS_URL = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"

# 个股搜索接口：必须 http（https 实测返回错乱缓存），响应为 JSONP 壳。
_SEARCH_NEWS_URL = "http://search-api-web.eastmoney.com/search/jsonp"

# ── 缓存 ──
_cache = {}
_cache_lock = threading.Lock()

TTL_GLOBAL = 90    # 全量快讯：90 秒
TTL_STOCK = 300    # 个股过滤结果：300 秒（快讯本身 90s 刷新，这里防重复过滤/打分）


def _get_cached(key: str, ttl: int, loader):
    """通用缓存包装（与 eastmoney.py 同款）：命中未过期直接返回；
    否则调 loader() 抓取，非空结果写缓存。空结果不缓存，便于立刻重试。"""
    now = time.time()
    with _cache_lock:
        c = _cache.get(key)
        if c and now - c["ts"] < ttl:
            return c["data"]
    try:
        data = loader()
    except Exception as e:
        print(f"[eastmoney_news] 缓存加载失败 key={key}: {e}")
        return []
    if data:
        with _cache_lock:
            _cache[key] = {"data": data, "ts": now}
    return data


def _parse_a_stock_codes(stock_list: list) -> list:
    """
    从快讯的 stockList 提取 A 股代码。
    格式 "市场.代码"：前缀 0=深市、1=沪市 且代码为 6 位数字才是 A 股；
    90=板块、106/116/201=港美股、150=债券、999=指数，一律忽略。
    """
    codes = []
    for c in stock_list or []:
        if not isinstance(c, str) or "." not in c:
            continue
        mkt, code = c.split(".", 1)
        if mkt in ("0", "1") and len(code) == 6 and code.isdigit():
            codes.append(code)
    return codes


def _normalize(item: dict) -> dict:
    """归一化一条快讯为轻量结构（只保留打分/展示需要的字段）。"""
    return {
        "id": item.get("code", ""),
        "title": (item.get("title") or "").strip(),
        "summary": (item.get("summary") or "").strip(),
        "time": item.get("showTime", ""),          # 北京时间 "YYYY-MM-DD HH:MM:SS"
        "stocks": _parse_a_stock_codes(item.get("stockList")),
    }


def get_global_news(limit: int = 100) -> list:
    """
    最新 7×24 快讯（缓存 90s）。
    返回归一化列表，按时间倒序（接口本身倒序）。失败返回 []。
    """
    def loader():
        # ★ sortEnd 必须存在（可为空串），接口对缺失参数直接报错；
        #   requests 的 params 会丢弃空值参数，故手动拼接 query。
        query = urlencode({
            "client": "web", "biz": "web_724", "fastColumn": "102",
            "sortEnd": "", "pageSize": str(limit), "req_trace": "1",
        })
        # 重试：东财接口偶发 502/空响应，非 200 或空体视为失败再试一次；
        # 空结果不缓存，后续调用也会自然重试。
        r = None
        for _ in range(2):
            try:
                resp = _session.get(f"{_FAST_NEWS_URL}?{query}", timeout=10)
                if resp.status_code == 200 and resp.content:
                    r = resp
                    break
            except Exception:
                pass
            time.sleep(1)
        if r is None:
            return []
        lst = (json.loads(r.content.decode("utf-8")).get("data") or {}).get("fastNewsList") or []
        return [_normalize(it) for it in lst if it.get("title")]
    return _get_cached("global", TTL_GLOBAL, loader)


def _strip_jsonp(text: str) -> dict:
    """剥掉 JSONP 壳 cb({...}) 后解析。失败抛异常由调用方兜底。"""
    return json.loads(text[text.index("(") + 1: text.rindex(")")])


def _fetch_search_news(code: str, limit: int) -> list:
    """
    东财搜索接口按代码查个股新闻（深度报道主源）。
    归一化为与快讯相同的轻量结构，失败返回 []（调用方降级为纯快讯）。
    """
    param = {
        "uid": "", "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": limit,
            "preTag": "", "postTag": "",   # 空串 → 标题不带 <em> 高亮标签
        }},
    }
    query = urlencode({"cb": "cb", "param": json.dumps(param, ensure_ascii=False, separators=(",", ":"))})
    r = None
    for _ in range(2):
        try:
            resp = _session.get(f"{_SEARCH_NEWS_URL}?{query}", timeout=10)
            if resp.status_code == 200 and resp.content:
                r = resp
                break
        except Exception:
            pass
        time.sleep(1)
    if r is None:
        return []
    arts = (_strip_jsonp(r.text).get("result") or {}).get("cmsArticleWebOld") or []
    out = []
    for a in arts:
        title = (a.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "id": a.get("code", ""),
            "title": title,
            "summary": (a.get("content") or "")[:200].strip(),  # 摘要截断防关键词噪声
            "time": a.get("date", ""),
            "stocks": [code],
        })
    return out


def get_stock_news(code: str, limit: int = 20) -> list:
    """
    某只股票的相关新闻（缓存 300s/只），双源合并：
      1. 搜索接口（主源）：个股深度报道；失败自动降级为空；
      2. 快讯过滤（辅源）：stockList 精确匹配代码 + 标题/摘要含名称（≥3 字）。
    两源按标题去重（快讯实时性更强，保留快讯版本），合计截断到 limit。
    股票名取自 tencent 行情缓存；缓存为空时名称匹配跳过。
    """
    def loader():
        name = ""
        try:
            from app.tencent import _cache as qt_cache
            name = (qt_cache.get("stocks", {}).get(code) or {}).get("name", "")
        except Exception:
            pass

        # 主源：搜索接口（深度报道）；失败不抛，降级为纯快讯方案。
        # 搜索按相关度排序可能混入弱相关文章，名称已知时要求标题/正文提及代码或名称。
        search_items = _fetch_search_news(code, limit)
        if name:
            search_items = [
                it for it in search_items
                if code in it["title"] + it["summary"] or name in it["title"] + it["summary"]
            ]

        # 辅源：快讯过滤（实时性）
        fast_items = []
        for it in get_global_news():
            if code in it["stocks"]:
                fast_items.append(it)
            elif name and len(name) >= 3 and (name in it["title"] or name in it["summary"]):
                fast_items.append(it)

        # 合并去重：标题相同的保留先入者（快讯在前 → 优先快讯版本）
        seen, merged = set(), []
        for it in fast_items + search_items:
            key = it["title"].strip()
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
        return merged[:limit]
    return _get_cached(f"stock:{code}", TTL_STOCK, loader)
