"""
================================================================================
【文件作用】板块数据基础设施：① 个股→行业映射 ② 板块每日快照
================================================================================

两部分共用同一套"东财数据源校验"逻辑（板块代码必须是 BK 前缀，否则视为降级新浪）。

  ① 映射（build_map）：个股属于哪个行业，含嵌套层级链
  ② 每日快照（take_snapshot）：每天记录全部板块的涨跌幅/涨跌家数/资金流，
     积累板块历史序列，供板块分化、板块动量类因子使用。
     成本极低——get_sectors + get_sector_flow 各一次请求就覆盖全部板块。
================================================================================

为什么需要：评分引擎要加"板块分化"因子，前提是知道每只股票属于哪个板块，
才能算"个股相对所属板块的强弱"。东财只提供"板块 → 成分股"的正向查询，
没有"个股 → 所属板块"的反查接口，所以这里遍历全部板块反向建表。

★ 关键认知 1：东财单次请求最多返回 100 条，且是【静默截断】
  必须用 eastmoney._fetch_clist_paged 分页。用 _fetch_clist(limit=2000) 实际
  只能拿到前 100 条且无任何报错 —— 大板块（基础化工 454 只）会被截成 100 只。

★ 关键认知 2：东财行业板块是【嵌套多层级】的，不是扁平唯一分类
  实测 000912 泸天化同时属于 ['氮肥'(6只) / '农化制品'(~80只) / '基础化工'(454只)]，
  即 氮肥 ⊂ 农化制品 ⊂ 基础化工。所以每只股票存两份信息：
    - main_industry：成分股最少的板块（最细分，区分度最高，适合做板块内对比）
    - industry_chain：从细到粗的完整层级链（可做"细分板块 vs 大行业"两层对比）

刷新策略：全量重建（不搞增量）
  全量约 100 个板块 × 平均 1-5 页 ≈ 300-500 次请求，实测 20-40 秒，每月跑一次
  成本完全可接受。增量逻辑看似省事，但要区分"新股加入"和"主业变更改分类"，
  容易漏掉后者（个股换了行业却还挂着旧归属，比没有归属更危险）。

数据落地：数据库表 stock_industry / industry_map_meta（schema.sql 定义）
================================================================================
"""

import json
import time
from collections import defaultdict

from app.database import db
from app.eastmoney import get_sectors, get_sector_flow, _fetch_clist_paged

# 板块之间的请求间隔（秒）。
# 反爬实测：连续几百次请求会触发东财断连甚至临时封 IP。
# ★ 2026-08-15 教训：0.3s 板块间隔 × 0.25s 翻页间隔跑建映射，把 IP 封了 ~48h，
#   盘中板块资金流/分化因子数据一起挂。统一放宽到 1s（建映射约 10 分钟，月级任务可接受）。
_SECTOR_GAP = 1.0


def _now_iso() -> str:
    from app.flash.rules import beijing_now
    return beijing_now().isoformat()


def _sector_members(sector_code: str, max_items: int = 3000) -> dict:
    """
    板块的全部成分股（分页全量）。返回 {股票代码: 股票名称}。
    东财 fs 前缀 "b:" 表示查询某板块的成分股。
    """
    rows = _fetch_clist_paged(f"b:{sector_code}", "f12,f14", max_items=max_items)
    out = {}
    for r in rows:
        code = r.get("f12")
        if code:
            out[code] = r.get("f14") or ""
    return out


# ================================================================
#  新浪行业分类全市场映射（★ 替代东财 build_map 的推荐方案）
# ================================================================
# 为什么换源：东财 build_map 需遍历 200 个板块 × 分页 ≈ 300-500 次请求，
# 2026-08-15 实测把 IP 封了 ~48h（盘中板块资金流/分化因子一起挂）。且实测
# 东财 clist 默认排序导致深市主板（000 前缀）只收录 165/500，覆盖缺口。
# 新浪方案：1 次拿 49 个行业节点 + 每节点 1 次成分股（num=500 一次拉全）
# ≈ 50 次请求 / 30 秒，单请求体量小、无东财风控，覆盖全市场 ~5700 只。
# 行业粒度：新浪一级行业（49 个）比东财细分（159 个）粗——但"覆盖完整、
# 分类一致"是板块共振/评分板块因子的前提，粒度粗不影响可用性；
# 东财细分链可作为可选增强（未来解封后再补）。

_SINA_BASE = "http://vip.stock.finance.sina.com.cn"
_SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "http://finance.sina.com.cn/",
}


def _fetch_sina_sectors() -> dict:
    """新浪行业节点列表：{节点代码: 行业名}（newSinaHy.php 一次返回 49 个）。"""
    import re, requests
    r = requests.get(f"{_SINA_BASE}/q/view/newSinaHy.php",
                     headers=_SINA_HEADERS, timeout=15)
    r.encoding = "gbk"
    m = re.search(r"=\s*(\{.*\})", r.text, re.S)
    if not m:
        return {}
    data = json.loads(m.group(1))
    out = {}
    for node, val in data.items():
        parts = val.split(",")
        out[node] = parts[1] if len(parts) > 1 else node
    return out


def _fetch_sina_members(node: str) -> dict:
    """新浪行业成分股（分页全量）：{纯6位code: 名称}。

    ★ 实测该接口 num 参数无效（恒 100 只/页），必须翻页：page 递增直到
    返回 <100 只或本页全重复。单行业最多几百只，5 页兜底足够。
    """
    import requests
    out = {}
    for page in range(1, 6):
        try:
            r = requests.get(
                f"{_SINA_BASE}/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                params={"node": node, "num": 100, "page": page},
                headers=_SINA_HEADERS, timeout=15)
            r.encoding = "gbk"
            arr = json.loads(r.text) or []
        except Exception:
            break
        added = 0
        for x in arr:
            code = str(x.get("code") or "").strip()
            if code and len(code) == 6 and code.isdigit() and code not in out:
                out[code] = str(x.get("name") or "")
                added += 1
        if len(arr) < 100 or added == 0:
            break
    return out


def build_map_sina(verbose: bool = True) -> dict:
    """★ 新浪全市场行业映射（推荐：无封 IP 风险，覆盖全市场）。

    返回 {ok, sectors, stocks, cost_sec, error}；失败保留旧表（宁旧勿空）。
    """
    t0 = time.time()
    sectors = _fetch_sina_sectors()
    if not sectors:
        return {"ok": False, "error": "新浪行业列表为空（接口异常？）"}
    mapping = {}
    total = 0
    for node, name in sectors.items():
        members = _fetch_sina_members(node)
        for code, cname in members.items():
            mapping[code] = {
                "code": code,
                "name": cname,
                "main_industry": name,
                "main_industry_code": node,
                "industry_chain": json.dumps([name], ensure_ascii=False),
                "industry_codes": json.dumps([node], ensure_ascii=False),
            }
        total += len(members)
        if verbose:
            print(f"[industry] {node} {name:<8} {len(members)} 只")
        time.sleep(0.3)          # 行业节点间留间隔（总量小，30 秒可完成）
    if not mapping:
        return {"ok": False, "error": "所有行业成分股均为空"}
    now = _now_iso()
    batch = [{
        "code": code,
        "name": m["name"],
        "main_industry": m["main_industry"],
        "main_industry_code": m["main_industry_code"],
        "industry_chain": m["industry_chain"],
        "industry_codes": m["industry_codes"],
        "updated_at": now,
    } for code, m in mapping.items()]
    try:
        written = db.upsert_many("stock_industry", batch, conflict_columns=["code"],
                                 page_size=1000)
    except Exception as e:
        print(f"[industry] 新浪映射批量写入失败: {e}")
        return {"ok": False, "error": f"写入失败: {e}"}
    for k, v in (("built_at", now), ("sectors", str(len(sectors))),
                 ("stocks", str(len(mapping))), ("source", "sina"),
                 ("multi_level", "0")):
        db.upsert("industry_map_meta", {"key": k, "value": v}, conflict_columns=["key"])
    cost = round(time.time() - t0, 1)
    clean = cleanup_industry_residual()
    print(f"[industry] 新浪映射完成: {len(sectors)} 行业 / {len(mapping)} 只，{cost}s"
          f"（归一化 {clean.get('fixed', 0)} 个旧分类）")
    return {"ok": True, "sectors": len(sectors), "stocks": len(mapping),
            "cost_sec": cost, "cleaned": clean.get("fixed", 0)}


# ================================================================
#  东财细分 → 新浪一级行业 自动归一化
# ================================================================
# 背景：新浪 49 行业覆盖约 2994 只，东财映射另含约 1937 只（细分行业名如
# "农商行Ⅲ""轨交设备Ⅲ"）。若残留细分类，主线/共振分析会出现"伪切换信号"
# （同一批股票换个行业名就被当成资金退出）。故建映射后把全表统一为新浪一级。
_SINA_INDUSTRIES = frozenset([
    "玻璃行业", "船舶制造", "传媒娱乐", "电力行业", "电器行业", "电子器件",
    "电子信息", "房地产", "发电设备", "飞机制造", "纺织行业", "纺织机械",
    "服装鞋类", "公路桥梁", "供水供气", "钢铁行业", "环保行业", "化工行业",
    "化纤行业", "家电行业", "酒店旅游", "家具行业", "金融行业", "交通运输",
    "机械行业", "建筑建材", "开发区", "酿酒行业", "摩托车", "煤炭行业",
    "农林牧渔", "农药化肥", "汽车制造", "其它行业", "塑料制品", "水泥行业",
    "食品行业", "次新股", "生物制药", "商业百货", "石油行业", "陶瓷行业",
    "物资外贸", "医疗器械", "仪器仪表", "印刷包装", "有色金属", "综合行业",
    "造纸行业",
])

# 关键词 → 新浪一级行业（顺序即优先级，先具体后兜底）
_EA_MERGE_RULES = [
    (("化纤",), "化纤行业"),
    (("水泥",), "水泥行业"),
    (("造纸",), "造纸行业"),
    (("医疗器械",), "医疗器械"),
    (("银行", "保险", "证券", "金融", "期货", "信托", "券商", "农商行", "城商行"),
     "金融行业"),
    # 发电设备须在机械前（"电网自动化设备"含"自动化"，会被机械规则先命中）
    (("电源设备", "电网", "配电", "电机", "风电", "光伏", "电池"), "发电设备"),
    (("电商", "营销", "零售", "百货", "商贸", "会展", "贸易"), "商业百货"),
    (("汽车零部件", "底盘", "轮胎", "轮毂", "汽车电子", "汽车综合"), "汽车制造"),
    (("软件", "IT", "计算机", "数字芯片", "模拟芯片", "半导体", "集成电路", "芯片",
      "印制电路", "面板", "光学元件", "LED", "被动元件", "消费电子", "电信", "通信",
      "电子化学品", "军工电子", "安防", "电子"), "电子信息"),
    (("互联网", "游戏", "传媒", "影视", "出版", "门户", "广告", "数字媒体", "广播",
      "院线"), "传媒娱乐"),
    (("专用设备", "通用设备", "工控", "自动化", "机器人", "激光", "机床", "磨具",
      "工程机械", "楼宇", "印刷包装机械", "轨交", "金属制品", "机械设备"), "机械行业"),
    (("航天装备", "航空装备", "国防军工", "军工"), "飞机制造"),
    (("家居", "家电", "家用电器", "电器", "厨房", "空调", "制冷", "卫浴", "照明",
      "彩电", "黑色家电"), "家电行业"),
    (("塑料", "橡胶", "有机硅", "炭黑", "胶黏", "印染", "涤纶", "煤化工", "化学"),
     "化工行业"),
    (("农药", "化肥"), "农药化肥"),
    (("固废", "大气治理", "环保", "水务"), "环保行业"),
    (("金属新材料", "磁性材料", "稀土"), "有色金属"),
    (("贵金属", "黄金", "白银", "工业金属", "冶钢原料", "有色", "铝", "铜", "锂",
      "钨", "钴", "稀有金属", "矿业", "采矿", "资源"), "有色金属"),
    (("燃气", "天然气", "供热"), "供水供气"),
    (("钢铁",), "钢铁行业"),
    (("煤炭", "动力煤"), "煤炭行业"),
    (("石油", "油田", "油服"), "石油行业"),
    (("医疗", "医药", "制药", "生物", "疫苗", "检测服务"), "生物制药"),
    (("建筑", "装修", "装饰", "工程咨询", "钢结构", "瓷砖", "玻纤", "建材", "管材"),
     "建筑建材"),
    (("服装", "鞋帽", "家纺", "服饰"), "服装鞋类"),
    (("纺织", "辅料"), "纺织行业"),
    (("养殖", "饲料", "农业", "种植", "水产", "牧渔", "种业"), "农林牧渔"),
    (("食品", "饮料", "乳品", "调味"), "食品行业"),
    (("酒",), "酿酒行业"),
    (("公交", "铁路", "公路", "机场", "航运", "物流", "港口", "运输"), "交通运输"),
    (("物业", "地产", "置业", "开发"), "房地产"),
    (("包装", "印刷"), "印刷包装"),
    (("酒店", "旅游", "餐饮", "景区"), "酒店旅游"),
    (("汽车",), "汽车制造"),
]


def _normalize_industry(name: str) -> str:
    """东财细分行业名 → 新浪一级行业名（已在新浪一级则原样返回，未知兜底其它）。"""
    if not name:
        return "其它行业"
    if name in _SINA_INDUSTRIES:
        return name
    for kws, target in _EA_MERGE_RULES:
        if any(k in name for k in kws):
            return target
    return "其它行业"


def cleanup_industry_residual() -> dict:
    """把 stock_industry 中东财残留的细分行业统一到新浪一级（幂等，可重复跑）。

    返回 {ok, fixed, mapping}；fixed = 发生变更的行业名数量。
    """
    rows = db.fetch("SELECT DISTINCT main_industry FROM stock_industry")
    updates = {}
    for r in rows or []:
        old = r["main_industry"]
        new = _normalize_industry(old)
        if new != old:
            updates[old] = new
    for old, new in updates.items():
        db.execute("UPDATE stock_industry SET main_industry = %s "
                   "WHERE main_industry = %s", (new, old))
    print(f"[industry] 归一化完成: {len(updates)} 个旧分类 -> 新浪一级 "
          f"({updates if updates else '无变更'})")
    return {"ok": True, "fixed": len(updates), "mapping": updates}


def reclassify_others() -> dict:
    """对 main_industry='其它行业' 的行，用原始行业名（main_industry_code）重分。

    触发场景：归一化规则完善后（如新增"贵金属→有色"），早期被兜底到
    "其它行业"的股票应重新归类。main_industry_code 保留 f100/f127 原始
    东财行业名，可据此再归一化；新浪 node（new_xxx）无法细分则跳过。
    """
    rows = db.fetch("SELECT code, name, main_industry_code FROM stock_industry "
                    "WHERE main_industry = '其它行业'")
    moved = {}
    for r in rows or []:
        raw = (r["main_industry_code"] or "").strip()
        if not raw or raw in _SINA_INDUSTRIES or raw.startswith("new_"):
            continue
        new = _normalize_industry(raw)
        if new != "其它行业":
            moved.setdefault(new, []).append(r["code"])
    for new, codes in moved.items():
        ph = ",".join(["%s"] * len(codes))
        db.execute(f"UPDATE stock_industry SET main_industry = %s "
                   f"WHERE code IN ({ph})", [new] + codes)
    if moved:
        print(f"[industry] 其它行业重分类: " +
              ", ".join(f"{k}{len(v)}只" for k, v in moved.items()))
    else:
        print("[industry] 其它行业重分类: 无可细分（或规则已完善）")
    return {"ok": True, "moved": {k: len(v) for k, v in moved.items()}}


def build_map_full(verbose: bool = True) -> dict:
    """★ 东财 clist 全市场行业映射（f100=行业名）+ 新浪一级归一化。

    为什么这是覆盖最全的方案：
      - 东财 build_map 按板块遍历要 300-500 次请求（曾封 IP 48h）；
      - 新浪 build_map_sina 只要 ~90 次但只覆盖约 3000 只（漏科创板/创业板/
        北交所及部分沪深主板，实测 943 只缺口）；
      - 本函数用 clist 全市场接口 + f100 行业字段，约 60 次请求 / 20-30 秒，
        覆盖 ~5900 只（含 688/300/301/920 全部市场），f100 为空时保留旧记录。
    行业统一为新浪一级（_normalize_industry），与 build_map_sina 完全同口径。
    """
    import requests
    t0 = time.time()
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0",
                      "Referer": "https://data.eastmoney.com/"})
    em = {}
    for pn in range(1, 70):
        try:
            r = s.get("http://push2.eastmoney.com/api/qt/clist/get", params={
                "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f12",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                "fields": "f12,f14,f100",
            }, timeout=10)
            rows = (r.json().get("data") or {}).get("diff") or []
        except Exception:
            break
        for x in rows:
            code = str(x.get("f12") or "").strip()
            if code and len(code) == 6 and code.isdigit():
                raw = str(x.get("f100") or "").strip()
                if raw and raw != "-":
                    em[code] = (str(x.get("f14") or ""), raw)
        if verbose:
            print(f"[industry] 东财全市场 第{pn}页 {len(rows)} 只（累计 {len(em)}）")
        if len(rows) < 100:
            break
        time.sleep(0.4)
    if not em:
        return {"ok": False, "error": "东财 clist 全市场为空（接口异常？）"}
    now = _now_iso()
    batch = []
    for code, (name, raw) in em.items():
        ind = _normalize_industry(raw)
        batch.append({
            "code": code, "name": name, "main_industry": ind,
            "main_industry_code": raw,
            "industry_chain": json.dumps([ind], ensure_ascii=False),
            "industry_codes": json.dumps([raw], ensure_ascii=False),
            "updated_at": now,
        })
    try:
        written = db.upsert_many("stock_industry", batch, conflict_columns=["code"],
                                 page_size=1000)
    except Exception as e:
        print(f"[industry] 东财全市场写入失败: {e}")
        return {"ok": False, "error": f"写入失败: {e}"}
    clean = cleanup_industry_residual()
    for k, v in (("built_at", now), ("sectors", "49"),
                 ("stocks", str(len(em))), ("source", "eastmoney_full"),
                 ("multi_level", "0")):
        db.upsert("industry_map_meta", {"key": k, "value": v}, conflict_columns=["key"])
    cost = round(time.time() - t0, 1)
    print(f"[industry] 东财全市场映射完成: {len(em)} 只，{cost}s"
          f"（归一化 {clean.get('fixed', 0)} 个旧分类）")
    return {"ok": True, "sectors": 49, "stocks": len(em), "cost_sec": cost,
            "cleaned": clean.get("fixed", 0)}


def backfill_missing(verbose: bool = True) -> dict:
    """用东财单股接口（f127=行业细分名）补齐 stock_industry 尾部缺失股票。

    触发场景：新浪 49 行业与东财 clist f100 都漏掉的尾部股票（新股/北交所/
    深主板 001/002 新段）。单股查询按需轻量（1 只/请求，间隔 0.3s），封 IP
    风险远低于板块遍历（此前 174 次单股查询实测安全）。
    """
    import requests, json as _json
    t0 = time.time()
    base = "http://vip.stock.finance.sina.com.cn"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "http://finance.sina.com.cn/"}
    all_stocks = {}
    for page in range(1, 70):
        try:
            r = requests.get(
                f"{base}/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                params={"node": "hs_a", "num": 100, "page": page},
                headers=headers, timeout=15)
            r.encoding = "gbk"
            arr = _json.loads(r.text) or []
        except Exception:
            break
        for x in arr:
            code = str(x.get("code") or "").strip()
            if code and len(code) == 6 and code.isdigit():
                all_stocks.setdefault(code, str(x.get("name") or ""))
        if len(arr) < 100:
            break
        time.sleep(0.1)
    have = {r["code"] for r in db.fetch("SELECT code FROM stock_industry")}
    gap = {c: n for c, n in all_stocks.items() if c not in have}
    if not gap:
        print("[industry] 回补: 无缺失（映射已全量覆盖）")
        return {"ok": True, "gap": 0, "filled": 0, "cost_sec": 0}
    print(f"[industry] 回填补缺: {len(gap)} 只缺失")
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0",
                      "Referer": "https://data.eastmoney.com/"})
    now = _now_iso()
    filled = 0
    for i, (code, name) in enumerate(gap.items(), 1):
        secid = f"1.{code}" if code[:1] in ("6", "9") else f"0.{code}"
        ind_raw = ""
        try:
            r = s.get("http://push2delay.eastmoney.com/api/qt/stock/get",
                      params={"secid": secid, "fields": "f127"}, timeout=8)
            ind_raw = ((r.json().get("data") or {}).get("f127") or "").strip()
        except Exception:
            pass
        if ind_raw and ind_raw != "-":
            ind = _normalize_industry(ind_raw)
            db.upsert("stock_industry", {
                "code": code, "name": name, "main_industry": ind,
                "main_industry_code": ind_raw,
                "industry_chain": json.dumps([ind], ensure_ascii=False),
                "industry_codes": json.dumps([ind_raw], ensure_ascii=False),
                "updated_at": now,
            }, conflict_columns=["code"])
            filled += 1
        if verbose and i % 50 == 0:
            print(f"[industry] 回补 {i}/{len(gap)}（已填 {filled}）")
        time.sleep(0.3)
    print(f"[industry] 回补完成: {filled}/{len(gap)} 只，"
          f"{round(time.time() - t0, 1)}s")
    return {"ok": True, "gap": len(gap), "filled": filled,
            "cost_sec": round(time.time() - t0, 1)}


def _main_site_available() -> bool:
    """
    建映射前置检查：主站 push2 是否可用。
    建映射要发 300-500 个 clist 请求。主站被封（风控）时这些请求会全部走
    delay 端点兜底——高频压 delay 有把它也搞封的风险（届时盘中资金流全挂）。
    所以主站不可用时应推迟建映射（返回 False），调度器窗口内会按小时重试，
    解封后自动补跑。
    """
    import requests
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0",
                          "Referer": "https://data.eastmoney.com/"})
        r = s.get("http://push2.eastmoney.com/api/qt/clist/get",
                  params={"pn": 1, "pz": 2, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                          "fs": "m:90+t:2", "fields": "f12,f14,f3"}, timeout=8)
        return bool(r.json().get("data", {}).get("diff"))
    except Exception:
        return False


def build_map(verbose: bool = True) -> dict:
    """
    全量构建映射表：遍历所有行业板块 → 拉成分股 → 反建 个股→行业 → 落库。

    返回统计：{ok, sectors, stocks, multi_level, cost_sec, error}
    失败时保留旧表（宁可用旧映射，也不能让评分因子全部失效）。
    主站被封时自动推迟（error 带推迟标记，调度器窗口内按小时重试）。
    """
    t0 = time.time()

    # 封禁守卫：主站不可用 → 不硬跑（避免把 delay 端点也压封）
    if not _main_site_available():
        return {"ok": False,
                "error": "东财主站不可用（疑似风控封禁），本轮建映射推迟；解封后自动重试",
                "deferred": True}

    sectors = get_sectors("industry", limit=200)
    if not sectors:
        return {"ok": False, "error": "行业板块列表为空（东财接口异常？）"}

    # 降级检测：东财行业板块代码形如 BK1206。若东财不可用而降级到新浪，
    # 返回的是 new_xxx 这类代码——用它拼 "b:new_xxx" 去东财反查成分股毫无意义
    # （会全部失败，最终写入一张空表）。这里直接中止，宁可不动旧数据。
    bad = [s["code"] for s in sectors if not str(s["code"]).upper().startswith("BK")]
    if bad:
        return {"ok": False,
                "error": f"板块代码非东财格式（疑似降级新浪，样例 {bad[:3]}），中止构建"}

    names = {s["code"]: s["name"] for s in sectors}

    # 1) 拉每个板块的成分股（板块之间留间隔防限流）
    members = {}                      # 板块code -> {股票code: 名称}
    failed = []                       # 拉取失败的板块名
    for i, s in enumerate(sectors):
        m = _sector_members(s["code"])
        if m:
            members[s["code"]] = m
        else:
            failed.append(s["name"])
        if i < len(sectors) - 1:
            time.sleep(_SECTOR_GAP)
        if verbose:
            print(f"[industry] {s['code']} {s['name']:<12} {len(m):>4} 只")
    if not members:
        return {"ok": False, "error": "所有板块成分股均为空"}

    # 2) 反查：股票 -> 所属板块列表（记录板块规模用于判定细分层级）
    stock_sectors = defaultdict(list)  # 股票code -> [(板块code, 板块名, 规模)]
    stock_names = {}
    for scode, m in members.items():
        size = len(m)
        sname = names.get(scode, scode)
        for code, name in m.items():
            stock_sectors[code].append((scode, sname, size))
            if name:
                stock_names[code] = name

    # 3) 按板块规模升序排序 → 最细分层级的板块排在第一个
    mapping = {}
    multi = 0
    for code, items in stock_sectors.items():
        items.sort(key=lambda x: x[2])           # 规模小的（更细分）在前
        main_code, main_name, _ = items[0]
        chain = [it[1] for it in items]          # 从细到粗
        codes = [it[0] for it in items]
        if len(items) > 1:
            multi += 1
        mapping[code] = {
            "code": code,
            "name": stock_names.get(code, ""),
            "main_industry": main_name,
            "main_industry_code": main_code,
            "industry_chain": chain,
            "industry_codes": codes,
        }

    # 4) 落库（★ 必须批量事务：远程云库单条 upsert 0.5s，逐条写 5000+ 条要 40+ 分钟）
    now = _now_iso()
    batch = [{
        "code": code,
        "name": m["name"],
        "main_industry": m["main_industry"],
        "main_industry_code": m["main_industry_code"],
        "industry_chain": json.dumps(m["industry_chain"], ensure_ascii=False),
        "industry_codes": json.dumps(m["industry_codes"], ensure_ascii=False),
        "updated_at": now,
    } for code, m in mapping.items()]
    try:
        written = db.upsert_many("stock_industry", batch, conflict_columns=["code"],
                                 page_size=1000)
    except Exception as e:
        print(f"[industry] 批量写入失败: {e}")
        written = 0

    # 5) 元信息
    for k, v in (("built_at", now), ("sectors", str(len(members))),
                 ("stocks", str(written)), ("multi_level", str(multi))):
        try:
            db.upsert("industry_map_meta", {"key": k, "value": v},
                      conflict_columns=["key"])
        except Exception as e:
            print(f"[industry] 写入元信息 {k} 失败: {e}")

    cost = round(time.time() - t0, 1)
    if verbose:
        print(f"[industry] 完成：{written} 只 / {len(members)} 个板块 / "
              f"{multi} 只多层归属 / 耗时 {cost}s"
              + (f" / 失败板块 {len(failed)} 个 {failed[:5]}" if failed else ""))
    # 部分失败也返回 ok=True（已有数据照常落库），失败板块列出来供排查
    return {"ok": True, "sectors": len(members), "stocks": written,
            "multi_level": multi, "cost_sec": cost, "failed_sectors": failed}


def get_stock_industry(code: str) -> dict:
    """查单只股票的行业归属。无记录返回空 dict。"""
    row = db.fetch_one("SELECT * FROM stock_industry WHERE code = %s", (code,))
    if not row:
        return {}
    return _parse_row(row)


def get_stocks_by_industry(industry: str, limit: int = 500) -> list:
    """查某行业板块的全部个股（按主行业匹配）。"""
    rows = db.fetch("SELECT * FROM stock_industry WHERE main_industry = %s LIMIT %s",
                    (industry, limit))
    return [_parse_row(r) for r in rows]


def get_all_codes() -> list:
    """有行业归属的全部股票代码。"""
    return [r["code"] for r in db.fetch("SELECT code FROM stock_industry")]


def get_stats() -> dict:
    """映射表统计（前端/调度器展示用）。"""
    try:
        meta = {r["key"]: r["value"] for r in db.fetch("SELECT * FROM industry_map_meta")}
        total = db.fetch_one("SELECT COUNT(*) AS n FROM stock_industry")
        # 主行业分布 Top10（板块规模，用于看板块分化时的"板块权重"）
        dist = db.fetch("SELECT main_industry, COUNT(*) AS n FROM stock_industry "
                        "GROUP BY main_industry ORDER BY n DESC LIMIT 10")
        return {
            "built_at": meta.get("built_at", ""),
            "sectors": int(meta.get("sectors") or 0),
            "stocks": int((total or {}).get("n") or 0),
            "multi_level": int(meta.get("multi_level") or 0),
            "top_industries": [{"name": d["main_industry"], "count": d["n"]} for d in dist],
        }
    except Exception as e:
        return {"error": str(e)}


def _parse_row(row: dict) -> dict:
    """数据库行 → 结构化 dict（JSON 字段反序列化，失败降级为空列表）。"""
    try:
        chain = json.loads(row.get("industry_chain") or "[]")
    except (ValueError, TypeError):
        chain = []
    try:
        codes = json.loads(row.get("industry_codes") or "[]")
    except (ValueError, TypeError):
        codes = []
    return {
        "code": row.get("code", ""),
        "name": row.get("name", ""),
        "main_industry": row.get("main_industry", ""),
        "main_industry_code": row.get("main_industry_code", ""),
        "industry_chain": chain,
        "industry_codes": codes,
        "updated_at": row.get("updated_at", ""),
    }


# ================================================================
#  二、板块每日快照（积累历史序列）
# ================================================================

def take_snapshot(date_str: str = None,
                  kinds: tuple = ("industry", "concept")) -> dict:
    """
    记录当日板块快照（涨跌幅 / 涨跌家数 / 资金流 / 领涨股）。

    成本：每个 kind 只需 2 次请求（板块列表 + 资金流），一次请求就返回全部板块，
    所以一天总共约 4 次请求 —— 完全可以每天跑，不会触发东财限流。

    ★ 东财不可用时【跳过】而非降级新浪：新浪板块代码是 new_xxx，与东财 BK1206
      不同源，混进同一张表会让同一个板块出现两个 key，历史序列直接断裂。
      东财封禁通常只持续几小时到几天，少记几天不影响（序列本来就要攒几个月）。

    返回 {date, written, kinds:{行业/概念: 条数}, skipped:[跳过原因]}
    """
    from app.flash.rules import beijing_now
    date = date_str or beijing_now().strftime("%Y-%m-%d")
    out = {"date": date, "written": 0, "kinds": {}, "skipped": []}

    for kind in kinds:
        sectors = get_sectors(kind, limit=500)
        if not sectors:
            out["skipped"].append(f"{kind}: 板块列表为空")
            continue
        if not str(sectors[0]["code"]).upper().startswith("BK"):
            out["skipped"].append(f"{kind}: 非东财数据源（已降级新浪），跳过以保证序列一致")
            continue

        flows = {f["code"]: f for f in (get_sector_flow(kind, limit=500) or [])}
        n = 0
        for s in sectors:
            f = flows.get(s["code"]) or {}
            try:
                db.upsert("sector_daily", {
                    "date": date, "kind": kind, "code": s["code"],
                    "name": s["name"],
                    "change_pct": s.get("change_pct") or 0,
                    "turnover_rate": s.get("turnover_rate") or 0,
                    "up_count": s.get("up_count") or 0,
                    "down_count": s.get("down_count") or 0,
                    "leader": s.get("leader") or "",
                    "leader_change_pct": s.get("leader_change_pct") or 0,
                    "net_inflow": f.get("net_inflow") or 0,
                    "net_inflow_pct": f.get("net_inflow_pct") or 0,
                }, conflict_columns=["date", "kind", "code"])
                n += 1
            except Exception as e:
                print(f"[sector] 快照写入失败 {kind}/{s['code']}: {e}")
        out["kinds"][kind] = n
        out["written"] += n
    return out


def get_sector_history(code: str, days: int = 30) -> list:
    """单板块的历史序列（日期正序，最新在后）。用于算板块动量/资金流趋势。"""
    rows = db.fetch("SELECT * FROM sector_daily WHERE code = %s "
                    "ORDER BY date DESC LIMIT %s", (code, days))
    return list(reversed(rows or []))


def get_date_snapshot(date: str, kind: str = "industry", limit: int = 500) -> list:
    """某交易日的全部板块快照（按涨跌幅降序）。"""
    return db.fetch("SELECT * FROM sector_daily WHERE date = %s AND kind = %s "
                    "ORDER BY change_pct DESC LIMIT %s", (date, kind, limit)) or []


def dispersion(date: str = None, kind: str = "industry") -> dict:
    """
    ★ 板块分化度 —— 这张表最直接的产出，也是 P2"板块分化"因子的现成度量。

    用当日各板块涨跌幅的离散程度衡量：
      分化度高 → 结构性行情（选对板块很关键，选错就亏）
      分化度低 → 系统性行情（普涨普跌，仓位比选板块重要）

    三个互补指标：
      std_dev    涨跌幅标准差（整体离散程度）
      max_spread 最强与最弱板块的差距（极端分化程度）
      up_ratio   上涨板块占比（配合看：是普涨还是少数板块拉动）
    """
    import statistics
    from app.flash.rules import beijing_now
    date = date or beijing_now().strftime("%Y-%m-%d")
    rows = get_date_snapshot(date, kind)
    if len(rows) < 5:
        return {"date": date, "kind": kind, "error": "当日快照不足 5 个板块"}
    chg = [float(r["change_pct"] or 0) for r in rows]
    up = sum(1 for c in chg if c > 0)
    down = sum(1 for c in chg if c < 0)
    return {
        "date": date, "kind": kind,
        "sector_count": len(rows),
        "std_dev": round(statistics.pstdev(chg), 3),
        "max_spread": round(max(chg) - min(chg), 3),
        "mean_change": round(sum(chg) / len(chg), 3),
        "up_sectors": up, "down_sectors": down,
        "up_ratio": round(up / len(chg), 3),
        "top": [{"name": r["name"], "change_pct": r["change_pct"]} for r in rows[:5]],
        "bottom": [{"name": r["name"], "change_pct": r["change_pct"]} for r in rows[-5:]],
    }


def snapshot_stats() -> dict:
    """快照表概况：已积累多少天、多少行、最新日期。"""
    try:
        days = db.fetch("SELECT DISTINCT date FROM sector_daily ORDER BY date DESC LIMIT 10")
        total = db.fetch_one("SELECT COUNT(*) AS n FROM sector_daily")
        by_kind = db.fetch("SELECT kind, COUNT(DISTINCT date) AS d, COUNT(*) AS n "
                           "FROM sector_daily GROUP BY kind")
        return {
            "latest_dates": [d["date"] for d in (days or [])],
            "days": len(days or []),
            "total_rows": int((total or {}).get("n") or 0),
            "by_kind": [{"kind": r["kind"], "days": r["d"], "rows": r["n"]}
                        for r in (by_kind or [])],
        }
    except Exception as e:
        return {"error": str(e)}
