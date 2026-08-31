"""
================================================================================
【文件作用】东方财富数据源 + 内存缓存层（板块 / 资金流）
================================================================================

整个后端的「第二数据源」，与 tencent.py 并列。专门负责板块和资金流数据：
  - 行业板块 / 概念板块（列表、资金流排名）
  - 个股主力资金流向
  - 北向资金（沪深港通当日净流入）

数据来源：东方财富 push2.eastmoney.com 公开 JSON 接口。
  这组接口非官方，但已稳定运行多年，也是 AkShare / Tushare 的底层源。
  字段码（f12/f62/f184 等）东方财富偶有调整，所以下面解析全程防御式：
  任何字段缺失/类型异常都兜底为 0 或空，绝不让单行异常拖垮整批数据。

对外函数（路由层调用，均带缓存 + 降级）：
  get_sectors(kind)            行业/概念板块列表
  get_sector_flow(kind)        板块资金流排名（按主力净流入降序）
  get_stock_flow(order,limit)  个股主力资金流向排名
  get_northbound()             北向资金实时（沪/深/合计 + 分时序列）

设计要点（与 tencent.py 保持一致的风格）：
  - requests.Session 复用 TCP 连接 + UA/Referer 头
  - 内存缓存 + TTL + threading.Lock，防止并发请求重复抓取
  - 全程 try/except：抓取或解析失败 → 返回空（[] / {}），绝不向上层抛异常
================================================================================
"""

import requests
import time
import threading
import json
import re

# requests.Session 复用连接池，性能更好（类比 axios.create()）
_session = requests.Session()
# 带 Referer 头：东方财富对无 Referer 的请求偶尔会拦截，带上更稳
_session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/",
})

# 板块/资金流的通用列表接口
_CLIST = "http://push2.eastmoney.com/api/qt/clist/get"
# 北向资金分时接口
_KAMT = "http://push2.eastmoney.com/api/qt/kamt.rtmin/get"

# ── 缓存 ──
# 结构：{ "key": {"data": <结果>, "ts": <时间戳>} }
# 与 tencent.py 一样用内存缓存 + TTL，板块/资金流变化频率约分钟级，缓存 60s 足够新鲜。
_cache = {}
_cache_lock = threading.Lock()

TTL_SECTOR = 60   # 板块列表/资金流：60 秒
TTL_FLOW = 60     # 个股资金流：60 秒
TTL_NORTH = 30    # 北向分时：30 秒（盘中变化较快）


# ================================================================
#  通用工具
# ================================================================

def _to_float(v, default=0.0) -> float:
    """
    安全转 float。东方财富的空值常用 "-" 或 null 表示，统一兜底为 default。
    类比 JS：Number(v) || 0，但这里更严格地区分合法的 0 和无效值。
    """
    try:
        if v in (None, "", "-"):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v, default=0) -> int:
    """安全转 int（东方财富的涨跌家数等字段）。"""
    try:
        if v in (None, "", "-"):
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _get_cached(key: str, ttl: int, loader):
    """
    通用缓存包装：
      命中且未过期 → 直接返回缓存；
      否则调 loader() 抓取，结果非空则写入缓存。
    抓取（网络 IO）放在锁外执行——只在读写缓存时持锁，避免长时间持锁阻塞其它请求。
    loader 内部已做异常兜底，这里再保险一层：loader 抛异常时返回空。
    """
    now = time.time()
    with _cache_lock:
        c = _cache.get(key)
        if c and now - c["ts"] < ttl:
            return c["data"]

    try:
        data = loader()
    except Exception as e:
        print(f"[eastmoney] 缓存加载失败 key={key}: {e}")
        return [] if not key.startswith("north") else {}

    # 只缓存非空结果：空结果不缓存，让下次请求能立刻重试（可能是临时网络抖动）
    if data:
        with _cache_lock:
            _cache[key] = {"data": data, "ts": now}
    return data


# 东财 clist 单页硬上限（实测）：pz 传大于 100 的值无效，接口静默只返回 100 条。
# 这个坑很隐蔽 —— 不报错、不提示，只是数据变少。曾导致板块成分股被截成 100 只
# （"基础化工"实际 454 只，只拿到前 100）。要拿全量必须用 _fetch_clist_paged()。
_PAGE_SIZE = 100

# 反爬：失败重试次数 + 退避基数（秒）。批量拉取时另需请求间隔，见 _PAGE_GAP。
# ★ 历史教训：0.25s 翻页间隔连续跑建映射任务（300-500 请求）触发了东财 IP 封禁
#   （2026-08-15，封禁持续 ~48h，期间 push2 全断）。间隔宁可慢也别封——
#   建映射是月级任务，多花几分钟无所谓。
_RETRIES = 2
_RETRY_BACKOFF = 0.6
_PAGE_GAP = 1.0           # 翻页间隔（秒）——1s 是实测安全值

# 端点降级链：主站封 IP（高频触发后封 24~48h）时切 delay 端点。
# push2delay 数据格式与 push2 完全相同，仅行情延迟约 15 分钟——封禁期有数据总比没有强。
_CLIST_HOSTS = ["http://push2.eastmoney.com", "https://push2delay.eastmoney.com"]
_CLIST_PATH = "/api/qt/clist/get"   # 两端点共用路径（hosts 只存主机名）


def _fetch_clist(fs: str, fields: str, fid: str = None,
                 order: str = "desc", limit: int = 200,
                 page: int = 1) -> list:
    """
    调用 clist 接口，返回 data.diff 列表（每项是 {fxx: 值} 的 dict）。

    ★ 单页最多 100 条：limit 传再大也只返回 100 条（东财限制，静默截断）。
      需要全量数据请用 _fetch_clist_paged()。

    参数：
      fs:     市场范围过滤（如 "m:90+t:2" 行业板块、"m:0+t:6,..." 沪深A股、
              "b:BK1206" 某板块的成分股）
      fields: 要返回的字段码（逗号分隔）
      fid:    排序字段（如 "f62" 按主力净流入排序）；不传则用东方财富默认排序
      order:  'desc' 降序 / 'asc' 升序
      limit:  返回条数（自动收敛到 100 以内）
      page:   页码（第几页，从 1 开始）

    端点策略：主站 push2 失败 → 自动切 push2delay（延迟行情）。
    """
    params = {
        "pn": page,                     # 页码
        "pz": min(limit, _PAGE_SIZE),   # 每页条数（东财上限 100，超了静默截断）
        "po": 1 if order == "desc" else 0,   # po=1 降序, 0 升序
        "np": 1,
        "fltt": 2,                      # fltt=2：返回纯数字（不带"亿/万"等单位），便于解析
        "invt": 2,
        "fs": fs,
        "fields": fields,
    }
    if fid:
        params["fid"] = fid

    # 东财对高频请求的处理是【直接断连】（RemoteDisconnected），且连续高频会临时封 IP
    # （实测封禁持续 24~48 小时）。所以失败做退避重试；主站连重试都失败 → 切 delay 端点；
    # 批量调用方（_fetch_clist_paged / sector_industry.build_map）还需自行加请求间隔。
    #
    # 健康埋点分两层：
    #   eastmoney_main —— 主站本身的状态（封 IP 时连续失败 → 触发企微告警）
    #   eastmoney      —— 数据链路整体（delay 兜底成功 = 数据还在流，算健康）
    from app import health
    last_err = ""
    for attempt in range(1 + _RETRIES):
        try:
            r = _session.get(_CLIST_HOSTS[0] + _CLIST_PATH, params=params, timeout=10)
            # 返回结构：{data: {total, diff: [...]}}
            rows = r.json().get("data", {}).get("diff", []) or []
            health.record("eastmoney", bool(rows), "" if rows else "clist 返回空")
            health.record("eastmoney_main", bool(rows),
                          "" if rows else "主站返回空（疑似风控）")
            return rows
        except Exception as e:
            last_err = str(e)
            if attempt < _RETRIES:
                time.sleep(_RETRY_BACKOFF * attempt)   # 0.6s → 1.2s 退避
                continue
    health.record("eastmoney_main", False, last_err)   # 主站重试耗尽（含封 IP 场景）

    # 主站（含重试）全部失败 → 尝试 delay 端点（数据同构，行情延迟 ~15 分钟）
    # 用全新 Session：主站的失败连接(RST)会污染 _session 的连接池，
    # 复用会话紧接着请求 delay 会拿到空响应体（实测），必须新开会话。
    try:
        time.sleep(0.5)
        with requests.Session() as fresh:
            fresh.headers.update(_session.headers)
            r = fresh.get(_CLIST_HOSTS[1] + _CLIST_PATH, params=params, timeout=10)
        rows = r.json().get("data", {}).get("diff", []) or []
        if rows:
            print(f"[eastmoney] 主站不可用，已切 delay 端点（行情延迟~15分钟）fs={fs}")
            health.record("eastmoney", True, "")   # 数据链路健康（虽延迟）
            return rows
        last_err = f"{last_err}; delay端点: 返回空"
    except Exception as e:
        last_err = f"{last_err}; delay端点: {e}"

    print(f"[eastmoney] clist 抓取失败 fs={fs}（主站+delay均失败）: {last_err[:150]}")
    health.record("eastmoney", False, last_err)
    return []


def _fetch_clist_paged(fs: str, fields: str, fid: str = None,
                       order: str = "desc", max_items: int = 6000,
                       max_pages: int = 200) -> list:
    """
    分页拉取 clist 全量数据（自动翻页，直到取完 / 无新数据 / 达到上限）。

    存在的理由：东财单页上限 100 条（见 _PAGE_SIZE 注释）。_fetch_clist(limit=2000)
    实际只返回前 100 条且**没有任何报错**，用它取板块成分股会得到残缺数据
    （"基础化工" 454 只只能拿到前 100 只）。建映射表、全市场扫描必须用这个。

    返回：去重后的完整列表（按 f12 代码去重 —— 翻页时相邻页可能出现重复行）。
    """
    out, seen = [], set()
    for pn in range(1, max_pages + 1):
        if pn > 1:
            time.sleep(_PAGE_GAP)       # 翻页间隔：连续请求会触发东财断连/封 IP
        rows = _fetch_clist(fs, fields, fid=fid, order=order,
                            limit=_PAGE_SIZE, page=pn)
        if not rows:
            break
        new = 0
        for r in rows:
            key = r.get("f12") or str(r)
            if key not in seen:
                seen.add(key)
                out.append(r)
                new += 1
        # 翻到末页的两个信号：本页不满 100 条 / 整页都是重复数据
        if len(rows) < _PAGE_SIZE or new == 0:
            break
        if len(out) >= max_items:
            break
    return out[:max_items]


# ================================================================
#  板块列表 / 板块资金流
# ================================================================

# 板块过滤码：t:2 行业板块, t:3 概念板块
_FS_SECTOR = {"industry": "m:90+t:2", "concept": "m:90+t:3"}

# 板块列表字段：代码/名称/指数价/涨跌幅/涨跌额/换手/上涨家/下跌家/领涨股/领涨涨幅/领涨代码
_SECTOR_FIELDS = "f12,f14,f2,f3,f4,f8,f104,f105,f128,f136,f140"

# 资金流字段：代码/名称/价/涨跌幅/主力净流入/主力净流入占比/超大单/大单/中单/小单
_FLOW_FIELDS = "f12,f14,f2,f3,f62,f184,f66,f72,f78,f84"


def get_sectors(kind: str = "industry", limit: int = 200) -> list:
    """
    行业 / 概念板块列表（按涨跌幅降序）。

    kind: 'industry' 行业板块 | 'concept' 概念板块
    返回：[{code, name, price, change_pct, change_amt, turnover_rate,
            up_count, down_count, leader, leader_change_pct, leader_code}, ...]
    """
    fs = _FS_SECTOR.get(kind, _FS_SECTOR["industry"])

    def loader():
        # ★ 必须用分页版：单页上限 100 条，而概念板块有几百个。用 _fetch_clist
        #   会静默只返回前 100 个 —— 行业板块恰好约 100 个看不出问题，
        #   概念板块则长期残缺（limit=200 实际只生效 100）。
        rows = _fetch_clist_paged(fs, _SECTOR_FIELDS, fid="f3", max_items=limit)
        out = []
        for d in rows:
            out.append({
                "code": d.get("f12", ""),
                "name": d.get("f14", ""),
                "price": _to_float(d.get("f2")),
                "change_pct": _to_float(d.get("f3")),
                "change_amt": _to_float(d.get("f4")),
                "turnover_rate": _to_float(d.get("f8")),
                "up_count": _to_int(d.get("f104")),
                "down_count": _to_int(d.get("f105")),
                "leader": d.get("f128", ""),
                "leader_change_pct": _to_float(d.get("f136")),
                "leader_code": d.get("f140", ""),
            })
        if not out:
            print(f"[eastmoney] 东财板块列表为空，降级新浪 {kind}")
            return _sina_sector_list(kind)
        return out

    return _get_cached(f"sectors|{kind}|{limit}", TTL_SECTOR, loader)


def _parse_flow(d: dict, with_price: bool = False) -> dict:
    """
    把一行 clist 结果解析成统一的「资金流」dict。
    金额单位：元（东方财富 clist 在 fltt=2 时直接返回元）。

    主力净流入 = 超大单 + 大单（东方财富口径）。
    """
    item = {
        "code": d.get("f12", ""),
        "name": d.get("f14", ""),
        "change_pct": _to_float(d.get("f3")),
        "net_inflow": _to_float(d.get("f62")),        # 主力净流入额（元）
        "net_inflow_pct": _to_float(d.get("f184")),   # 主力净流入占比（%）
        "super_large_net": _to_float(d.get("f66")),   # 超大单净流入（元）
        "large_net": _to_float(d.get("f72")),         # 大单净流入（元）
        "medium_net": _to_float(d.get("f78")),        # 中单净流入（元）
        "small_net": _to_float(d.get("f84")),         # 小单净流入（元）
    }
    if with_price:
        item["price"] = _to_float(d.get("f2"))
    return item


def get_sector_flow(kind: str = "industry", limit: int = 200) -> list:
    """
    板块资金流排名（按主力净流入额降序）。

    kind: 'industry' | 'concept'
    返回：[{code, name, change_pct, net_inflow, net_inflow_pct,
            super_large_net, large_net, medium_net, small_net}, ...]
    """
    fs = _FS_SECTOR.get(kind, _FS_SECTOR["industry"])

    def loader():
        # 同 get_sectors：用分页版避免单页 100 条截断（概念板块几百个）
        rows = _fetch_clist_paged(fs, _FLOW_FIELDS, fid="f62", max_items=limit)
        out = [_parse_flow(d) for d in rows]
        if not out:
            print(f"[eastmoney] 东财板块资金流为空，降级新浪 {kind}")
            return _sina_sector_flow(kind)
        return out

    return _get_cached(f"sector_flow|{kind}|{limit}", TTL_FLOW, loader)


# ================================================================
#  个股主力资金流向
# ================================================================

# 沪深A股范围：深主板 + 创业板 + 沪主板 + 科创板
# （探测验证 total≈5500，覆盖全A股）
_FS_ASHARE = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def get_stock_flow(order: str = "desc", limit: int = 100) -> list:
    """
    个股主力资金流向排名。

    order: 'desc' 主力净流入最多（资金涌入榜）| 'asc' 净流出最多（资金出逃榜）
    返回：[{code, name, price, change_pct, net_inflow, net_inflow_pct,
            super_large_net, large_net, medium_net, small_net}, ...]
    """
    def loader():
        rows = _fetch_clist(_FS_ASHARE, _FLOW_FIELDS, fid="f62",
                            order=order, limit=limit)
        return [_parse_flow(d, with_price=True) for d in rows]

    return _get_cached(f"stock_flow|{order}|{limit}", TTL_FLOW, loader)


# ================================================================
#  北向资金（沪深港通当日净流入）
# ================================================================

def get_northbound() -> dict:
    """
    北向资金实时净流入（沪深港通）。

    返回：
      {time, sh_net, sz_net, total_net, series: [{time, total_net}, ...]}
      金额单位：元。抓取失败返回 {}。

    数据来自 kamt.rtmin 分时接口，原始单位是「万元」（开盘余额 5200000 万 = 520 亿
    每日额度，由此可确认单位），这里统一 ×10000 转成元，和板块/个股资金流保持一致。

    非交易时段 / 休市时净流入为 0，属正常现象（series 仍返回当日分时点位）。
    """
    def loader():
        try:
            r = _session.get(_KAMT, params={
                "fields1": "f1,f2,f3,f4",
                "fields2": "f51,f52,f53,f54,f55,f56",
            }, timeout=10)
            s2n = r.json().get("data", {}).get("s2n", []) or []
        except Exception as e:
            print(f"[eastmoney] 北向资金抓取失败: {e}")
            return {}

        series = []
        latest = None
        for row in s2n:
            # row 形如 "9:30,0.00,5200000.00,0.00,5200000.00,0.00"
            # 列含义：时间, 沪股通净流入, 沪股通余额, 深股通净流入, 深股通余额, 北向合计净流入
            parts = row.split(",")
            if len(parts) < 6:
                continue
            t = parts[0]
            sh_net = _to_float(parts[1]) * 10000    # 万元 → 元
            sz_net = _to_float(parts[3]) * 10000
            total = _to_float(parts[5]) * 10000
            series.append({"time": t, "total_net": total})
            latest = {"time": t, "sh_net": sh_net, "sz_net": sz_net, "total_net": total}

        if not latest:
            return {}
        return {
            "time": latest["time"],
            "sh_net": latest["sh_net"],
            "sz_net": latest["sz_net"],
            "total_net": latest["total_net"],
            "series": series,
        }

    return _get_cached("northbound", TTL_NORTH, loader)


# ================================================================
#  新浪降级源（板块数据兜底）
# ================================================================
# 东财 push2 偶发限流 / 字段调整（板块数据不稳定的主因）。这里加新浪财经兜底：
#   - 板块列表：newSinaHy.php（行业）/ newFLJK.php?param=class（概念）
#   - 板块资金流：MoneyFlow.ssl_bkzj_zjlrqs（fenlei=0 行业 / 1 概念，按日）
# 新浪 = 宏观面板（macro.py hq.sinajs.cn）同源，长期稳定；老接口响应为 GBK 需显式解码。
# 触发条件：东财抓取失败 / 返回空列表（交易日东财板块资金流周末返回全 0 行而非空，不误触）。

_SINA_SESSION = requests.Session()
_SINA_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn",
})


def _sina_get(url: str, params: dict = None) -> str:
    """抓取新浪接口文本（显式 GBK 解码）。失败返回空串，绝不抛异常。"""
    try:
        r = _SINA_SESSION.get(url, params=params, timeout=10)
        r.encoding = "gbk"
        return r.text
    except Exception as e:
        print(f"[eastmoney] 新浪抓取失败 {url}: {e}")
        return ""


def _sina_sector_list(kind: str) -> list:
    """新浪板块列表（行业/概念）。字段布局（13 列，经实测验证）：
    代码,名称,家数,均价,涨跌额,涨跌幅%,成交量,成交额,领涨股代码,领涨涨幅,领涨价,领涨额,领涨名。
    新浪口径行业 49 个、概念 175 个（比东财少），无涨跌家数/换手率 → 兜底 0。"""
    url = ("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
           if kind == "industry"
           else "http://money.finance.sina.com.cn/q/view/newFLJK.php")
    params = None if kind == "industry" else {"param": "class"}
    text = _sina_get(url, params)
    m = re.search(r"= (\{.*?\});?\s*$", text, re.S) if text else None
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except (ValueError, TypeError):
        return []
    out = []
    for v in data.values():
        p = v.split(",")
        if len(p) < 13:
            continue
        out.append({
            "code": p[0],
            "name": p[1],
            "price": _to_float(p[3]),
            "change_pct": _to_float(p[5]),
            "change_amt": _to_float(p[4]),
            "turnover_rate": 0.0,
            "up_count": 0,
            "down_count": 0,
            "leader": p[12],
            "leader_change_pct": _to_float(p[9]),
            "leader_code": p[8],
        })
    return out


def _sina_sector_flow(kind: str) -> list:
    """新浪板块资金流（按日，仅当日有效，周末/盘前为空属正常）。
    字段：netamount 主力净流入(元) / ratioamount 净占比 / r0-r3 超大单、大、中、小单。"""
    text = _sina_get(
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_zjlrqs",
        {"page": "1", "num": "300", "sort": "netamount", "asc": "0",
         "fenlei": "0" if kind == "industry" else "1"})
    if not text:
        return []
    try:
        rows = json.loads(text)
    except (ValueError, TypeError):
        return []
    out = []
    for d in rows:
        out.append({
            "code": d.get("code", ""),
            "name": d.get("name", ""),
            "change_pct": _to_float(d.get("changeratio")),
            "net_inflow": _to_float(d.get("netamount")),
            "net_inflow_pct": _to_float(d.get("ratioamount")),
            "super_large_net": _to_float(d.get("r0_net")),
            "large_net": _to_float(d.get("r1_net")),
            "medium_net": _to_float(d.get("r2_net")),
            "small_net": _to_float(d.get("r3_net")),
        })
    return out
