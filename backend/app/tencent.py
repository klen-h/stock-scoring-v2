"""
================================================================================
【文件作用】腾讯行情数据源 + 内存缓存层
================================================================================

整个后端的"数据来源"。它做的事：
  1. 从腾讯财经公开接口（qt.gtimg.cn）抓取 A股实时行情、K线数据
  2. 把全量 A股行情缓存到内存，供 /api/score/batch 等批量接口使用
  3. 对外暴露函数（get_stock / get_kline / search_stocks 等）供路由调用

数据源说明：
  - 腾讯的接口返回的是一段文本（不是标准 JSON），形如：
      v_sz000001="51~平安银行~000001~10.5~10.3~..."
    每个字段用 ~ 分隔，第 N 个字段对应固定含义（价格、成交量、PE 等）。
    所以代码里有大量 data[3]、data[37] 这种"按下标取字段"的操作。
================================================================================
"""

import requests, json, time, threading
from datetime import datetime, timedelta

# requests 是 Python 最常用的 HTTP 客户端，类似前端的 axios
# 这里用 Session() 复用 TCP 连接，性能更好（类似 axios.create() 复用连接池）
_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0"})  # 伪装浏览器 UA，避免被接口拒绝


# ===== A股代码表（静态生成）=====
# 临时屏蔽的板块：在此集合中的代码前缀将被排除出全量代码池。
# 恢复方法：从集合中删掉对应前缀即可（无需改其他代码）。
DISABLED_PREFIXES = {
    "688",  # 科创板（上交所）—— 暂时屏蔽
    "300",  # 创业板（深交所）—— 暂时屏蔽
    "301",  # 创业板（深交所，新段）—— 同上
}

# 是否过滤 ST/*ST 风险警示股（退市风险股不进入行情缓存/榜单）。
# ST 股票名称特征：以 ST、*ST、SST、S*ST 开头（S=未完成股改）。
# 设为 False 即可恢复。注意：手动按代码查个股(get_stock)不受影响，仅影响缓存/榜单。
EXCLUDE_ST = True

def _is_disabled(code: str) -> bool:
    """判断某只股票代码是否属于被屏蔽的板块（被屏蔽则不进入代码池）"""
    return any(code.startswith(p) for p in DISABLED_PREFIXES)

def _is_st_stock(name: str) -> bool:
    """
    判断是否为 ST 风险警示股。
    腾讯返回的名称可能带空格（如 "ST 平能"），先去空格再判断前缀。
    中文股名里基本不会出现连续的 "ST" 字母，所以前缀匹配足够可靠。
    """
    if not EXCLUDE_ST or not name:
        return False
    clean = name.replace(' ', '').upper()
    return (clean.startswith('ST') or
            clean.startswith('*ST') or
            clean.startswith('SST') or
            clean.startswith('S*ST'))

def _build_stock_pool():
    """
    生成全A股"可能"的代码列表。

    A股代码是有规则的（不同板块代码段不同）：
      深市主板：000xxx
      创业板：  300xxx / 301xxx   ← 当前被 DISABLED_PREFIXES 屏蔽
      沪市主板：600xxx ~ 605xxx
      科创板：  688xxx            ← 当前被 DISABLED_PREFIXES 屏蔽

    注意：这里生成的是"所有可能的代码"，里面有很多空号（不存在的股票），
    后续请求腾讯时会过滤掉（price==0 视为无效）。

    临时屏蔽某板块：把它的代码前缀加入顶部 DISABLED_PREFIXES 集合即可，
    行情列表 / 评分排行 / 搜索 都会自动排除（不影响手动按代码查个股）。
    """
    codes = []
    # 深市主板 000001-005999
    for i in range(1, 6000):
        codes.append(("sz", f"{i:06d}"))   # f"{i:06d}" 把数字补零到 6 位，如 1 → "000001"
    # 创业板 300001-301999（被屏蔽时跳过）
    for i in range(300001, 302000):
        codes.append(("sz", f"{i:06d}"))
    # 沪市主板 600000-605999
    for i in range(600000, 606000):
        codes.append(("sh", f"{i:06d}"))
    # 科创板 688001-688999（被屏蔽时跳过）
    for i in range(688001, 689000):
        codes.append(("sh", f"{i:06d}"))
    # 统一过滤被屏蔽的板块
    return [(mkt, code) for (mkt, code) in codes if not _is_disabled(code)]

# 模块级变量（import 时执行一次，后续复用）：
# _ALL_CODES：所有 (市场前缀, 代码) 的元组列表
_ALL_CODES = _build_stock_pool()
# _CODE_TO_PREFIX：{代码: 市场前缀} 的映射表，方便后续通过代码反查市场
# 类比 JS：const codeToPrefix = Object.fromEntries(allCodes)
_CODE_TO_PREFIX = {}
for _prefix, _code in _ALL_CODES:
    _CODE_TO_PREFIX[_code] = _prefix


# ===== 缓存 =====

# 全局缓存字典。因为全量 A股有几千只，每次请求都拉一遍腾讯会很慢，
# 所以把行情数据缓存起来，定期（60秒）刷新一次。
#
# 结构说明（类比 JS 对象）：
#   _cache = {
#     stocks: { "000001": {行情数据}, "000002": {...}, ... },  # 缓存的行情
#     last_update: 1700000000,       # 上次刷新的 Unix 时间戳（秒）
#     lock: <threading.Lock 对象>    # 线程锁，防止并发刷新冲突
#   }
#
# threading.Lock 是 Python 的互斥锁：
#   多个 HTTP 请求同时进来时，防止它们同时触发刷新（会重复请求、浪费资源）。
#   类似前端"防抖/节流"，但这里是为了线程安全。
_cache = {"stocks": {}, "last_update": 0, "lock": threading.Lock()}
BATCH_SIZE = 80  # 每次请求腾讯最多放多少只股票（腾讯单次上限约 100，留余量用 80）

# 有效代码缓存：首次全量扫描后，记住哪些代码是真实存在的（price>0）。
# 后续刷新只请求这些有效代码，跳过 8000+ 个空号，速度提升约 3 倍。
# 后端重启后丢失（首次仍需全量扫描）。
# 全量扫描由 force=True 触发（前端"手动刷新"按钮），用于发现新上市股票。
_valid_codes = []  # [(prefix, code), ...] 有效代码列表

# K线专用缓存：避免短时间内重复请求同一只股票（详情页 + 评分精算都会调 get_kline）
# 结构：{ "code|period": {"data": [...], "ts": 1700000000} }
# TTL 设为 5 分钟（盘中足够新鲜，又避免高频打爆腾讯接口）
KLINE_CACHE = {}
KLINE_CACHE_TTL = 300  # 秒


def _fetch_tencent(codes_str: str, timeout: int = 10) -> dict:
    """
    从腾讯获取行情的核心函数。

    参数：
      codes_str: 股票代码字符串，多个用逗号分隔，如 "sh000001,sz000002"
      timeout:   超时秒数

    返回：
      { qt_code: {股票信息dict}, ... }
      例如 { "sh000001": {code:"000001", name:"上证指数", price:3100, ...} }

    腾讯接口格式说明：
      URL: https://qt.gtimg.cn/q=sh000001,sz000002
      返回（纯文本，多行用 ; 分隔）：
        v_sh000001="1~上证指数~000001~3100.5~...";
        v_sz000002="51~万科A~000002~9.8~...";
      每行里，引号内的内容用 ~ 分隔成字段数组，字段含义是固定的（按下标取）。
    """
    url = f"https://qt.gtimg.cn/q={codes_str}"
    r = _session.get(url, timeout=timeout)
    result = {}
    # r.text 是原始文本。先去掉首尾空白，再按 ; 切分成多行
    for line in r.text.strip().split(";"):
        line = line.strip()
        # 空行或没有 ~ 的行（无效数据）直接跳过
        if not line or '~' not in line:
            continue
        try:
            # 一行形如：v_sh000001="1~上证指数~..."
            # split("=")[0] → "v_sh000001"，再去掉 "v_" 前缀 → "sh000001"（即 qt_code）
            qt_code = line.split("=")[0].replace("v_", "")
            # split('"')[1] 取引号之间的内容，即 "1~上证指数~..." 这部分
            # 再按 ~ 切分成字段数组
            data = line.split('"')[1].split('~')
            # 字段数不足 59 说明数据残缺，跳过
            if len(data) < 59:
                continue
            # 解析各字段。data[N] 对应固定含义（参考腾讯协议）
            # 用 data[3] if data[3] else 0 防止空字符串导致 float() 报错
            price = float(data[3]) if data[3] else 0
            prev_close = float(data[4]) if data[4] else 0
            result[qt_code] = {
                "code": data[2],                # 股票代码（如 "000001"）
                "name": data[1],                # 股票名称（如 "平安银行"）
                "price": price,                 # 最新价
                "prev_close": prev_close,       # 昨收价
                "open": float(data[5]) if data[5] else 0,      # 今开
                "high": float(data[41]) if data[41] else 0,    # 最高
                "low": float(data[42]) if data[42] else 0,     # 最低
                "change_amt": float(data[31]) if data[31] else 0,   # 涨跌额
                "change_pct": float(data[32]) if data[32] else 0,   # 涨跌幅 %
                "volume": float(data[6]) if data[6] else 0,         # 成交量（手）
                "amount_wan": float(data[37]) if data[37] else 0,   # 成交额（万元）
                "amount": float(data[37]) * 10000 if data[37] else 0,  # 成交额（元）
                "turnover_rate": float(data[38]) if data[38] else 0,   # 换手率 %
                "pe": float(data[39]) if data[39] else 0,   # 市盈率 PE
                "pb": float(data[40]) if data[40] else 0,   # 市净率 PB
                "amplitude": float(data[43]) if data[43] else 0,   # 振幅 %
                "market_cap": float(data[57]) if data[57] else 0,  # 总市值（万元）
                "float_cap": float(data[58]) if data[58] else 0,  # 流通市值（万元）
            }
        except (IndexError, ValueError, TypeError):
            # 任何解析异常都跳过这一行（不影响其他股票）
            continue
    return result


def refresh_all_stocks(force: bool = False):
    """
    刷新全量 A股行情缓存。

    参数：
      force: True 表示强制刷新（忽略 60 秒冷却 + 触发全量扫描）

    速度优化（有效代码缓存）：
      - 首次（_valid_codes 为空）：全量扫描所有 12000 个候选代码（慢，~2-4分钟）
        扫描后把有效代码（price>0）记入 _valid_codes
      - 后续非强制刷新：只请求 _valid_codes 里的代码（~4000 只，快 3 倍）
      - force=True（手动刷新按钮）：重新全量扫描，用于发现新上市股票

    这样既保证日常刷新快，又能在手动刷新时发现新股（IPO）。
    代价：后端重启后首次仍需全量扫描（_valid_codes 是内存缓存）。
    """
    global _valid_codes
    with _cache["lock"]:   # with 语法：进入时自动加锁，退出时自动释放（类似 try-finally）
        # 冷却判断：未强制且 60 秒内刷新过 → 直接返回旧数据
        if not force and time.time() - _cache["last_update"] < 60:
            return _cache["stocks"]

        # 决定本次扫描的代码池：
        #   force=True 或 _valid_codes 为空 → 全量扫描（发现新股）
        #   否则 → 增量扫描（只请求已知有效代码，跳过 8000 个空号）
        do_full_scan = force or not _valid_codes
        scan_pool = _ALL_CODES if do_full_scan else _valid_codes
        mode = "全量" if do_full_scan else "增量"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始{mode}刷新行情... ({len(scan_pool)} 个代码)")

        stocks = {}
        valid_found = []  # 记录本次发现的有效代码（用于更新 _valid_codes）
        total = len(scan_pool)
        for i in range(0, total, BATCH_SIZE):
            batch = scan_pool[i:i + BATCH_SIZE]   # 切片取这一批（最多 80 只）
            codes_str = ",".join(f"{p}{c}" for p, c in batch)
            try:
                data = _fetch_tencent(codes_str, timeout=15)
                for qt_code, info in data.items():
                    # 三重过滤：price>0（有效）+ 非 ST 股 + 名称非空
                    if (info["price"] > 0
                            and not _is_st_stock(info.get("name", ""))
                            and info.get("name")):
                        stocks[info["code"]] = info
                        # 记录有效代码的前缀（用于增量扫描）
                        prefix = qt_code.replace(info["code"], "")
                        valid_found.append((prefix, info["code"]))
            except Exception as e:
                print(f"  批次 {i // BATCH_SIZE} 失败: {e}")
            # 每 10 批打印一次进度
            if i % (BATCH_SIZE * 10) == 0 and i > 0:
                print(f"  进度: {i}/{total}, 已获取 {len(stocks)} 只")

        # 全量扫描后更新有效代码缓存（增量扫描不需要更新，因为池子已经是有效代码）
        if do_full_scan and valid_found:
            _valid_codes = valid_found
            print(f"  有效代码缓存已建立: {len(_valid_codes)} 只")

        _cache["stocks"] = stocks
        _cache["last_update"] = time.time()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {mode}刷新完成: {len(stocks)} 只股票")
        return stocks


def get_stock(code: str) -> dict:
    """获取单只股票实时行情。code 是纯数字代码如 "000001"。"""
    # 根据代码反查市场前缀（sh/sz）；查不到则按代码首位推断（0/3 开头是深市，其余沪市）
    prefix = _CODE_TO_PREFIX.get(code, "sz" if code.startswith("0") or code.startswith("3") else "sh")
    data = _fetch_tencent(f"{prefix}{code}")
    # 取第一个（也是唯一一个）结果。data.values() 返回所有值的视图，
    # for 循环取第一个就 return，相当于 JS 的 Object.values(data)[0]
    for v in data.values():
        return v
    return {}


def get_stocks_batch(codes: list) -> list:
    """批量获取股票行情。codes 是代码列表，返回行情 dict 列表。"""
    codes_str = []
    for c in codes:
        prefix = _CODE_TO_PREFIX.get(c, "sz" if c.startswith("0") or c.startswith("3") else "sh")
        codes_str.append(f"{prefix}{c}")
    data = _fetch_tencent(",".join(codes_str))
    return list(data.values())


def get_index(code: str) -> dict:
    """
    获取指数行情（如上证指数 000001、深证成指 399001）。

    注意：指数代码和股票代码会重叠（比如 000001 既是上证指数也是平安银行），
    这里通过市场前缀区分：sh000001=上证指数，sz000001=平安银行。
    """
    prefix = "sh" if code.startswith("0") else "sz"
    data = _fetch_tencent(f"{prefix}{code}")
    for v in data.values():
        return v
    return {}


def get_kline(symbol: str, period: str = "day", start: str = "", end: str = "", count: int = 300) -> list:
    """
    获取K线数据（历史行情，用于计算技术指标、画图）。

    参数：
      symbol: 纯数字代码，股票如 "000001"，指数如 "000300"
      period: "day"日线 / "week"周线 / "month"月线
      start/end: 日期范围（留空则默认近 2 年）
      count:    返回多少根K线

    返回（K线数组，每根 K线是一个 dict）：
      [{date, open, close, high, low, volume}, ...]
    """
    # 缓存命中判断：同一只股票同一周期 5 分钟内不重复请求
    # 这一步至关重要——评分精算 + 详情页 + 技术指标都会调 get_kline，
    # 没缓存的话短时间内对同一只票反复请求，会触发腾讯限流导致全部失败。
    cache_key = f"{symbol}|{period}"
    cached = KLINE_CACHE.get(cache_key)
    if cached and time.time() - cached["ts"] < KLINE_CACHE_TTL:
        return cached["data"]

    # 默认时间范围：近 2 年至今
    if not start:
        start = (datetime.now() - timedelta(days=365 * 2)).strftime("%Y-%m-%d")
    if not end:
        end = datetime.now().strftime("%Y-%m-%d")

    # 判断市场前缀 & 是否为指数
    # 指数的 K线字段名是 "day"，股票是 "qfqday"（qfq=前复权，调整历史价格使连贯）
    INDEX_MAP = {"000001": "sh", "399001": "sz", "399006": "sz", "000300": "sh", "000905": "sh", "000688": "sh"}
    if symbol in INDEX_MAP:
        prefix = INDEX_MAP[symbol]
        is_index = True
        kline_key = period          # 指数：取 data.sh000001.day
    else:
        prefix = _CODE_TO_PREFIX.get(symbol, "sh" if symbol.startswith("6") else "sz")
        is_index = False
        kline_key = f"qfq{period}"  # 股票：取 data.sh600000.qfqday

    # 腾讯另一个接口：web.ifzq.gtimg.cn（K线专用）
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{prefix}{symbol},{period},{start},{end},{count},qfq"}
    try:
        r = _session.get(url, params=params, timeout=15)
        d = r.json()
        # 返回结构较深：d.data.<市场代码>.<周期key>
        # .get(...) 链式调用，任一层缺失就返回 []
        raw = d.get("data", {}).get(f"{prefix}{symbol}", {}).get(kline_key, [])
        result = []
        for item in raw:
            # 每条 K线：[日期, 开, 收, 高, 低, 成交量, ...]
            result.append({
                "date": item[0],
                "open": round(float(item[1]), 3),
                "close": round(float(item[2]), 3),
                "high": round(float(item[3]), 3),
                "low": round(float(item[4]), 3),
                "volume": float(item[5]),
            })
        # 只缓存有数据的结果（空结果不缓存，让下次能重试）
        if result:
            KLINE_CACHE[cache_key] = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        print(f"K线获取失败 {symbol}: {e}")
        return []


def search_stocks(keyword: str) -> list:
    """
    搜索股票（按代码或名称模糊匹配）。

    策略：
      1. 优先从内存缓存里搜（快，几千只瞬间完成）
      2. 缓存为空时，回退到直接请求腾讯（仅当 keyword 是数字代码时）
    """
    stocks = _cache["stocks"]
    keyword = keyword.strip().upper()   # 转大写，让搜索不区分大小写
    results = []
    for code, info in stocks.items():
        # 代码或名称包含关键词就算命中
        if keyword in code or keyword in info.get("name", "").upper():
            results.append({"code": code, "name": info["name"]})
            if len(results) >= 20:   # 最多返回 20 条
                break
    # 缓存为空的兜底：直接拉腾讯
    if not results:
        prefix = "sz" if keyword.isdigit() and (keyword.startswith("0") or keyword.startswith("3")) else "sh"
        if keyword.isdigit():
            data = _fetch_tencent(f"{prefix}{keyword}")
            for v in data.values():
                if v["price"] > 0:
                    results.append({"code": v["code"], "name": v["name"]})
    return results
