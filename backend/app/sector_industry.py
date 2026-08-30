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
# 反爬实测：连续几百次请求会触发东财断连甚至临时封 IP（封禁持续数十分钟）。
# 建表是一次性任务，慢一点没关系，但不能把 IP 搞封 —— 否则盘中板块资金流也跟着挂。
_SECTOR_GAP = 0.3


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


def build_map(verbose: bool = True) -> dict:
    """
    全量构建映射表：遍历所有行业板块 → 拉成分股 → 反建 个股→行业 → 落库。

    返回统计：{ok, sectors, stocks, multi_level, cost_sec, error}
    失败时保留旧表（宁可用旧映射，也不能让评分因子全部失效）。
    """
    t0 = time.time()

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
