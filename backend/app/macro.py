"""
================================================================================
【文件作用】宏观数据源 + 规则引擎（全球+国内面板 → 标签 → 今日方向分）
================================================================================

数据来源：新浪财经公开行情接口（hq.sinajs.cn），一次请求抓全部品种。
  全球：布伦特/WTI原油、COMEX金银铜、美债10Y、纳指期货、日经、恒生科技、
        美元指数、离岸人民币、VIX
  国内：富时A50期货、中国10年期国债、在岸人民币、螺纹钢/铁矿石主连（黑色系）
        （以上国内代码均经实抓验证）

规则引擎：把面板数据按「配置表」逐条评估，输出结构化标签 + 分组加权方向分。
  设计原则（详见规则表）：
  - 每条规则 = 一个可说清传导逻辑的假设
  - 阈值优先用变化率（自我归一），少数用水平值（VIX 等有真实分界线）
  - 三态输出（偏多/中性/偏空），留中性带防噪声
  - 规则是数据不是代码：可调、可版本化、可归因
  - 规则输出叫「参考标签」，回测校准后才配叫「信号」

⚠️ 方向分不进个股评分，只与市场温度并列展示（避免顺周期、保留可解释性）。

对外函数：
  get_macro_panel()    抓取并解析宏观面板（含衍生比率）
  evaluate_rules()     规则引擎：面板 → 触发规则列表 + 分组得分
  get_macro_snapshot() 一份 LLM-ready 的完整快照（面板+标签+方向+市场温度）
================================================================================
"""

import requests
import time
import threading

_session = requests.Session()
# 新浪 2022 起强制校验 Referer，不带会被拒
_session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn",
})

_SINA_URL = "https://hq.sinajs.cn/list="

# ── 缓存（与 tencent.py / eastmoney.py 同风格）──
_cache_lock = threading.Lock()
_cache = {}          # {"key": {"data":..., "ts":...}}
TTL_QUOTES = 60      # 行情面板 60 秒
TTL_SNAPSHOT = 60    # 快照 60 秒

# ================================================================
#  一、行情面板
# ================================================================

# 新浪代码 → 面板键。解析器族：hf=外盘期货, nf=国内期货, bd=国债, fx=汇率,
# hk=港股指数, dxy=美元指数, gb=美股ETF
_SINA_SYMBOLS = [
    "hf_OIL", "hf_CL", "hf_GC", "hf_XAU", "hf_SI", "hf_HG",       # 油/金/银/铜
    "hf_NQ", "hf_NK", "rt_hkHSTECH",                               # 纳指/日经/恒科
    "globalbd_us10yt", "globalbd_cn10yt",                          # 美债/中国国债
    "DINIW", "fx_susdcnh", "fx_susdcny",                           # 美元/离岸/在岸人民币
    "hf_VX", "gb_gld",                                             # VIX / GLD ETF
    "hf_CHA50CFD", "nf_RB0", "nf_I0",                              # A50 / 螺纹 / 铁矿
]

# 面板键 → (新浪代码, 解析族)
# 各族字段布局（均经实抓验证）：
#   hf_*:  [0]最新 [7]昨结 [4]高 [5]低 [12]日期 [6]时间
#   nf_*:  [8]最新 [10]昨结 [3]高 [4]低 [17]日期 [1]时间
#   bd_*:  [3]最新 [2]昨收 [4]高 [5]低 [12]日期 [13]时间
#   fx_*:  [8]最新 [3]昨收 [6]高 [7]低 [17]日期 [0]时间
#   rt_hk: [6]最新 [3]昨收 [4]高 [5]低 [17]日期 [18]时间
#   DINIW: [1]最新 [3]昨收 [6]高 [7]低 [10]日期 [0]时间
#   gb_*:  [1]最新 [26]昨收 [6]高 [7]低 [25]时间
_PANEL_MAP = {
    "brent":    ("hf_OIL", "hf"),
    "wti":      ("hf_CL", "hf"),
    "gold":     ("hf_GC", "hf"),        # COMEX 金期货（面板主金价，其余金来源只做参考）
    "gold_spot": ("hf_XAU", "hf"),      # 伦敦金现货
    "silver":   ("hf_SI", "hf"),
    "copper":   ("hf_HG", "hf"),
    "nasdaq":   ("hf_NQ", "hf"),
    "nikkei":   ("hf_NK", "hf"),
    "hstech":   ("rt_hkHSTECH", "hk"),
    "us10y":    ("globalbd_us10yt", "bd"),
    "cn10y":    ("globalbd_cn10yt", "bd"),
    "dxy":      ("DINIW", "dxy"),
    "usdcnh":   ("fx_susdcnh", "fx"),
    "usdcny":   ("fx_susdcny", "fx"),
    "vix":      ("hf_VX", "hf"),
    "gld":      ("gb_gld", "gb"),
    "a50":      ("hf_CHA50CFD", "hf"),
    "rebar":    ("nf_RB0", "nf"),       # 螺纹钢主连
    "iron":     ("nf_I0", "nf"),        # 铁矿石主连
}


def _f(fields, idx):
    """安全取字段并转 float；缺失/非法返回 0。"""
    try:
        v = fields[idx] if idx < len(fields) else ""
        return float(v) if v not in ("", None) else 0.0
    except (ValueError, TypeError, IndexError):
        return 0.0


def _s(fields, idx):
    """安全取字符串字段。"""
    try:
        return fields[idx] if idx < len(fields) else ""
    except IndexError:
        return ""


def _fetch_sina() -> dict:
    """
    一次性抓取全部新浪代码，返回 {代码: 字段列表}。
    注意编码：新浪返回 GBK，必须用 r.content.decode('gbk')。
    """
    try:
        r = _session.get(_SINA_URL + ",".join(_SINA_SYMBOLS), timeout=10)
        text = r.content.decode("gbk", errors="replace")
        out = {}
        for line in text.strip().split("\n"):
            line = line.strip().rstrip(";")
            if '="' not in line:
                continue
            key = line.split("=")[0].replace("var hq_str_", "")
            val = line.split('"')[1] if '"' in line else ""
            if val:
                out[key] = val.split(",")
        return out
    except Exception as e:
        print(f"[macro] 新浪行情抓取失败: {e}")
        return {}


def _parse(family: str, f: list) -> dict:
    """按字段族解析成统一结构 {price, prev_close, change_pct, high, low, time}。"""
    if family == "hf":
        price, prev, hi, lo = _f(f, 0), _f(f, 7), _f(f, 4), _f(f, 5)
        t = f"{_s(f, 12)} {_s(f, 6)}"
    elif family == "nf":
        price, prev, hi, lo = _f(f, 8), _f(f, 10), _f(f, 3), _f(f, 4)
        t = f"{_s(f, 17)} {_s(f, 1)}"
    elif family == "bd":
        price, prev, hi, lo = _f(f, 3), _f(f, 2), _f(f, 4), _f(f, 5)
        t = f"{_s(f, 12)} {_s(f, 13)}"
    elif family == "fx":
        price, prev, hi, lo = _f(f, 8), _f(f, 3), _f(f, 6), _f(f, 7)
        t = f"{_s(f, 17)} {_s(f, 0)}"
    elif family == "hk":
        price, prev, hi, lo = _f(f, 6), _f(f, 3), _f(f, 4), _f(f, 5)
        t = f"{_s(f, 17)} {_s(f, 18)}"
    elif family == "dxy":
        price, prev, hi, lo = _f(f, 1), _f(f, 3), _f(f, 6), _f(f, 7)
        t = f"{_s(f, 10)} {_s(f, 0)}"
    elif family == "gb":
        price, prev, hi, lo = _f(f, 1), _f(f, 26), _f(f, 6), _f(f, 7)
        t = _s(f, 25)
    else:
        return {}
    chg = round((price - prev) / prev * 100, 2) if prev > 0 else 0.0
    return {"price": price, "prev_close": prev, "change_pct": chg,
            "high": hi, "low": lo, "time": t.strip()}


def get_macro_panel() -> dict:
    """
    宏观面板：全部品种统一结构 + 衍生指标（比率/基差/收益率变动）。
    单品种失败不拖累整体（留空跳过）。
    """
    now = time.time()
    with _cache_lock:
        c = _cache.get("panel")
        if c and now - c["ts"] < TTL_QUOTES:
            return c["data"]

    raw = _fetch_sina()
    panel = {}
    for key, (symbol, family) in _PANEL_MAP.items():
        f = raw.get(symbol)
        if f and len(f) >= 2:
            panel[key] = _parse(family, f)

    # 健康埋点：核心资产（布伦特/WTI/金/纳指/美元）有价才算健康
    from app import health
    core_ok = all((panel.get(k) or {}).get("price") for k in ("brent", "wti", "gold", "nasdaq", "dxy"))
    health.record("sina_macro", core_ok, "" if core_ok else "核心资产价格缺失")

    # ── 衍生指标 ──
    derived = {}
    g, s_ = panel.get("gold"), panel.get("silver")
    if g and s_ and s_["price"] > 0:
        r_now = g["price"] / s_["price"]
        r_prev = (g["prev_close"] / s_["prev_close"]) if s_["prev_close"] > 0 else r_now
        derived["gold_silver_ratio"] = round(r_now, 2)
        # 金银比日变化%（上行=避险升温=风险偏好回落）
        derived["goldsilver_change_pct"] = round((r_now / r_prev - 1) * 100, 2) if r_prev > 0 else 0.0
    c, b = panel.get("copper"), panel.get("brent")
    if c and b:
        if b["price"] > 0:
            derived["copper_oil_ratio"] = round(c["price"] / b["price"], 2)
        if g and g["price"] > 0:
            derived["copper_gold_ratio"] = round(c["price"] / g["price"], 6)
        if b["price"] > 0:
            derived["gold_oil_ratio"] = round(g["price"] / b["price"], 2) if g else None
    # 在岸/离岸人民币基差（pips）：离岸显著弱于在岸 = 贬值压力
    cnh, cny = panel.get("usdcnh"), panel.get("usdcny")
    if cnh and cny and cnh["price"] > 0 and cny["price"] > 0:
        derived["cny_cnh_basis_pips"] = round((cnh["price"] - cny["price"]) * 10000, 1)
    # 中国10年国债收益率日变化（bp，1bp=0.01%）
    cn = panel.get("cn10y")
    if cn:
        derived["cn10y_bp_change"] = round((cn["price"] - cn["prev_close"]) * 100, 2)
    # 黑色系均值（螺纹+铁矿）
    rb, ir = panel.get("rebar"), panel.get("iron")
    if rb and ir:
        derived["black_change_pct"] = round((rb["change_pct"] + ir["change_pct"]) / 2, 2)

    panel["_derived"] = derived
    with _cache_lock:
        _cache["panel"] = {"data": panel, "ts": now}
    return panel


# ================================================================
#  二、规则配置表（v1）
# ================================================================
# 每条规则：id / group(分组) / metric(指标) / bull(偏多条件) / bear(偏空条件) /
#           strength(强度1~2) / tag_bull / tag_bear / why(传导逻辑)
# bull/bear 形如 {"op": ">", "v": 0.5}；type=band 表示区间规则（油价非单调）。
#
# 阈值是 v1 初值（按各品种日均波幅的量级设定），后续需用历史数据回测校准：
# 触发偏多的次日上证上涨比例 ≥55% 保留、50~55% 降权、<50% 删除。
RULES_VERSION = "macro-rules-v1"

GROUP_WEIGHTS = {
    "china": 0.35,      # 中国直接信号（指示性最强）
    "global": 0.30,     # 全球风险偏好
    "commodity": 0.20,  # 商品/需求
    "internal": 0.15,   # 内部状态（温度+北向）
}

RULES = [
    # ── 中国直接信号 ──
    {"id": "a50_overnight", "group": "china", "metric": "a50.change_pct",
     "bull": {"op": ">", "v": 0.5}, "bear": {"op": "<", "v": -0.5}, "strength": 2.0,
     "tag_bull": "A50隔夜走强", "tag_bear": "A50隔夜走弱",
     "why": "新加坡A50期货隔夜交易，是A股开盘方向的第一指标"},
    {"id": "cny_offshore", "group": "china", "metric": "usdcnh.change_pct",
     "bull": {"op": "<", "v": -0.2}, "bear": {"op": ">", "v": 0.2}, "strength": 1.5,
     "tag_bull": "人民币升值", "tag_bear": "人民币贬值压力",
     "why": "贬值压力引发外资流出；隔夜用离岸价（在岸盘中才有价）"},
    {"id": "cny_cnh_basis", "group": "china", "metric": "cny_cnh_basis_pips",
     "bull": {"op": "<", "v": 30}, "bear": {"op": ">", "v": 120}, "strength": 1.0,
     "tag_bull": "离岸在岸价差正常", "tag_bear": "离岸显著弱于在岸",
     "why": "CNH 显著弱于 CNY 反映离岸做空人民币/资金外流压力"},
    {"id": "cn10y_liquidity", "group": "china", "metric": "cn10y_bp_change",
     "bull": {"op": "<", "v": -2}, "bear": {"op": ">", "v": 3}, "strength": 1.0,
     "tag_bull": "国债收益率下行(宽松)", "tag_bear": "国债收益率快速上行(收紧)",
     "why": "收益率快速下行=宽松预期利好估值；快速上行=流动性收紧担忧"},

    # ── 全球风险偏好 ──
    {"id": "nasdaq_overnight", "group": "global", "metric": "nasdaq.change_pct",
     "bull": {"op": ">", "v": 0.7}, "bear": {"op": "<", "v": -0.7}, "strength": 1.5,
     "tag_bull": "纳指隔夜走强", "tag_bear": "纳指隔夜走弱",
     "why": "全球科技风险偏好，映射A股成长股情绪"},
    {"id": "vix_low", "group": "global", "metric": "vix.price",
     "bull": {"op": "<", "v": 14}, "bear": None, "strength": 1.0,
     "tag_bull": "VIX低位(低波动)", "tag_bear": None,
     "why": "低波环境利好风险资产（水平值规则：14/20/25 是真实分界线）"},
    {"id": "vix_elevated", "group": "global", "metric": "vix.price",
     "bull": None, "bear": {"op": ">", "v": 20}, "strength": 1.5,
     "tag_bull": None, "tag_bear": "VIX偏高(情绪紧张)",
     "why": "VIX>20 全球避险升温，外资风险偏好回落"},
    {"id": "vix_panic", "group": "global", "metric": "vix.price",
     "bull": None, "bear": {"op": ">", "v": 25}, "strength": 2.0,
     "tag_bull": None, "tag_bear": "VIX恐慌",
     "why": "VIX>25 恐慌模式，全球风险资产系统性承压"},
    {"id": "hstech_overnight", "group": "global", "metric": "hstech.change_pct",
     "bull": {"op": ">", "v": 1}, "bear": {"op": "<", "v": -1}, "strength": 1.5,
     "tag_bull": "恒生科技走强", "tag_bear": "恒生科技走弱",
     "why": "中国资产离岸风向标，与A股科技/成长联动强"},
    {"id": "dxy_strength", "group": "global", "metric": "dxy.change_pct",
     "bull": {"op": "<", "v": -0.3}, "bear": {"op": ">", "v": 0.3}, "strength": 1.5,
     "tag_bull": "美元走弱", "tag_bear": "美元走强",
     "why": "强美元抽水新兴市场，压制外资流入"},

    # ── 商品/需求 ──
    {"id": "copper_demand", "group": "commodity", "metric": "copper.change_pct",
     "bull": {"op": ">", "v": 1}, "bear": {"op": "<", "v": -1}, "strength": 1.0,
     "tag_bull": "铜价走强(全球需求健康)", "tag_bear": "铜价走弱(需求担忧)",
     "why": "铜博士=全球制造业需求的实时温度计"},
    {"id": "black_demand", "group": "commodity", "metric": "black_change_pct",
     "bull": {"op": ">", "v": 1}, "bear": {"op": "<", "v": -1}, "strength": 1.5,
     "tag_bull": "黑色系走强(国内需求预期)", "tag_bear": "黑色系走弱(国内需求疲软)",
     "why": "螺纹+铁矿是国内基建地产需求的晴雨表，比铜更『中国』"},
    {"id": "oil_band", "group": "commodity", "metric": "brent.change_pct", "type": "band",
     # 非单调：温和上涨=需求健康；剧烈波动(大涨=输入性通胀/大跌=需求恐慌)都偏空
     "band": {"bull_lo": 0, "bull_hi": 2, "bear_above": 3, "bear_below": -4},
     "strength": 1.0,
     "tag_bull": "油价温和(需求健康)", "tag_bear": "油价剧烈波动",
     "why": "油价大涨=输入性通胀压制宽松空间；大跌=全球需求恐慌"},
    {"id": "goldsilver_ratio", "group": "commodity", "metric": "goldsilver_change_pct",
     "bull": {"op": "<", "v": -1}, "bear": {"op": ">", "v": 1}, "strength": 1.0,
     "tag_bull": "金银比回落(风险偏好回暖)", "tag_bear": "金银比上行(避险升温)",
     "why": "金银比上行=资金弃银投金=避险情绪升温的经典信号"},

    # ── 内部状态 ──
    {"id": "internal_temp", "group": "internal", "metric": "temperature",
     "bull": {"op": ">", "v": 58}, "bear": {"op": "<", "v": 28}, "strength": 1.0,
     "tag_bull": "市场温度偏热", "tag_bear": "市场温度过冷",
     "why": "内部多空力量（盘前用昨收盘值）"},
    {"id": "northbound", "group": "internal", "metric": "northbound_net_yi",
     "bull": {"op": ">", "v": 50}, "bear": {"op": "<", "v": -50}, "strength": 1.5,
     "tag_bull": "北向大幅净流入", "tag_bear": "北向大幅净流出",
     "why": "外资边际态度（2024起仅盘后披露，盘前用T-1值）"},
]


def _resolve_metric(metric: str, panel: dict, temperature, northbound_net_yi):
    """把规则里的指标名解析成数值；取不到返回 None（该规则记为无数据）。"""
    d = panel.get("_derived", {})
    if metric == "temperature":
        return temperature
    if metric == "northbound_net_yi":
        return northbound_net_yi
    if metric in d:
        return d[metric]
    if "." in metric:
        key, field = metric.split(".", 1)
        item = panel.get(key)
        if isinstance(item, dict):
            return item.get(field)
    return None


def _hit(cond, v):
    """判断条件命中。cond 形如 {'op':'>','v':0.5}；v 为 None 时返回 False。"""
    if not cond or v is None:
        return False
    if cond["op"] == ">":
        return v > cond["v"]
    if cond["op"] == "<":
        return v < cond["v"]
    return False


def evaluate_rules(panel: dict, temperature=None, northbound_net_yi=None):
    """
    规则引擎：遍历 RULES，输出 (触发列表, 分组得分, 方向分, 等级)。

    分组得分 = Σ(方向×强度) / Σ(强度)（组内有数据的规则），归一到 [-1,1]；
    方向分 = Σ 组权重×组得分 × 100，范围 [-100,+100]。
    """
    triggered = []
    group_sum = {}    # {group: (Σ方向×强度, Σ强度)}
    group_scores = {}

    for rule in RULES:
        v = _resolve_metric(rule["metric"], panel, temperature, northbound_net_yi)
        direction = 0
        tag = None
        if v is None:
            tag = None   # 无数据 → 不计分也不计入分母
        elif rule.get("type") == "band":
            b = rule["band"]
            if b["bull_lo"] < v <= b["bull_hi"]:
                direction, tag = 1, rule["tag_bull"]
            elif v > b["bear_above"] or v < b["bear_below"]:
                direction, tag = -1, rule["tag_bear"]
        else:
            if _hit(rule.get("bull"), v):
                direction, tag = 1, rule["tag_bull"]
            elif _hit(rule.get("bear"), v):
                direction, tag = -1, rule["tag_bear"]

        if v is not None:
            s_, d_ = group_sum.get(rule["group"], (0.0, 0.0))
            group_sum[rule["group"]] = (s_ + direction * rule["strength"], d_ + rule["strength"])
        if direction != 0 and tag:
            triggered.append({
                "id": rule["id"], "group": rule["group"], "tag": tag,
                "direction": direction, "strength": rule["strength"],
                "value": v, "why": rule["why"],
            })

    total = 0.0
    for g, w in GROUP_WEIGHTS.items():
        s_, d_ = group_sum.get(g, (0.0, 0.0))
        gs = round(s_ / d_, 3) if d_ > 0 else 0.0
        group_scores[g] = gs
        total += w * gs
    score = round(total * 100, 1)

    if score >= 40:
        level = "强多"
    elif score >= 15:
        level = "偏多"
    elif score > -15:
        level = "中性"
    elif score > -40:
        level = "偏空"
    else:
        level = "强空"
    return triggered, group_scores, score, level


# ================================================================
#  三、完整快照（LLM-ready）
# ================================================================

_LEVEL_ADVISORY = {
    "强多": "外部与内部信号共振偏多，可按个股信号积极操作",
    "偏多": "宏观信号偏多，正常执行个股信号",
    "中性": "宏观信号中性，以个股与市场温度为准",
    "偏空": "宏观信号偏空，建议提高买入标准、轻仓为主",
    "强空": "宏观信号全面偏空，以防守为主",
}


def get_macro_snapshot() -> dict:
    """
    一份自包含的快照：面板 + 衍生指标 + 规则标签 + 方向分 + 市场温度。
    这份 JSON 就是喂给 LLM（或人工分析）的全部输入。
    """
    now = time.time()
    with _cache_lock:
        c = _cache.get("snapshot")
        if c and now - c["ts"] < TTL_SNAPSHOT:
            return c["data"]

    panel = get_macro_panel()
    notes = []

    # 内部状态：市场温度（复用 market.py 的缓存逻辑）
    temperature = None
    try:
        from app.routers.market import market_temperature
        t = market_temperature()
        if isinstance(t, dict) and t.get("temperature") is not None:
            temperature = t["temperature"]
    except Exception as e:
        print(f"[macro] 市场温度获取失败: {e}")

    # 北向（盘后数据，盘中常为 0 → 用 None 表示不可用而非当作 0 分）
    northbound_net_yi = None
    try:
        from app.eastmoney import get_northbound
        nb = get_northbound()
        if nb and nb.get("total_net"):
            northbound_net_yi = round(nb["total_net"] / 1e8, 1)   # 元 → 亿
    except Exception:
        pass
    if northbound_net_yi is None:
        notes.append("北向资金盘中不可用（2024起仅盘后披露），内部状态仅用市场温度")

    triggered, group_scores, score, level = evaluate_rules(
        panel, temperature, northbound_net_yi)

    snapshot = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rules_version": RULES_VERSION,
        "panel": {k: v for k, v in panel.items() if not k.startswith("_")},
        "derived": panel.get("_derived", {}),
        "rules_triggered": triggered,
        "tags_bull": [t["tag"] for t in triggered if t["direction"] > 0],
        "tags_bear": [t["tag"] for t in triggered if t["direction"] < 0],
        "direction": {
            "score": score,
            "level": level,
            "advisory": _LEVEL_ADVISORY[level],
            "group_scores": group_scores,
        },
        "market_temperature": temperature,
        "northbound_net_yi": northbound_net_yi,
        "notes": notes,
    }
    with _cache_lock:
        _cache["snapshot"] = {"data": snapshot, "ts": now}
    return snapshot
