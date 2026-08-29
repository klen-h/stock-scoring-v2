"""
================================================================================
【文件作用】快讯规则层（移植自 flash-monitor/scripts/rules.js + chinaMarket.js）
================================================================================

纯函数、无 IO。包含三块：
  1. 快讯过滤/聚类常量（正则、关键词、事件簇）
  2. 市场时钟（多市场交易时段感知，全项目唯一实现）
  3. 数据质量评估（按市场时钟分级降级 LLM 可执行步骤——原项目精华设计）

移植说明：
  - 合并了原项目两份不一致的 getMarketClock（统一用 %24 正确回绕版本）
  - 中国节假日日历做成 dict，便于逐年追加
================================================================================
"""

import re
from datetime import datetime, timedelta, timezone

# 北京时间时区（所有市场时段判断都基于北京时间）
_TZ8 = timezone(timedelta(hours=8))


def beijing_now() -> datetime:
    """当前北京时间（tz-aware）。"""
    return datetime.now(_TZ8)


# ================================================================
#  一、快讯过滤 / 聚类常量
# ================================================================

# 硬排除模式（正则）：标题党/客套话/与宏观无关的碎嘴
EXCLUDE_PATTERNS = [
    re.compile(r"^【金十数据整理[：:]"),
    re.compile(r"^【今日重点关注的财经数据"),
    re.compile(r"^【财料】"),
    re.compile(r"^【金十整理[：:]"),
    re.compile(r"涨\d+%.*股价再创历史新高"),
    re.compile(r"估值或升至逾\d+亿美元"),
    re.compile(r"Good afternoon", re.I),
    re.compile(r'"好好先生"', re.I),
    re.compile(r'特朗普.*"太迟先生"', re.I),
    re.compile(r"特朗普.*其他地方没人要他", re.I),
]

# 低价值关键词（包含即丢弃）
LOW_VALUE_KEYWORDS = [
    "俏皮话", "最后一次新闻发布会", '不会成为"影子主席"',
    "部署时间创纪录", "厕所也反复出现问题",
]

# 时间敏感关键词（含 '.*' 的按正则处理，其余按包含匹配）
URGENT_TIME_KEYWORDS = [
    "几小时内", "即将", "马上", "立刻", "立即",
    "倒计时", "最后期限", "最后通牒",
    "周一早上", "周二", "明天", "今晚",
    "行动开始", "行动将在", "启动.*行动",
    "几小时", "数小时内", "接下来",
]

# 宏观相关关键词（命中则放行，避免纯个股财报被误杀）
A_STOCK_KEYWORDS = [
    "原油", "油价", "石油", "WTI", "布伦特", "Brent", "EIA",
    "欧佩克", "OPEC", "页岩油", "战略储备", "储油", "管道",
    "霍尔木兹", "海峡", "油轮", "油运", "航运",
    "沙特", "阿联酋", "科威特", "伊拉克", "委内瑞拉",
    "三桶油", "中石油", "中石化", "中海油",
    "化工", "塑料", "PTA", "沥青", "化肥",
    "通胀", "CPI", "PPI", "美联储", "加息", "降息", "鲍威尔",
    "央行", "降准", "MLF", "LPR",
    "A股", "上证", "深证", "创业板", "沪指", "沪深300",
    "汇金", "社保基金", "国家队",
    "伊朗", "核计划", "封锁", "美伊", "中东", "战争",
    "中美", "关税", "贸易", "制裁",
    "证券", "券商", "银行", "保险", "半导体", "芯片",
    "房地产", "限购", "公积金",
]

# 事件簇定义（首个命中的簇生效，未命中 → "其他"）
# keywords 中含 '.*' 的按正则处理，其余按包含匹配
EVENT_CLUSTERS = [
    {"name": "原油能源", "keywords": [
        "原油", "油价", "石油", "WTI", "布伦特", "Brent",
        "EIA", "欧佩克", "OPEC", "页岩油", "战略储备",
        "霍尔木兹", "海峡", "油轮", "储油", "管道", "出口",
        "沙特", "阿联酋", "科威特", "伊拉克", "委内瑞拉",
        "三桶油", "中石油", "中石化", "中海油",
        "化工", "塑料", "PTA", "沥青", "化肥", "油运",
    ]},
    {"name": "伊朗局势", "keywords": [
        "伊朗", "核计划", "封锁", "特朗普.*伊朗", "美伊", "伊美", "哈梅内伊",
    ]},
    {"name": "中东战争", "keywords": [
        "战争授权", "战争权力法", "60天", "国会授权", "军事行动", "以军", "真主党", "哈马斯",
    ]},
    {"name": "美联储利率", "keywords": [
        "美联储", "FOMC", "利率决议", "维持利率", "降息", "加息", "鲍威尔", "沃什",
    ]},
    {"name": "美联储人事", "keywords": [
        "沃什", "美联储主席提名", "参议院", "米兰", "哈玛克", "卡什卡利",
    ]},
    {"name": "黄金贵金属", "keywords": [
        "黄金", "增持黄金", "世界黄金协会", "白银", "央行购金",
    ]},
    {"name": "国内政策", "keywords": [
        "证监会", "央行", "降准", "降息", "LPR", "MLF", "限购", "公积金", "房地产",
    ]},
    {"name": "中美贸易", "keywords": [
        "中美", "关税", "贸易", "半导体", "华虹", "脱钩",
    ]},
    {"name": "俄乌局势", "keywords": [
        "普京", "俄乌", "乌克兰", "停火", "胜利日",
    ]},
    {"name": "港股/中概股", "keywords": [
        "港股", "恒生", "科网股", "小米", "阿里巴巴", "百度", "中芯国际",
    ]},
]

# 板块集体行动模式（正则）：命中则视为"板块级"而非个股新闻
_SECTOR_PATTERNS = [
    re.compile(r"集体走高|集体上扬|集体大涨|集体飙升"),
    re.compile(r"涨幅扩大至\d+%"),
    re.compile(r"涨超.*涨超"),          # 至少 2 个"涨超"
    re.compile(r"科网股|芯片股|半导体股|地产股|汽车股"),
    re.compile(r"港股.*涨|恒生.*涨"),
]

# 纯个股财报模式
_STOCK_REPORT_PATTERN = re.compile(r"一季度净利润|第一季度净利润|Q1净利润|一季度营收|第一季度营收")


def _kw_match(kw: str, content: str) -> bool:
    """关键词匹配：含 '.*' 视为正则，否则子串包含。"""
    if ".*" in kw:
        return re.search(kw, content) is not None
    return kw in content


def is_sector_move(content: str) -> bool:
    """是否为板块级集体行动（而非单只个股异动）。"""
    return any(p.search(content) for p in _SECTOR_PATTERNS)


def has_urgent_time(content: str) -> bool:
    """是否含时间敏感词（决定是否重推已推送的簇）。"""
    return any(_kw_match(kw, content) for kw in URGENT_TIME_KEYWORDS)


def pre_filter(items: list) -> list:
    """
    快讯硬过滤：排除模式 / 低价值词 / 纯个股财报（非板块级且无宏观词）。
    items: [{id, time, hot, content, ...}]
    """
    out = []
    for item in items:
        content = item.get("content") or ""
        if any(p.search(content) for p in EXCLUDE_PATTERNS):
            continue
        if any(kw in content for kw in LOW_VALUE_KEYWORDS):
            continue
        is_stock_report = bool(_STOCK_REPORT_PATTERN.search(content))
        if (is_stock_report and not is_sector_move(content)
                and not any(kw in content for kw in A_STOCK_KEYWORDS)):
            continue
        out.append(item)
    return out


def cluster_items(items: list) -> list:
    """
    事件聚合：按 EVENT_CLUSTERS 首个命中的簇归类，同簇合并。
    代表条目取 source_link 最长者（信息最全），簇热度取最高（爆>沸）。

    返回：[{**代表条目, _cluster, _clusterSize, _clusterHot, _clusterTime, _allItems}]
    """
    clusters = []          # [{clusterName, representative, allItems, hotMax, earliestTime}]
    used_ids = set()
    for item in items:
        if item["id"] in used_ids:
            continue
        content = item.get("content") or ""
        matched = None
        for cdef in EVENT_CLUSTERS:
            if any(_kw_match(kw, content) for kw in cdef["keywords"]):
                matched = cdef
                break
        name = matched["name"] if matched else "其他"
        existing = next((c for c in clusters if c["clusterName"] == name), None)
        if existing is None:
            clusters.append({
                "clusterName": name, "representative": item, "allItems": [item],
                "hotMax": 2 if item.get("hot") == "爆" else 1,
                "earliestTime": item.get("time", ""),
            })
        else:
            existing["allItems"].append(item)
            if item.get("hot") == "爆":
                existing["hotMax"] = 2
            if len(item.get("source_link") or "") > len(existing["representative"].get("source_link") or ""):
                existing["representative"] = item
        used_ids.add(item["id"])

    result = []
    for c in clusters:
        rep = dict(c["representative"])
        rep["_cluster"] = c["clusterName"]
        rep["_clusterSize"] = len(c["allItems"])
        rep["_clusterHot"] = "爆" if c["hotMax"] == 2 else "沸"
        rep["_clusterTime"] = c["earliestTime"]
        rep["_allItems"] = c["allItems"]
        result.append(rep)
    return result


# 重大更新重推规则：首次命中这些军事/外交升级关键词 → 重新推送
MAJOR_UPDATE_RULES = [
    {"key": "hasMilitary", "kw": ["军事行动"]},
    {"key": "hasStrike", "kw": ["打击方案"]},
    {"key": "hasAction", "kw": ["行动开始"]},
    {"key": "hasDeployment", "kw": ["15000名", "导弹驱逐舰", "航母", "军机"]},
    {"key": "wasRejected", "kw": ["不可接受"]},
    {"key": "wasBroken", "kw": ["违反停火"]},
]
MAJOR_UPDATE_ALWAYS = ["重启空袭", "恢复打击"]   # 命中必重推


def is_major_update(cluster_content: str, existing_state: dict) -> bool:
    """已推送的簇是否构成"重大更新"需要重推。"""
    if has_urgent_time(cluster_content):
        return True
    for rule in MAJOR_UPDATE_RULES:
        kws = rule["kw"] if isinstance(rule["kw"], list) else [rule["kw"]]
        if any(kw in cluster_content for kw in kws) and not existing_state.get(rule["key"]):
            return True
    if any(kw in cluster_content for kw in MAJOR_UPDATE_ALWAYS):
        return True
    return False


# ================================================================
#  二、市场时钟（全项目唯一实现）
# ================================================================

def get_market_clock() -> dict:
    """
    多市场交易时段感知（北京时间）：
      A股/港股连续竞价 9:30-11:30 / 13:00-15:00
      恒科延展（ETF 收盘后指数仍在发布）15:00-16:30
      日经（北京时间）8:00-10:30 / 11:30-14:00（近似取整）
      美股常规 21:30-04:00（夏令时）
    """
    now = beijing_now()
    t = now.hour * 60 + now.minute
    a_morning = 570 <= t < 690
    a_afternoon = 780 <= t < 900
    is_a_stock = a_morning or a_afternoon
    is_hstech_ext = 900 <= t < 990
    is_nikkei = (480 <= t < 630) or (690 <= t < 840)
    is_us = t >= 1290 or t < 240     # 跨午夜，正确回绕
    return {
        "beijing_time": f"{now.hour:02d}:{now.minute:02d}",
        "is_a_stock_trading": is_a_stock,
        "is_hstech_extended": is_hstech_ext,
        "is_nikkei_trading": is_nikkei,
        "is_us_trading": is_us,
        "is_asia_equity_closed": not (is_a_stock or is_hstech_ext or is_nikkei),
    }


# 中国 A 股节假日日历（按年追加即可，格式：{(月, 日): 假期名区间}）
# 来源：上交所/深交所公告；原项目为 2026 年版
HOLIDAYS = {
    2026: [
        ((1, 1), (1, 3), "元旦"),
        ((2, 15), (2, 23), "春节"),
        ((4, 4), (4, 6), "清明"),
        ((5, 1), (5, 5), "劳动节"),
        ((6, 19), (6, 21), "端午"),
        ((9, 25), (9, 27), "中秋"),
        ((10, 1), (10, 7), "国庆"),
    ],
}


def get_china_market_status() -> dict:
    """A股当前交易状态 {is_open, reason}（节假日/周末/时段）。"""
    now = beijing_now()
    md = (now.month, now.day)
    for (lo, hi, name) in HOLIDAYS.get(now.year, []):
        if lo <= md <= hi:
            return {"is_open": False, "reason": f"{name}假期休市"}
    if now.weekday() >= 5:   # 0=周一 ... 5/6=周六/周日
        return {"is_open": False, "reason": "周末休市"}
    t = now.hour * 100 + now.minute
    if t < 915:
        return {"is_open": False, "reason": "未开盘 (9:15前)"}
    if t < 925:
        return {"is_open": True, "reason": "开盘集合竞价中 (9:15-9:25)"}
    if t <= 1130:
        return {"is_open": True, "reason": "上午交易中 (9:25-11:30)"}
    if t < 1300:
        return {"is_open": False, "reason": "午间休市 (11:30-13:00)"}
    if t < 1457:
        return {"is_open": True, "reason": "下午交易中 (13:00-14:57)"}
    if t <= 1500:
        return {"is_open": True, "reason": "收盘集合竞价中 (14:57-15:00)"}
    return {"is_open": False, "reason": "已收盘 (15:00后)"}


def is_trading_day(dt=None) -> bool:
    """是否为 A 股交易日（只判周末 + 法定节假日，不判盘中时段）。

    与 get_china_market_status() 的区别：后者把 15:00 后一律判为"已收盘"，
    而盘后复盘窗口（15:03-23:59）本就落在收盘之后，用它做门禁会把正常的
    盘后复盘一并拦掉。判断"今天该不该跑复盘"只需要日期维度的交易日。
    """
    now = dt or beijing_now()
    md = (now.month, now.day)
    for (lo, hi, _name) in HOLIDAYS.get(now.year, []):
        if lo <= md <= hi:
            return False
    return now.weekday() < 5      # 0=周一 ... 5/6=周六/周日


# ================================================================
#  三、数据质量评估（按市场时钟分级降级）
# ================================================================

def evaluate_data_quality(panel: dict, holdings: list) -> dict:
    """
    检查 7 项数据源，输出质量等级 + 激活/中止的 LLM 步骤。
    panel: app/macro.get_macro_panel() 的返回值
    holdings: ETF 行情列表（休市时为 []）

    降级规则（原项目精华，保留原设计）：
      - 缺纽约原油 → 严重不足，中止整个诊断模块
      - 缺 ≥3 项   → 严重不足，中止多情景生成（仅单线推演）
      - 缺 1-2 项  → 部分缺失，中止对应具体步骤
      - 不缺       → 充足，激活相关性诊断 + （盘中且有 ETF）盘面交叉验证
    """
    clock = get_market_clock()
    missing = []

    def _ok(key):
        item = panel.get(key) or {}
        return item.get("price", 0) not in (0, None)

    if not _ok("wti"):
        missing.append("纽约原油涨跌方向")
    gold = panel.get("gold") or {}
    if not gold.get("price") or not gold.get("prev_close"):
        missing.append("黄金涨跌方向")
    if not _ok("dxy"):
        missing.append("美元指数走势")
    if not _ok("nasdaq"):
        missing.append("纳指期货盘中表现")
    # 恒科：仅亚盘时段要求实时数据；收盘后静态数据不算缺失
    if (clock["is_a_stock_trading"] or clock["is_hstech_extended"]) and not _ok("hstech"):
        missing.append("恒生科技盘中表现")
    if not _ok("nikkei"):
        missing.append("日经225期货盘中表现")
    # ETF：仅 A 股交易时段期望有实时数据
    if clock["is_a_stock_trading"] and not holdings:
        missing.append("对应ETF的盘面数据")

    data_quality = "充足"
    activated = ["1.1 必要数据清单", "1.2 数据质量判定", "2.4 事件簇分析"]
    aborted = []
    if "纽约原油涨跌方向" in missing:
        data_quality = "严重不足"
        aborted.append("整个诊断模块 (原因: 缺失纽约原油涨跌方向)")
    elif len(missing) >= 3:
        data_quality = "严重不足"
        aborted.append("多情景生成 (原因: 数据严重不足，仅执行单线推演)")
    elif missing:
        data_quality = "部分缺失"
        if "黄金涨跌方向" in missing:
            aborted.append("相关性诊断步骤1 (原因: 黄金方向不明)")
        if "美元指数走势" in missing:
            aborted.append("D状态步骤2 (原因: 美元细节缺失)")
        if any(k in missing for k in ("纳指期货盘中表现", "日经225期货盘中表现", "恒生科技盘中表现")):
            aborted.append("D状态步骤3 (原因: 风险资产数据缺失)")

    if data_quality != "严重不足":
        activated.append("2.2 原油-黄金相关性诊断")
        if "对应ETF的盘面数据" not in missing and clock["is_a_stock_trading"]:
            activated.append("2.1 盘面交叉验证")

    return {
        "data_quality": data_quality,
        "missing_items": missing,
        "activated_steps": activated,
        "aborted_steps": aborted,
        "overall_confidence": {"充足": "高", "部分缺失": "中"}.get(data_quality, "低"),
        "market_clock": {
            "beijing_time": clock["beijing_time"],
            "is_a_stock_trading": clock["is_a_stock_trading"],
            "is_hstech_extended": clock["is_hstech_extended"],
            "is_nikkei_trading": clock["is_nikkei_trading"],
            "is_us_trading": clock["is_us_trading"],
            "is_asia_equity_closed": clock["is_asia_equity_closed"],
        },
    }
