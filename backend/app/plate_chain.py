# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】板块产业链传导 —— 关系表 + **事后对账**（2026-11-28）
================================================================================
背景（用户 2026-11-28）：给了一张"万物全息流体沙盘"图，8 层价值流动：
  总源 → 九大板块 → 74 单品 → 反应转化 → 22 产业链 → 26 用品 → 九大需求，
  要求"输入任意事件 → 全产业链传导推演 → 区分上下游盈亏 → 红/绿/金标识"。

★ 为什么不照搬 8 层（改动前必读）：
  `③ 74 单品`（螺纹钢/PTA/黄金现货）与 `④ 反应转化`（高炉炼钢/电解冶炼）是
  **商品与产能**层面的对象，A 股没有对应标的 —— 硬做只能产出无法落到涨跌的文字。
  项目的数据只支撑 **3 层：上游 → 中游 → 下游（板块级）**，
  而 104 个板块本身就是申万二级，天然落在这些链条上（`油气开采及服务` /
  `石油加工贸易` / `化学纤维` / `纺织制造` / `服装家纺` 全都有）。

★ 为什么是"事后对账"而不是"事前预测"（本模块最核心的判定）：
  项目铁律：**不可证伪的规则不许进系统**（见 `thesis_tracker` / `event_targets` 红线）。
  产业链传导的方向判断在直觉上成立，但**没有样本就无法说它对**。
  ⇒ 先做「事件发生日 → 沿链统计各节点 D+n 实际涨跌 → 与预期方向比对」，
    只陈述事实、可逐条证伪；积累够了才谈系数与预测。

★ 为什么不给 -3~+3（用户原框架的量化冲击）：
  实测数据跨度：`plate_daily_zz` 仅 50 个交易日（2026-08-21 起）。
  一个事件只能贡献个位数样本 ⇒ 回归系数必然是噪声。
  ⇒ **只给方向符号（+1 / -1 / 0）**，强度字段留空并标注 `calibrated=False`。
    宁缺勿假 —— 与 11-24「盘前占位包假 0」同一纪律。

★★★ 2026-11-28 最终结论（回补 461 个交易日后，**这个结论推翻了本模块的预测能力**）：
  用户当天选择"先解决历史数据"，于是新增
  `scripts/backfill_plate_history.py` 把 `plate_daily_zz` 从 55 天补到 **461 天**
  （2025-01-02 ~ 2026-11-27，46,979 行，8.4 倍）。结果：

      50 天  → 样本   627，方向命中率 0.518
      461 天 → 样本 9,929，方向命中率 **0.491**

  **样本扩大 16 倍，命中率反而掉到随机线以下。**
  ⇒ "样本不足"这个借口被排除了。真实结论是：
    **A 股板块之间，日频、剔除大盘 beta 后，上游涨跌对下游涨跌几乎没有预测力。**

  ⚠️ 那少数 rate 0.58~0.60 的边（如 `油气开采及服务→石油加工贸易` n=163 rate=0.595）
     也不要当真 —— 从 92 条边里挑最好的 8 条，在 n≈150 时
     期望最大值 ≈ 0.5 + 2σ ≈ 0.58，**与观察值恰好吻合** ⇒ 这是多重比较的假阳性，
     不是信号。（`backtest()` 的返回值里刻意保留了全部边，就是为了能看出这一点。）

  ⇒ 本模块的定位因此**最终确定为**：
    ✅ 关系表 + 事后对账 = **知识结构与事实陈述**（"事件后链条上各板块实际涨跌多少"）
    ❌ **不做任何方向判断、不给 hit/miss 结论、不用于选股或择时**
    `calibrated` 恒为 False；`reconcile()` 的 `hit_rate` 明确标注无统计意义。
  ⇒ 保留 `backtest()` 的唯一作用是**可复现的否定证据** ——
    将来有了更长历史（或更高频数据），重跑一次就能知道结论是否改变。

★ 板块名称口径（最易踩的坑）：
  全部用 `plate_daily_zz.name`（中文）做键，不用 881xxx 代码（zzshare 会变）。
  ⚠️ 任何一处拼错，那条链就静默失效 ⇒ `validate()` 必须能查出来，
     且 `seed()` 落库前先跑校验，名字对不上的**拒绝入库**（不写入半条链）。

不做什么：
  · 不进评分、不进闸门、不碰模拟盘（与事件落点/关键位同级）
  · 不给买卖建议，只输出板块级涨跌事实
================================================================================
"""
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.database import db

_TABLE = "plate_chain"

# ================================================================
#  一、产业链定义（**上游 → 下游，从左到右**）
# ================================================================
# 口径说明：
#   · `nodes` 顺序即上下游关系，相邻两点自动生成一条边；
#   · `sign` 是**成本冲击**方向（默认 -1：上游涨价 → 下游承压）。
#     需求冲击（下游旺 → 上游受益）由调用方用 `direction="up"` 反向走，sign 自动翻。
#   · `lag` 是经验传导时滞（交易日），**不是校准值** —— 后续用对账数据回归替换。
#
# ⚠️ 板块名必须与 `plate_daily_zz.name` 逐字一致；`seed()` 会校验。
CHAINS: Dict[str, Dict] = {
    # ── 能源化工 ──
    "石化链": {
        "nodes": ["油气开采及服务", "石油加工贸易", "化学原料",
                  "化学纤维", "纺织制造", "服装家纺"],
        "lag": 1, "note": "油→炼化→PX/PTA→涤纶→面料→成衣，最标准的成本传导链",
    },
    "化工链": {
        "nodes": ["化学原料", "化学制品", "化工合成材料", "塑料制品"],
        "lag": 2, "note": "基础化工 → 精细化工 → 合成材料 → 下游制品",
    },
    "新材料链": {
        # ★ 2026-11-28 回测修正：原尾部为 "化工新材料" → "电子化学品"，
        #   两者是**包含关系**（电子化学品本身属于化工新材料），不是上下游，
        #   实测该边命中率 0.25 ⇒ 删掉，只保留 化学制品 → 化工新材料 这一跳。
        "nodes": ["化学制品", "化工新材料"],
        "lag": 2, "note": "基础化工 → 高端材料；需求端由电子/新能源拉动而非成本",
    },
    "煤化工链": {
        "nodes": ["煤炭开采加工", "化学原料", "化工合成材料"],
        "lag": 2, "note": "煤价是煤化工的原料成本",
    },
    "橡胶链": {
        "nodes": ["化学制品", "橡胶制品", "汽车零部件", "汽车整车"],
        "lag": 2, "note": "合成橡胶 → 轮胎 → 整车配套",
    },
    "汽车链": {
        "nodes": ["汽车零部件", "汽车整车", "汽车服务及其他"],
        "lag": 2, "note": "零部件 → 整车 → 经销/售后；整车是零部件唯一大客户",
    },
    # ── 金属 ──
    "钢铁链": {
        "nodes": ["石油矿业开采", "钢铁", "金属新材料", "工程机械"],
        "lag": 2, "note": "铁矿→钢→特种材料→设备；地产基建是另一条需求线",
    },
    "有色链": {
        "nodes": ["工业金属", "有色冶炼加工", "金属新材料"],
        "lag": 2, "note": "铜铝锌 → 冶炼加工 → 材料",
    },
    "锂电链": {
        # ★ 2026-11-28 回测修正：原为 ["小金属","能源金属","电池","汽车整车"]，
        #   但 `小金属`（稀土/钨钼）与 `能源金属`（锂钴镍）是**同层并列**而非上下游，
        #   实测该边命中率 0.25（低于随机）⇒ 机制不成立，删掉这一跳。
        "nodes": ["能源金属", "电池", "汽车整车"],
        "lag": 2, "note": "锂钴镍 → 正负极/电解液 → 电芯 → 整车",
    },
    "贵金属链": {
        "nodes": ["贵金属"],
        "lag": 0, "note": "独立链：由实际利率/美元驱动，不由 A 股产业成本驱动",
    },
    # ── 新能源 ──
    "光伏链": {
        "nodes": ["光伏设备", "电力"],
        "lag": 3, "note": "组件降价利好电站运营，是**反向**成本关系（见 CROSS）",
    },
    "风电链": {
        "nodes": ["风电设备", "电力"],
        "lag": 3, "note": "同上，设备与运营是反向成本关系",
    },
    "电网链": {
        "nodes": ["电力设备", "电网设备", "电力"],
        "lag": 3, "note": "设备 → 电网投资 → 发电运营",
    },
    "电机链": {
        "nodes": ["电机", "自动化设备", "通用设备", "工程机械"],
        "lag": 2, "note": "电机/驱动 → 自动化产线 → 通用设备 → 工程机械",
    },
    # ── 电子半导体 ──
    "半导体链": {
        "nodes": ["半导体", "元件", "消费电子"],
        "lag": 1, "note": "芯片 → 元器件 → 整机；SOX 是这条链的隔夜锚",
    },
    "光电链": {
        "nodes": ["元件", "光学光电子", "其他电子"],
        "lag": 1, "note": "面板/LED/光学，需求端看消费电子与车载",
    },
    "消费电子链": {
        "nodes": ["消费电子", "互联网电商"],
        "lag": 2, "note": "整机 → 渠道零售",
    },
    "通信链": {
        "nodes": ["通信设备", "通信服务"],
        "lag": 2, "note": "设备商 → 运营商；运营商资本开支是上游需求",
    },
    "软件链": {
        "nodes": ["软件开发", "IT服务", "计算机应用"],
        "lag": 1, "note": "软件产品 → 集成服务 → 行业应用",
    },
    "硬件链": {
        "nodes": ["计算机设备", "计算机应用"],
        "lag": 1, "note": "服务器/整机 → 应用",
    },
    # ── 医药 ──
    "医药链": {
        "nodes": ["化学原料", "化学制药", "医药商业", "医疗服务"],
        "lag": 3, "note": "原料药 → 制剂 → 流通 → 服务；集采是政策冲击不是成本冲击",
    },
    "器械链": {
        "nodes": ["医疗器械", "医疗服务"],
        "lag": 3, "note": "设备/耗材 → 医院端",
    },
    "中药链": {
        "nodes": ["中药"],
        "lag": 0, "note": "独立链：受药材价格与政策（基药/医保）驱动",
    },
    "生物链": {
        "nodes": ["生物制品"],
        "lag": 0, "note": "独立链：疫苗/血制品，事件驱动为主",
    },
    # ── 农业食品 ──
    "农业链": {
        "nodes": ["农化制品", "种植业与林业", "农产品加工", "食品加工制造"],
        "lag": 3, "note": "化肥农药 → 种植 → 初加工 → 食品制造（成本传导最典型）",
    },
    "养殖链": {
        "nodes": ["养殖业", "农产品加工", "食品加工制造"],
        "lag": 3, "note": "猪价上行利好养殖、压制下游肉制品成本",
    },
    "饮品链": {
        "nodes": ["农产品加工", "饮料制造", "白酒"],
        "lag": 4, "note": "包材/原粮成本对饮料白酒影响弱，主要看需求与提价",
    },
    "纺服链": {
        "nodes": ["纺织制造", "服装家纺"],
        "lag": 2, "note": "面料 → 成衣/家纺；棉花与涤纶是两条成本线",
    },
    "造纸链": {
        "nodes": ["造纸", "包装印刷"],
        "lag": 2, "note": "纸浆 → 原纸 → 包装；纸浆涨价压制包装",
    },
    # ── 地产建材 ──
    "建材链": {
        "nodes": ["建筑材料", "建筑装饰", "房地产"],
        "lag": 3, "note": "水泥/玻璃 → 施工装修 → 开发商（需求由地产投资拉动，方向相反）",
    },
    "地产链": {
        "nodes": ["房地产", "房地产服务", "家居用品"],
        "lag": 3, "note": "销售 → 中介服务 → 家居（后周期）",
    },
    "家电链": {
        "nodes": ["金属新材料", "白色家电", "黑色家电", "厨卫电器", "小家电"],
        "lag": 2, "note": "铜/钢是家电主要成本；四条家电线并列而非串联",
    },
    # ── 交运物流 ──
    "航运链": {
        "nodes": ["港口航运", "物流", "贸易"],
        "lag": 1, "note": "运价 → 物流成本 → 贸易；油运受油价与地缘双重驱动",
    },
    "轨交链": {
        "nodes": ["轨交设备", "公路铁路运输"],
        "lag": 3, "note": "设备 → 运营（重资产，成本端看电价与折旧）",
    },
    "环保链": {
        "nodes": ["环保设备", "环保工程", "环境治理"],
        "lag": 3, "note": "设备 → 工程 → 运营；订单驱动为主",
    },
    # ── 军工 ──
    "军工链": {
        "nodes": ["军工电子", "军工装备"],
        "lag": 2, "note": "上游配套 → 总装；订单与地缘事件驱动，成本传导弱",
    },
    # ── 消费服务 ──
    "零售链": {
        "nodes": ["贸易", "零售", "互联网电商"],
        "lag": 2, "note": "批发 → 零售 → 线上；渠道变革比成本更主导",
    },
    "传媒链": {
        "nodes": ["影视院线", "文化传媒", "游戏", "互联网电商"],
        "lag": 2, "note": "内容 → 平台 → 变现；四条线更像并列而非串联",
    },
    "旅游链": {
        "nodes": ["旅游及酒店", "酒店及餐饮", "其他社会服务"],
        "lag": 1, "note": "出行链，受油价（机票）与消费力驱动",
    },
    # ── 金融（独立链，不参与产业成本传导） ──
    "金融链": {
        "nodes": ["银行", "证券", "保险", "多元金融"],
        "lag": 0, "note": "独立链：利率/流动性驱动，彼此是同业而非上下游",
    },
    # ── 其他独立板块（无上下游，仅登记避免校验报警） ──
    # ★★ 2026-11-28 回测修正：`no_edges=True` —— 这些板块之间**没有**上下游关系，
    #   首版忘了这项标记，`_edges_from_chains` 按节点顺序把 11 个不相干的板块
    #   连成一条 10 跳的"链"（综合→园区→公交→…），实测贡献了 98 个纯噪声样本。
    "独立板块": {
        "nodes": ["综合", "园区开发", "公交", "教育", "美容护理",
                  "仪器仪表", "非汽车交运", "其他电源设备", "非金属材料",
                  "专用设备", "农业服务",
                  # ★ 2026-11-28：以下三个在删掉"机制不成立"的边之后失去归属，
                  #   明确登记在此（"不参与传导"是有意的，不是遗漏）。
                  "小金属",       # 稀土/钨钼，与能源金属同层并列，无稳定下游
                  "燃气",         # 公用事业，气价对其是双刃（收入 vs 采购成本）
                  "电子化学品"],  # 属化工新材料的**子集**，不是其下游
        "lag": 0, "no_edges": True,
        "note": "无稳定上下游关系，不参与传导（明确登记而非遗漏）",
    },
}

# ================================================================
#  二、跨链关系（一条链的变动外溢到另一条链）
# ================================================================
# `sign` = 同向(+1) / 反向(-1)：
#   +1 ⇒ src 涨，dst 同向受益（需求拉动 / 情绪联动）
#   -1 ⇒ src 涨，dst 承压（成本上升 / 替代效应）
# ⚠️ 只登记**机制明确**的跨链影响；"感觉相关"的一律不写（这是本表可信度的前提）。
CROSS: List[Dict] = [
    {"src": "油气开采及服务", "dst": "机场航运", "sign": -1, "lag": 1,
     "note": "油价=航油成本，最大单项成本，负相关最确定"},
    {"src": "油气开采及服务", "dst": "港口航运", "sign": -1, "lag": 1,
     "note": "燃油成本；但油运（VLCC）反而是运价受益，故只挂航运整体需谨慎"},
    # ★ 2026-11-28 回测修正：原登记 `油气开采及服务 → 燃气` sign=+1（气价跟油价），
    #   实测命中率 **0.31**（n=13）显著低于随机 ⇒ 这条边是**双刃**：
    #   气价上行对上游开采是收入、对下游城燃是采购成本，方向取决于谁在定价。
    #   机制不明确 ⇒ **删掉**（本表只登记机制明确的跨链影响）。
    {"src": "煤炭开采加工", "dst": "电力", "sign": -1, "lag": 1,
     "note": "动力煤是火电最大成本"},
    {"src": "工业金属", "dst": "电网设备", "sign": -1, "lag": 2,
     "note": "铜是电缆/变压器主要成本"},
    {"src": "工业金属", "dst": "白色家电", "sign": -1, "lag": 2,
     "note": "铜铝占家电成本比重高"},
    {"src": "工业金属", "dst": "汽车零部件", "sign": -1, "lag": 2,
     "note": "铜铝用量大"},
    {"src": "钢铁", "dst": "建筑材料", "sign": -1, "lag": 2,
     "note": "钢材是建筑主材成本"},
    {"src": "钢铁", "dst": "工程机械", "sign": -1, "lag": 2,
     "note": "钢材成本占比高"},
    {"src": "贵金属", "dst": "有色冶炼加工", "sign": 1, "lag": 1,
     "note": "金银铜同属有色，情绪与资金联动"},
    {"src": "半导体", "dst": "汽车零部件", "sign": 1, "lag": 2,
     "note": "车规芯片供给影响零部件交付"},
    {"src": "半导体", "dst": "军工电子", "sign": 1, "lag": 2,
     "note": "军用芯片同源供应链"},
    {"src": "电力", "dst": "有色冶炼加工", "sign": -1, "lag": 2,
     "note": "电解铝是高耗电，电价即成本（对应『有色冶炼加工』板块）"},
    {"src": "饮料制造", "dst": "包装印刷", "sign": 1, "lag": 2,
     "note": "包材需求随饮品产量"},
    {"src": "房地产", "dst": "建筑材料", "sign": 1, "lag": 3,
     "note": "地产投资是建材需求的源头（需求拉动，非成本）"},
    {"src": "房地产", "dst": "钢铁", "sign": 1, "lag": 3,
     "note": "地产新开工是用钢主力"},
    {"src": "电池", "dst": "能源金属", "sign": 1, "lag": 2,
     "note": "电池排产决定锂钴需求（需求拉动上游）"},
]


# ================================================================
#  三、落库
# ================================================================
_READY = False


def ensure_table() -> None:
    """建表（幂等，兼容 PostgreSQL / SQLite）。"""
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            upstream TEXT NOT NULL,
            downstream TEXT NOT NULL,
            chain TEXT,
            sign INTEGER,
            lag INTEGER,
            note TEXT,
            created_at TEXT,
            PRIMARY KEY (upstream, downstream, chain)
        )
    """)
    _READY = True


def _edges_from_chains() -> List[Dict]:
    """把 CHAINS 的节点序列展开成边（相邻两点一条边，sign 默认 -1 成本冲击）。

    ⚠️ `no_edges=True` 的链（如"独立板块"）**不生成任何边** ——
       它们只是登记名单，节点之间没有上下游关系。
    """
    out = []
    for chain, spec in CHAINS.items():
        if spec.get("no_edges"):
            continue
        nodes = spec.get("nodes") or []
        lag = int(spec.get("lag") or 1)
        for i in range(len(nodes) - 1):
            out.append({
                "upstream": nodes[i], "downstream": nodes[i + 1],
                "chain": chain, "sign": -1, "lag": lag,
                "note": spec.get("note") or "",
            })
    # 跨链：单独成"跨链"链条目（chain 前缀 X: 便于区分与统计）
    for c in CROSS:
        out.append({
            "upstream": c["src"], "downstream": c["dst"],
            "chain": "跨链", "sign": int(c.get("sign") or 1),
            "lag": int(c.get("lag") or 1), "note": c.get("note") or "",
        })
    return out


def validate() -> Dict:
    """校验：链里出现的板块名，是否都在 `plate_daily_zz` 里（防拼错静默失效）。

    返回 `{ok, missing: [...], known: n, edges: n}`。
    """
    known = set()
    try:
        rows = db.fetch("SELECT DISTINCT name FROM plate_daily_zz "
                        "WHERE kind='industry'") or []
        known = {str(r.get("name") or "").strip() for r in rows if r.get("name")}
    except Exception as e:
        return {"ok": False, "error": f"读取板块表失败: {str(e)[:80]}",
                "missing": [], "known": 0, "edges": 0}
    used = set()
    for spec in CHAINS.values():
        used.update(spec.get("nodes") or [])
    for c in CROSS:
        used.add(c["src"])
        used.add(c["dst"])
    missing = sorted(x for x in used if x not in known)
    return {
        "ok": not missing,
        "missing": missing,          # 空 = 全部对得上
        "used": len(used),
        "known": len(known),
        "edges": len(_edges_from_chains()),
        "uncovered": sorted(known - used),   # 库里存在但没进任何链的板块
    }


def seed(force: bool = False) -> Dict:
    """把关系表写入数据库（幂等）。

    ⚠️ 先 `validate()`：**只要有一个板块名对不上就拒绝入库** ——
       半条链比没有链更危险（会让对账结果悄悄少一段，而看不出为什么）。
    """
    ensure_table()
    v = validate()
    if not v["ok"]:
        return {"ok": False, "error": "板块名校验未通过，拒绝入库",
                "missing": v["missing"], "edges": 0}
    edges = _edges_from_chains()
    from app.flash.rules import beijing_now as _bj
    now = _bj().strftime("%Y-%m-%d %H:%M:%S")
    written = 0
    try:
        if force:
            db.execute(f"DELETE FROM {_TABLE}")
        batch = [{**e, "created_at": now} for e in edges]
        written = db.upsert_many(_TABLE, batch,
                                 conflict_columns=["upstream", "downstream", "chain"],
                                 page_size=200)
    except Exception as e:
        return {"ok": False, "error": f"写库失败: {str(e)[:100]}", "edges": len(edges)}
    return {"ok": True, "edges": len(edges), "written": written,
            "chains": len(CHAINS), "cross": len(CROSS),
            "uncovered": v.get("uncovered") or []}


def load() -> List[Dict]:
    """读回关系表（空则现场 seed 一次）。"""
    ensure_table()
    try:
        rows = db.fetch(f"SELECT upstream, downstream, chain, sign, lag, note "
                        f"FROM {_TABLE} ORDER BY chain, upstream") or []
        if rows:
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[plate_chain] load failed: {str(e)[:80]}")
    seed()
    try:
        return [dict(r) for r in
                (db.fetch(f"SELECT upstream, downstream, chain, sign, lag, note "
                          f"FROM {_TABLE} ORDER BY chain, upstream") or [])]
    except Exception:
        return []


# ================================================================
#  四、传播（从种子板块出发，沿链走 N 跳）
# ================================================================
def chain_of(name: str) -> List[Dict]:
    """某板块所在的所有链及位置：`[{chain, index, nodes, direction}]`。"""
    out = []
    for chain, spec in CHAINS.items():
        nodes = spec.get("nodes") or []
        if name in nodes:
            i = nodes.index(name)
            out.append({"chain": chain, "index": i, "nodes": nodes,
                        "position": ("上游" if i == 0 else
                                     "下游" if i == len(nodes) - 1 else "中游")})
    return out


def propagate(seed: str, hops: int = 2) -> List[Dict]:
    """从 `seed` 板块出发，沿链向下游走 `hops` 跳。

    返回 `[{name, chain, hop, sign, lag, expect, note}]`：
      `expect` = +1(预期同向受益) / -1(预期承压)
    语义：**假设 seed 出现"涨价/景气上行"** ⇒ 下游按成本传导承压（sign=-1）。
    需求方向（下游旺→上游受益）由调用方用 `seed` 取下游端点实现，不在这里反推。
    """
    out, seen = [], {(seed, 0)}
    frontier = [(seed, 0, 1, 0)]        # (name, hop, sign 累积, lag 累积)
    while frontier:
        name, hop, acc_sign, acc_lag = frontier.pop(0)
        if hop >= hops:
            continue
        for chain, spec in CHAINS.items():
            if spec.get("no_edges"):       # 独立板块：只登记名单，不传导
                continue
            nodes = spec.get("nodes") or []
            if name not in nodes:
                continue
            i = nodes.index(name)
            # ★ 一次**只走一步**（原实现 `range(i+1, i+1+剩余跳数)` 会一次生成多跳，
            #   把 hop=1 与 hop=2 混在一起、同一节点重复出现且符号错乱 —— 实测踩到）。
            if i + 1 >= len(nodes):
                continue
            nxt = nodes[i + 1]
            key = (nxt, hop + 1)
            if key in seen:
                continue
            seen.add(key)
            lag = int(spec.get("lag") or 1)
            rec = {"name": nxt, "chain": chain, "hop": hop + 1,
                   "sign": acc_sign * -1,          # 每跳翻一次（成本传导）
                   "lag": acc_lag + lag, "note": spec.get("note") or ""}
            out.append(rec)
            frontier.append((nxt, hop + 1, rec["sign"], rec["lag"]))
        # 跨链外溢（只走一跳，避免在跨链表里无限扩散）
        if hop == 0:
            for c in CROSS:
                if c["src"] != name:
                    continue
                key = (c["dst"], hop + 1)
                if key in seen:
                    continue
                seen.add(key)
                rec = {"name": c["dst"], "chain": "跨链", "hop": hop + 1,
                       "sign": int(c.get("sign") or 1),
                       "lag": int(c.get("lag") or 1), "note": c.get("note") or ""}
                out.append(rec)
    return out


# ================================================================
#  五、事后对账（核心：只陈述事实）
# ================================================================
def _dates(limit: int = 0) -> List[str]:
    """板块表的交易日序列（正序）。"""
    try:
        rows = db.fetch("SELECT DISTINCT date FROM plate_daily_zz "
                        "WHERE kind='industry' ORDER BY date") or []
        ds = [str(r.get("date"))[:10] for r in rows if r.get("date")]
        return ds[-limit:] if limit else ds
    except Exception:
        return []


def _pct(day: str, name: str) -> Optional[float]:
    """某板块某日涨跌幅（%）；缺失返回 None（**不返回 0**）。"""
    try:
        r = db.fetch_one("SELECT change_pct FROM plate_daily_zz "
                         "WHERE date=%s AND kind='industry' AND name=%s",
                         (day, name)) or {}
        v = r.get("change_pct")
        return float(v) if v is not None else None
    except Exception:
        return None


def _cum(day: str, name: str, days: int, seq: List[str]) -> Optional[float]:
    """`day` 之后 `days` 个交易日的累计涨跌幅（%，复利）。"""
    try:
        i = seq.index(day)
    except ValueError:
        return None
    acc = 1.0
    for k in range(i + 1, min(i + 1 + days, len(seq))):
        p = _pct(seq[k], name)
        if p is None:
            return None
        acc *= (1 + p / 100.0)
    return round((acc - 1) * 100, 2)


def reconcile(day: str, seed: str, windows: Tuple[int, ...] = (1, 3, 5),
              hops: int = 2) -> Dict:
    """**事后对账**：`day` 当天 `seed` 板块异动后，链条上各节点的实际表现。

    只输出事实：各节点 D+n 累计涨跌 + 是否与预期方向一致（hit)。
    `expect` 的含义：seed 大涨（成本上行）⇒ 下游预期承压（-1）。
    """
    seq = _dates()
    if day not in seq:
        return {"ok": False, "error": f"{day} 不在板块交易日序列里",
                "available": (seq[-1] if seq else None)}
    base = _pct(day, seed)
    # ★ 种子方向：对账判定必须带它 —— 种子跌时"下游受益"也是吻合，
    #   只看 `v * sign` 会把种子下跌日的正确传导判成 miss（实测踩到）。
    seed_dir = 1 if (base or 0) > 0 else -1
    # 同一板块可能出现在多条链（如化学原料同时在石化链与化工链）⇒ 按名字去重取最小 hop
    merged: Dict[str, Dict] = {}
    for n in [{"name": seed, "chain": "种子", "hop": 0, "sign": 0,
               "lag": 0, "note": ""}] + propagate(seed, hops):
        cur = merged.get(n["name"])
        if cur is None or n["hop"] < cur["hop"]:
            merged[n["name"]] = n
    rows = []
    for n in merged.values():
        rets = {f"D+{w}": _cum(day, n["name"], w, seq) for w in windows}
        # 方向判定：只看第一个可用的窗口
        verdict = None
        for w in windows:
            v = rets.get(f"D+{w}")
            if v is None:
                continue
            if n["sign"] == 0:
                verdict = "n/a"
            elif abs(v) < 0.3:                      # 噪声带：±0.3% 内视为无方向
                verdict = "flat"
            else:
                verdict = "hit" if (v * n["sign"] * seed_dir > 0) else "miss"
            break
        rows.append({**n, "rets": rets, "verdict": verdict})
    judged = [r for r in rows if r["verdict"] in ("hit", "miss")]
    return {
        "ok": True,
        "day": day,
        "seed": seed,
        "seed_pct": base,
        "windows": list(windows),
        "nodes": rows,
        "n_hit": sum(1 for r in judged if r["verdict"] == "hit"),
        "n_miss": sum(1 for r in judged if r["verdict"] == "miss"),
        "hit_rate": (round(sum(1 for r in judged if r["verdict"] == "hit")
                           / len(judged), 3) if judged else None),
        "calibrated": False,        # ★ 强度未校准，永远是 False（见模块头）
        # ★★ 2026-11-28：`hit_rate` 必须标"无效" ——
        #   单次对账通常只有 3~8 个节点，比率毫无统计意义；
        #   而 461 天全样本的总体命中率是 **0.491（低于随机）**，
        #   见模块头"最终结论"。保留该字段只为让人看到原始计数。
        "hit_rate_valid": False,
        "hit_rate_note": "单次对账的 hit_rate 无统计意义（样本 3~8 个）；"
                         "461 天全样本总体命中率 0.491（低于随机 0.5）。"
                         "本字段仅作原始记录，不构成任何预测依据。",
        "note": "只陈述事实：种子板块异动后各节点的实际涨跌。"
                "⚠️ 方向吻合与否**不代表传导规律成立**（总体命中率 0.491）。"
                "不含预测、不含系数、不构成投资建议。",
    }


def _matrix() -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    """一次读全表 → `({date: {name: pct}}, [date...])`。

    ★ 为什么不用逐条 `_pct()`：逐边回测是 100 边 × 50 天 = 5000 次往返，
      Supabase 免费档下行恒定 8.4 KB/s（见 `sector_zz` 注释）⇒ 会拖到分钟级。
      一次 `SELECT` 拉 104×50=5200 行（≈100KB）在内存里算只要几百毫秒。
    """
    m: Dict[str, Dict[str, float]] = {}
    try:
        rows = db.fetch("SELECT date, name, change_pct FROM plate_daily_zz "
                        "WHERE kind='industry'") or []
    except Exception as e:
        print(f"[plate_chain] matrix load failed: {str(e)[:80]}")
        return {}, []
    for r in rows:
        d = str(r.get("date"))[:10]
        v = r.get("change_pct")
        if v is None:
            continue
        m.setdefault(d, {})[str(r.get("name"))] = float(v)
    return m, sorted(m.keys())


# ★ 2026-11-28：矩阵全表读（104×50≈5200 行）不该每次请求都跑 ——
#   `verify()` 是手动触发的自检接口，但用户可能连点几下。
#   TTL 300s：板块序列一天只变一次（收盘后日批落库），与 `sector_zz._SCAN_CACHE` 同口径。
_MX_CACHE = {"ts": 0.0, "val": None}
_MX_TTL = 300.0


def _matrix_cached():
    """`_matrix()` 的 5 分钟缓存版（供 API 调用，避免重复拉全表）。"""
    now = time.time()
    if _MX_CACHE["val"] is not None and now - _MX_CACHE["ts"] < _MX_TTL:
        return _MX_CACHE["val"]
    val = _matrix()
    _MX_CACHE.update({"ts": now, "val": val})
    return val


def backtest(threshold: float = 3.0, window: int = 1,
             min_samples: int = 3, noise: float = 0.3,
             demean: bool = True) -> Dict:
    """**验收用**：逐条边验证"上游异动 → 下游 D+window 方向是否吻合"。

    ★★ 必须去 beta（`demean=True`，默认开）——实测驱动，改动前必读：
      首版用**绝对涨跌**判定：`up_pct * dst_pct * sign > 0`，总命中率 **0.48**，
      刚好等于随机基线。原因不是关系表写错，而是 A 股板块联动极强：
      上游"大涨"的那天往往是全市场普涨日，下游跟着涨 ⇒ 成本传导（负相关）
      被 beta（正相关）完全淹没，测出来的是大盘，不是传导。
      ⇒ 改用**相对强弱**：两边各自减去当日全板块均值，只比超额部分。
        这样才问得出"上游跑赢市场的日子，下游是否跑输市场"。

    判定式：`(up - mean) * (dst - mean) * sign > 0`
      上游跑赢 & 下游跑输 & sign=-1 ⇒ (+)(-)(-) > 0 ⇒ 命中。
      上游跑输 & 下游跑赢 & sign=-1 ⇒ (-)(+)(-) > 0 ⇒ 命中（成本下降利好下游）。

    参数：
      threshold   触发阈值：上游**超额**涨跌的绝对值 ≥ 此值才算"事件日"
                  ⚠️ 去 beta 后阈值应下调（超额 1.5% 已经很显著，绝对值 3% 才相当于 1~2%）
      window      下游观察窗口（交易日）
      min_samples 样本不足该数的边不参与统计（避免 1 个样本就下结论）
      noise       下游超额 |涨跌| < 此值视为"无方向"，不计入命中/偏离
      demean      是否剔除当日全板块均值（默认 True；设 False 可复现首版对照）

    返回 `{ok, edges:[...], n_edges, n_hit, n_miss, hit_rate, by_chain, note}`
    """
    m, seq = _matrix_cached()
    if not seq:
        return {"ok": False, "error": "板块表为空"}
    edges = load()
    if not edges:
        return {"ok": False, "error": "关系表为空（seed 失败？）"}
    # 每日全板块均值（作为 beta 基准）
    day_mean: Dict[str, float] = {}
    for d, vals in m.items():
        vs = [v for v in vals.values() if v is not None]
        if vs:
            day_mean[d] = sum(vs) / len(vs)
    detail = []
    for e in edges:
        up, down, sign = e["upstream"], e["downstream"], int(e.get("sign") or 1)
        hit = miss = flat = 0
        for i, d in enumerate(seq):
            if i + window >= len(seq):
                break
            up_pct = (m.get(d) or {}).get(up)
            if up_pct is None:
                continue
            base = day_mean.get(d) or 0.0
            up_x = up_pct - base if demean else up_pct
            if abs(up_x) < threshold:
                continue
            acc = 1.0
            macc = 1.0
            ok_data = True
            for k in range(i + 1, i + 1 + window):
                p = (m.get(seq[k]) or {}).get(down)
                if p is None:
                    ok_data = False
                    break
                acc *= (1 + p / 100.0)
                macc *= (1 + (day_mean.get(seq[k]) or 0.0) / 100.0)
            if not ok_data:
                continue
            dst_pct = (acc - 1) * 100
            if demean:
                dst_x = dst_pct - (macc - 1) * 100
            else:
                dst_x = dst_pct
            if abs(dst_x) < noise:
                flat += 1
                continue
            if up_x * dst_x * sign > 0:
                hit += 1
            else:
                miss += 1
        n = hit + miss
        if n < min_samples:
            continue
        detail.append({"upstream": up, "downstream": down, "chain": e.get("chain"),
                       "sign": sign, "lag": e.get("lag"),
                       "n": n, "hit": hit, "miss": miss, "flat": flat,
                       "hit_rate": round(hit / n, 3)})
    tot_h = sum(x["hit"] for x in detail)
    tot_m = sum(x["miss"] for x in detail)
    by_chain: Dict[str, Dict] = {}
    for x in detail:
        c = by_chain.setdefault(x["chain"], {"n": 0, "hit": 0, "edges": 0})
        c["n"] += x["n"]
        c["hit"] += x["hit"]
        c["edges"] += 1
    for c in by_chain.values():
        c["hit_rate"] = round(c["hit"] / c["n"], 3) if c["n"] else None
    detail.sort(key=lambda x: (-x["hit_rate"], -x["n"]))
    return {
        "ok": True,
        "threshold": threshold, "window": window,
        "n_edges": len(detail),
        "n_hit": tot_h, "n_miss": tot_m,
        "hit_rate": round(tot_h / (tot_h + tot_m), 3) if (tot_h + tot_m) else None,
        "by_chain": by_chain,
        "edges": detail,
        "note": "方向命中率的**随机基线是 0.5**（涨/跌二分）。"
                "⚠️ 2026-11-28 实测：461 个交易日、9929 个样本的**总体**命中率为 "
                "**0.491（低于随机）** ⇒ 板块间日频方向传导不可测。"
                "个别边看似 0.58~0.60 属**多重比较假阳性**（92 条边挑最好 8 条，"
                "n≈150 时期望最大值本就 ≈0.58）。"
                "本函数保留的唯一用途是**可复现的否定证据**，不是策略回测、更不是信号。",
    }
