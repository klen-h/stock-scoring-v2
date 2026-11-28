# -*- coding: utf-8 -*-
"""
================================================================================
政策脉冲：政策快讯 → 受益板块映射（2026-11-28）
================================================================================
【为什么做】用户复盘 11-27：
  "政策中午发文，午盘开盘猛拉，我当时不知所措，犹豫不决，错过上车好时机。"
  实测时间线：13:24 首条政策快讯 → 13:33 中证1000期货涨停 → 13:36 创业板指+10%。
  **从政策到期货涨停只有 9 分钟**，任何"研究后决策"都来不及。

  系统当时**没有任何一处把"这是一条政策级利好"喊出来**：

  · `flash_news.cluster` 虽有「国内政策」，但同主题后续几条被分到「其他」
    （11-27 的"规范减持"、"伪科技"就是 cluster=其他），**标签不可靠**
  · `event_targets.THEME_INDUSTRY` 是"主题→行业"，主题来自 cluster ⇒ 跟着错
  · `flash_brief` 是**盘后**汇总，不是盘中即时
  ⇒ 结论：必须**按正文关键词**识别，不能信 cluster。

【定位】纯观察器 · 零决策链
  · 不改评分、不进买入闸门、不碰模拟盘
  · 只回答"这条政策可能影响哪些板块"，**不给买卖建议**
  · 输出的是**线索**，不是信号 —— 政策利好到板块上涨仍需市场确认

【口径】
  · 板块名必须与 `plate_daily_zz(kind='industry')` 的 104 个完全一致
  · 板块涨幅优先取 `plate_daily_zz`（收盘）；盘中该日未落库 ⇒ chg_pct=None，如实标注
  · 同一主题多条快讯**合并为一条事件**，first_time 取最早出现时刻
================================================================================
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.database import db

BJ = timezone(timedelta(hours=8))

# ── 政策主体：出现在正文里才算"政策级"，避免把媒体评论当政策 ──
#   结构 (规范名, [同义写法]) —— 同义合并，避免"国务院/国常会/国务院常务会议"并列出现。
#   顺序即**优先级**：越靠前级别越高，一条快讯命中多个时取最靠前的。
POLICY_ACTORS: List[Tuple[str, List[str]]] = [
    ("国常会", ["国常会", "国务院常务会议"]),
    ("政治局会议", ["政治局会议"]),
    ("中央经济工作会议", ["中央经济工作会议"]),
    ("国务院", ["国务院"]),
    ("证监会", ["证监会"]),
    ("央行", ["央行", "中国人民银行"]),
    ("发改委", ["国家发改委", "发改委"]),
    ("财政部", ["财政部"]),
    ("工信部", ["工信部", "工业和信息化部"]),
    ("国资委", ["国资委"]),
    ("市场监管总局", ["市场监管总局"]),
    ("金融监管总局", ["金融监管总局"]),
    ("外汇局", ["外汇局"]),
    ("商务部", ["商务部"]),
]

# ── 主题 → (触发词, 受益板块) ──
#   ⚠️ 触发词宁可窄不可宽：`["科技"]` 会把"伪科技"这种**抑制性**表述也带进来。
#   ⚠️ 板块名必须命中 `plate_daily_zz` 的 104 个 industry 名单。
THEME_SECTOR: Dict[str, Tuple[List[str], List[str]]] = {
    "资本市场改革": (
        ["资本市场", "投融资综合改革", "上市公司治理", "上市公司质量",
         "减持", "IPO", "分红", "回购", "长期资金", "长钱长投"],
        ["证券", "多元金融", "软件开发", "半导体"],
    ),
    "科技创新": (
        ["科技产业", "科技创新", "新质生产力", "自主可控", "集成电路",
         "半导体", "人工智能", "先进封装", "算力"],
        ["半导体", "电子化学品", "软件开发", "计算机应用", "通信设备", "元件"],
    ),
    "货币宽松": (
        ["降准", "降息", "流动性充裕", "社会融资条件", "货币政策工具",
         "保持流动性", "宽货币"],
        ["银行", "证券", "房地产", "贵金属"],
    ),
    "设备更新": (
        ["设备更新", "以旧换新", "消费品换新", "大规模设备"],
        ["通用设备", "专用设备", "白色家电", "汽车整车", "仪器仪表"],
    ),
    "新能源": (
        ["新能源", "光伏", "风电", "储能", "新型电力系统"],
        ["光伏设备", "风电设备", "电池", "电网设备"],
    ),
    "地产链": (
        ["房地产", "保交楼", "城市更新", "地产"],
        ["房地产", "房地产服务", "建筑材料", "建筑装饰"],
    ),
    "促消费": (
        ["促消费", "扩内需", "消费券", "提振消费"],
        ["白酒", "食品加工制造", "零售", "饮料制造", "旅游及酒店"],
    ),
    "央企整合": (
        ["专业化整合", "央企", "合并精简", "产能过剩", "低价竞争"],
        ["建筑装饰", "钢铁", "煤炭开采加工", "军工装备"],
    ),
}

# ── 抑制性表述：出现则**不算利好**，整条跳过（避免"严防伪科技"被算成"科技创新"利好） ──
#   ★ 2026-11-28 实测教训：11-27 13:31「严防"伪科技"借机上市」原本被聚合成
#     "科技创新"主题的**首条**，标题还成了"严防伪科技" —— 方向完全反了。
NEGATIVE_HINTS = [
    "严肃查处", "严打", "严禁", "严防", "处罚", "打击", "编造传播", "立案调查",
    "借机上市", "注水上市", "无真实业绩", "违规", "非法",
]

# ── 汇总类快讯：媒体日终汇总，会把 23:05 的内容算进当日下午的主题 ──
DIGEST_HINTS = ["金十数据整理", "每日", "汇总", "速递", "过去24小时"]


def _title_of(content: str) -> str:
    """取快讯标题：`【xxx】` 内文，没有则取前 40 字。"""
    m = re.match(r"^【([^】]{2,80})】", content or "")
    if m:
        return m.group(1).strip()
    return (content or "").strip().replace("\n", " ")[:40]


def _primary_actor(text: str) -> str:
    """返回**级别最高**的政策主体（命中多个取优先级靠前的），没有返回空串。"""
    for canon, alts in POLICY_ACTORS:
        if any(a in text for a in alts):
            return canon
    return ""


def _hit_themes(text: str) -> List[Tuple[str, List[str]]]:
    out = []
    for theme, (kws, sectors) in THEME_SECTOR.items():
        if any(k in text for k in kws):
            out.append((theme, sectors))
    return out


def _latest_day() -> str:
    r = db.fetch_one("SELECT MAX(date) AS d FROM plate_daily_zz WHERE kind='industry'")
    return str((r or {}).get("d") or "")[:10]


def _sector_quotes(day: str, names: List[str]) -> List[Dict]:
    """取指定板块当日涨跌与净流入（`plate_daily_zz`，收盘口径）。缺失返回空列表。"""
    if not names:
        return []
    ph = ",".join(["%s"] * len(names))
    rows = db.fetch(
        f"""SELECT name, change_pct, net_inflow, trade_money
            FROM plate_daily_zz
            WHERE date=%s AND kind='industry' AND name IN ({ph})""",
        tuple([day] + names),
    ) or []
    by = {r["name"]: r for r in rows}
    out = []
    for n in names:
        r = by.get(n)
        out.append({
            "name": n,
            "chg_pct": round(float(r["change_pct"]), 2) if r and r.get("change_pct") is not None else None,
            "net_inflow_yi": (round(float(r["net_inflow"]) / 1e8, 2)
                              if r and r.get("net_inflow") is not None else None),
            "trade_money_yi": (round(float(r["trade_money"]) / 1e8, 1)
                               if r and r.get("trade_money") is not None else None),
        })
    return out


def scan(day: Optional[str] = None, hours: int = 0, max_events: int = 8) -> Dict:
    """扫描政策快讯并映射到受益板块。

    参数：
      day    指定交易日（默认取 `plate_daily_zz` 最新日）
      hours  若 >0 则取"最近 N 小时"（跨日，用于盘中实时），忽略 day
      max_events 最多返回几条事件（按首次出现时间正序）

    返回：
      {ok, date, mode, n_scanned, events:[{first_time,last_time,actor,theme,title,
       hot,cluster,negative,sectors:[...],n_news}]}
    """
    latest = _latest_day()
    day = (day or latest)[:10]

    if hours and hours > 0:
        since = (datetime.now(BJ) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        rows = db.fetch(
            """SELECT id, time, content, cluster, hot, important
               FROM flash_news WHERE time >= %s ORDER BY time""", (since,)) or []
        mode = f"recent{hours}h"
    else:
        rows = db.fetch(
            """SELECT id, time, content, cluster, hot, important
               FROM flash_news WHERE time LIKE %s ORDER BY time""", (day + "%",)) or []
        mode = "day"

    # 按主题聚合
    agg: Dict[str, Dict] = {}
    n_skipped_neg = 0
    n_skipped_digest = 0
    for r in rows:
        text = r.get("content") or ""
        if not text:
            continue
        # 汇总类快讯一律跳过（否则 23:05 的"欧盘美盘汇总"会把时间拖到深夜）
        if any(h in text for h in DIGEST_HINTS):
            n_skipped_digest += 1
            continue
        themes = _hit_themes(text)
        if not themes:
            continue
        actor = _primary_actor(text)
        # 无政策主体 ⇒ 只认「国内政策」聚类且 important，避免媒体评论混入
        if not actor and not (r.get("important") and (r.get("cluster") or "") == "国内政策"):
            continue
        # 抑制性表述整条跳过：监管"严防/查处"不是利好
        if any(h in text for h in NEGATIVE_HINTS):
            n_skipped_neg += 1
            continue
        t = str(r.get("time") or "")
        for theme, sectors in themes:
            e = agg.get(theme)
            if not e:
                e = agg[theme] = {
                    "theme": theme,
                    "actor": "",
                    "sectors": set(),
                    "first_time": t,
                    "last_time": t,
                    "title": _title_of(text),
                    "hot": r.get("hot") or "",
                    "cluster": r.get("cluster") or "",
                    "negative": False,
                    "n_news": 0,
                    "news_ids": [],
                }
            # ★ 主体取**首次命中条**的级别最高者，不合并 —— 否则会显示
            #   "中国人民银行/国务院/国常会/国资委/证监会"一长串，看不出是谁发的
            if not e["actor"] and actor:
                e["actor"] = actor
            e["sectors"].update(sectors)
            e["n_news"] += 1
            e["news_ids"].append(r.get("id"))
            if t and (not e["first_time"] or t < e["first_time"]):
                e["first_time"] = t
                e["title"] = _title_of(text)          # 用最早那条的标题
            if t and t > (e["last_time"] or ""):
                e["last_time"] = t
            if r.get("hot") == "爆":
                e["hot"] = "爆"

    events = []
    for e in agg.values():
        names = sorted(e["sectors"])
        events.append({
            "theme": e["theme"],
            "actor": e["actor"] or ("国内政策" if e["cluster"] == "国内政策" else ""),
            "title": e["title"],
            "first_time": e["first_time"],
            "last_time": e["last_time"],
            "hot": e["hot"],
            "cluster": e["cluster"],
            "negative": e["negative"],
            "n_news": e["n_news"],
            "sectors": _sector_quotes(day, names),
            "news_ids": e["news_ids"][:20],
        })

    # 排序：有政策主体的优先，其次首次出现时间正序
    events.sort(key=lambda x: (x["actor"] == "", x["first_time"] or ""))
    events = events[:max_events]

    # 当日是否已落板块快照（决定 sectors 的 chg_pct 是否为定稿值）
    has_snapshot = bool(db.fetch_one(
        "SELECT 1 AS x FROM plate_daily_zz WHERE date=%s AND kind='industry' LIMIT 1", (day,)))

    # ★ 2026-11-28：并入「广度确认」—— 11-27 的完整信号是
    #   13:24 政策 + 13:33 期货涨停 + 13:36 创业板+10%，只给政策不给广度仍然不完整。
    #   两段口径不同（广度=收盘定稿、期货=盘中快讯），各自带说明，不混为一谈。
    try:
        from app import market_breadth as mb
        _breadth = mb.breadth(day)
        _fut = mb.futures_limit(day=day, hours=hours)
        # ★★ 2026-11-28：午盘开盘异动 —— 唯一**早于政策快讯**的信号。
        #   11-27 实测：创业板 13:00→13:05 相对上午收盘再涨 +3.46%，
        #   而首条政策快讯 13:24 才到（晚约 19 分钟）。
        #   ⇒ 政策回答"为什么涨"，这个字段回答"已经在涨了"。
        _open = mb.open_pulse(day)
    except Exception:
        _breadth, _fut, _open = None, [], None

    return {
        "ok": True,
        "date": day,
        "mode": mode,
        "n_scanned": len(rows),
        "n_skipped_negative": n_skipped_neg,
        "n_skipped_digest": n_skipped_digest,
        "n_events": len(events),
        "sector_quotes_final": has_snapshot,
        "note": ("板块涨跌为**收盘定稿**口径（plate_daily_zz 已落库）" if has_snapshot
                 else "当日板块快照未落库 ⇒ 涨跌幅为 null（盘中只给板块名单，涨跌请看实时行情）"),
        "events": events,
        # ★ 广度确认（见 market_breadth.py）：政策是"利好"，广度是"市场认不认"
        "breadth": _breadth,
        "futures_limit": _fut,
        "n_futures": len(_fut),
        # ★★ 午盘开盘异动（分钟级）：**比政策快讯早**，盘中增量采集才有数据
        "open_pulse": _open,
    }


def recent(hours: int = 24) -> Dict:
    """盘中用：最近 N 小时的政策脉冲（跨日）。"""
    return scan(hours=hours)
