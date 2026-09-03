"""
矛盾扫描引擎核心：L2 行为背离纯规则扫描器

当前实现（MVP）：
  1. index_vs_breadth   指数红盘 vs 个股普跌/宽度恶化
  2. sector_narrative_vs_flow  板块上涨 vs 资金净流出
  3. price_vs_volume    指数/个股价格新高 vs 成交量萎缩
  4. northbound_vs_index 指数红盘 vs 北向大幅净流出

未来扩展：
  - L1：财经日历 surprise_score、快讯事件定价
  - L3：财报现金流/应收账款断层
  - L2：散户情绪 vs 融资余额、政策表态 vs 流动性投放
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional


# ── 工具 ──

def _today(date: Optional[str] = None) -> str:
    if date:
        return date
    from app.flash.rules import beijing_now
    return beijing_now().strftime("%Y-%m-%d")


def _load_market_snapshot() -> Dict:
    """加载收盘行情快照；若不存在，回退到内存缓存。"""
    try:
        from app.flash import store
        snap = store.load_market_snapshot()
        if snap and snap.get("stocks"):
            return snap
    except Exception as e:
        print(f"[contradiction] 读取收盘快照失败: {e}")
    try:
        from app.tencent import _cache
        stocks = _cache.get("stocks", {})
        if stocks:
            return {"stocks": stocks, "saved_at": None}
    except Exception:
        pass
    return {}


def _load_index_quotes() -> List[Dict]:
    """加载主要大盘指数实时行情。"""
    try:
        from app.tencent import get_index
        indices = []
        for symbol, name in [("000001", "上证指数"), ("399001", "深证成指"),
                             ("399006", "创业板指"), ("000300", "沪深300"),
                             ("000905", "中证500"), ("000688", "科创50")]:
            q = get_index(symbol)
            if q and q.get("price"):
                indices.append({"code": symbol, "name": name,
                                  "change_pct": q.get("change_pct", 0.0),
                                  "price": q.get("price", 0.0)})
        return indices
    except Exception as e:
        print(f"[contradiction] 读取指数行情失败: {e}")
        return []


def _load_sector_data() -> Dict:
    """同时加载板块列表与板块资金流。"""
    try:
        from app.eastmoney import get_sectors, get_sector_flow
        sectors = get_sectors("industry", limit=200)
        flows = get_sector_flow("industry", limit=200)
        return {"sectors": sectors or [], "flows": flows or []}
    except Exception as e:
        print(f"[contradiction] 读取板块数据失败: {e}")
        return {"sectors": [], "flows": []}


def _load_northbound() -> Optional[Dict]:
    """加载北向资金。"""
    try:
        from app.eastmoney import get_northbound
        return get_northbound()
    except Exception as e:
        print(f"[contradiction] 读取北向资金失败: {e}")
        return None


def _load_index_kline(symbol: str = "000001", count: int = 30) -> List[Dict]:
    """加载指数日 K 线。"""
    try:
        from app.tencent import get_kline
        rows = get_kline(symbol, period="day", count=count)
        return rows or []
    except Exception as e:
        print(f"[contradiction] 读取指数 K 线失败: {e}")
        return []


def _fmt_money(v) -> str:
    try:
        return f"{float(v) / 1e8:.2f} 亿"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(v) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "-"


# ── L2 扫描器 ──

def scan_index_vs_breadth(date: Optional[str] = None) -> Optional[Dict]:
    """指数红盘 vs 个股宽度恶化。

    触发条件：任一主要指数涨幅>0，但样本平均涨幅<0 或 涨跌比<0.5。
    """
    snap = _load_market_snapshot()
    stocks = snap.get("stocks") or {}
    indices = _load_index_quotes()

    if not stocks or not indices:
        return None

    up = down = flat = 0
    chgs = []
    for s in stocks.values():
        if not isinstance(s, dict):
            continue
        c = s.get("change_pct")
        if c is None:
            c = s.get("pct_chg") or s.get("pct")
        if c is None:
            continue
        try:
            c = float(c)
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

    avg = sum(chgs) / len(chgs)
    ratio = up / max(down, 1)
    max_index_pct = max([i.get("change_pct", 0) for i in indices] or [0])
    red_indices = [i for i in indices if i.get("change_pct", 0) > 0]

    if max_index_pct <= 0:
        return None
    if avg >= 0 and ratio >= 0.5:
        return None

    # 判定 severity
    if avg < -0.5 or ratio < 0.3:
        severity = "severe"
    elif avg < -0.2 or ratio < 0.4:
        severity = "obvious"
    else:
        severity = "minor"

    index_str = "、".join(f"{i['name']}{i['change_pct']:+.2f}%" for i in red_indices[:3])
    return {
        "level": "L2",
        "type": "index_vs_breadth",
        "severity": severity,
        "title": "指数红盘，个股失血",
        "summary": (f"今日 {index_str} 收红，但有效交易样本 {len(chgs)} 只中 "
                    f"涨 {up} / 跌 {down} / 平 {flat}，涨跌比 {ratio:.2f}，平均 {avg:+.2f}%。"
                    f"权重护盘特征明显，小票承压。"),
        "evidence": {
            "narrative": "主要指数收红，表面市场企稳",
            "actual": "个股跌多涨少，平均涨幅为负",
            "metrics": {
                "sample_size": len(chgs), "up": up, "down": down, "flat": flat,
                "ratio": round(ratio, 2), "avg_pct": round(avg, 2),
                "red_indices": [{"name": i["name"], "change_pct": i["change_pct"]}
                                for i in red_indices],
            },
        },
        "signal": "权重护盘不能代表赚钱效应，磨底期每次反弹都是减仓窗口，而非加仓信号。",
    }


def scan_sector_narrative_vs_flow(date: Optional[str] = None) -> Optional[Dict]:
    """板块叙事 vs 资金流向背离。

    触发条件：涨幅榜前 20 的板块中，存在主力净流入为负（涨但资金流出）。
    或跌幅榜前 20 中，存在主力净流入为正（跌但资金流入）。
    """
    data = _load_sector_data()
    sectors = data.get("sectors") or []
    flows = data.get("flows") or []
    if not sectors or not flows:
        return None

    # 按名称建立资金流索引
    flow_map = {f.get("name"): f for f in flows if f.get("name")}

    # 按涨幅排序取前 20
    top_up = sorted(sectors, key=lambda x: x.get("change_pct") or -100, reverse=True)[:20]
    top_down = sorted(sectors, key=lambda x: x.get("change_pct") or 100)[:20]

    up_but_outflow = []
    down_but_inflow = []
    for s in top_up:
        name = s.get("name")
        f = flow_map.get(name)
        if not f:
            continue
        net = f.get("net_inflow") or 0
        if net < 0:
            up_but_outflow.append({
                "name": name,
                "change_pct": s.get("change_pct"),
                "net_inflow": net,
            })
    for s in top_down:
        name = s.get("name")
        f = flow_map.get(name)
        if not f:
            continue
        net = f.get("net_inflow") or 0
        if net > 0:
            down_but_inflow.append({
                "name": name,
                "change_pct": s.get("change_pct"),
                "net_inflow": net,
            })

    if not up_but_outflow and not down_but_inflow:
        return None

    n_up = len(up_but_outflow)
    n_down = len(down_but_inflow)
    total_out = sum(f["net_inflow"] for f in up_but_outflow)
    total_in = sum(f["net_inflow"] for f in down_but_inflow)

    # severity：3 个以上 severe，1-2 个 obvious
    if n_up >= 3 or abs(total_out) >= 5e8:
        severity = "severe"
    else:
        severity = "obvious"

    parts = []
    if n_up:
        parts.append(f"{n_up} 个上涨板块出现主力净流出（合计 {total_out/1e8:+.2f} 亿）")
    if n_down:
        parts.append(f"{n_down} 个下跌板块出现主力净流入（合计 {total_in/1e8:+.2f} 亿）")

    return {
        "level": "L2",
        "type": "sector_narrative_vs_flow",
        "severity": severity,
        "title": "板块叙事与资金流向背离",
        "summary": "；".join(parts) + "。资金行为不支持表面叙事，需警惕拉高出货或弱势吸筹。",
        "evidence": {
            "narrative": "某些板块涨幅靠前，呈现强势叙事",
            "actual": "这些板块主力资金净流出，行为端不认可",
            "metrics": {
                "up_but_outflow_count": n_up,
                "up_but_outflow_total": round(total_out, 2),
                "up_but_outflow_samples": [
                    {"name": x["name"], "change_pct": x["change_pct"],
                     "net_outflow": round(x["net_inflow"], 2)}
                    for x in up_but_outflow[:5]
                ],
                "down_but_inflow_count": n_down,
                "down_but_inflow_total": round(total_in, 2),
                "down_but_inflow_samples": [
                    {"name": x["name"], "change_pct": x["change_pct"],
                     "net_inflow": round(x["net_inflow"], 2)}
                    for x in down_but_inflow[:5]
                ],
            },
        },
        "signal": "涨但资金流出 → 主力出货；跌但资金流入 → 左侧吸筹。短线不宜追高涨幅榜。",
    }


def scan_price_vs_volume(date: Optional[str] = None) -> Optional[Dict]:
    """价格新高 vs 成交量萎缩（动能衰竭）。

    当前实现大盘指数层面的量价背离；个股层面未来可扩展。
    """
    klines = _load_index_kline("000001", count=35)
    if not klines or len(klines) < 21:
        return None

    closes = [k["close"] for k in klines]
    volumes = [k.get("volume") or 0 for k in klines]
    latest_close = closes[-1]
    max_20 = max(closes[-20:])
    prior_volumes = volumes[-21:-1]          # 不含当日的最近 20 根成交量
    vol_20_avg = sum(prior_volumes) / max(1, len(prior_volumes))
    latest_vol = volumes[-1]

    if latest_close <= max_20:
        return None
    if vol_20_avg <= 0 or latest_vol <= 0:
        return None

    vol_ratio = latest_vol / vol_20_avg
    if vol_ratio >= 0.9:
        return None

    if vol_ratio < 0.7:
        severity = "severe"
    elif vol_ratio < 0.8:
        severity = "obvious"
    else:
        severity = "minor"

    return {
        "level": "L2",
        "type": "price_vs_volume",
        "severity": severity,
        "title": "指数新高但量能不足",
        "summary": (f"上证指数收于 {latest_close:.2f}，创近 20 日新高，"
                    f"但今日成交量 {latest_vol/1e8:.2f} 亿仅为 20 日均量 {vol_20_avg/1e8:.2f} 亿的 "
                    f"{vol_ratio*100:.1f}%。价升量减 = 动能衰竭。"),
        "evidence": {
            "narrative": "指数创阶段新高，表面多头强势",
            "actual": "成交量显著萎缩，上涨缺乏资金跟进",
            "metrics": {
                "index": "上证指数", "latest_close": round(latest_close, 2),
                "high_20d": round(max_20, 2), "latest_vol": round(latest_vol, 2),
                "vol_20d_avg": round(vol_20_avg, 2), "vol_ratio": round(vol_ratio, 3),
            },
        },
        "signal": "缩量新高多为假突破，若后续放量滞涨需果断减仓。",
    }


def scan_northbound_vs_index(date: Optional[str] = None) -> Optional[Dict]:
    """指数红盘 vs 北向大幅净流出。

    触发条件：任一主要指数涨 > 0，且北向净流出 > 5 亿。
    """
    indices = _load_index_quotes()
    nb = _load_northbound()
    if not indices:
        return None
    if not nb or nb.get("total_net") is None:
        return None

    total_net = nb.get("total_net", 0)
    if total_net >= 0:
        return None

    red_indices = [i for i in indices if i.get("change_pct", 0) > 0]
    if not red_indices:
        return None

    outflow = abs(total_net)
    if outflow >= 1e9:
        severity = "severe"
    elif outflow >= 5e8:
        severity = "obvious"
    else:
        return None  # 小于 5 亿不触发

    index_str = "、".join(f"{i['name']}{i['change_pct']:+.2f}%" for i in red_indices[:3])
    return {
        "level": "L2",
        "type": "northbound_vs_index",
        "severity": severity,
        "title": "指数红盘，北向离场",
        "summary": (f"今日 {index_str} 收红，但北向资金净流出 {total_net/1e8:.2f} 亿。"
                    f"内资拉指数、外资出货，结构承压。"),
        "evidence": {
            "narrative": "主要指数收红，市场情绪偏暖",
            "actual": "北向资金大幅净流出，外资实际减仓",
            "metrics": {
                "northbound_net": round(total_net, 2),
                "northbound_sh": round(nb.get("sh_net", 0), 2),
                "northbound_sz": round(nb.get("sz_net", 0), 2),
                "red_indices": [{"name": i["name"], "change_pct": i["change_pct"]}
                                for i in red_indices],
            },
        },
        "signal": "外资离场而指数红盘，多为权重股护盘；短期不宜追高。",
    }


# ── L1 扫描器 ──

def scan_calendar_surprise(date: Optional[str] = None) -> Optional[Dict]:
    """
    L1 预期差：财经日历 actual vs consensus。

    触发条件：高星级经济指标中，actual 与 consensus 偏离 |surprise_score| > 3。
    surprise_score = (actual - consensus) / |consensus| * 10
    """
    try:
        from app.flash import calendar
        items = calendar.get_items()
    except Exception as e:
        print(f"[contradiction] 读取财经日历失败: {e}")
        return None

    if not items:
        return None

    target = _today(date)
    surprises = []
    for item in items:
        if item.get("kind") != "data":
            continue
        # 只关注今日发布的数据
        if item.get("date") != target:
            continue
        consensus = item.get("consensus")
        actual = item.get("actual")
        if consensus is None or actual is None:
            continue
        try:
            consensus = float(consensus)
            actual = float(actual)
        except (TypeError, ValueError):
            continue
        if abs(consensus) < 1e-9:
            continue
        score = (actual - consensus) / abs(consensus) * 10
        if abs(score) <= 3:
            continue
        # 星级：越高越重要
        star = item.get("star") or 0
        surprises.append({
            "title": item.get("title", ""),
            "country": item.get("country", ""),
            "period": item.get("period", ""),
            "unit": item.get("unit", ""),
            "consensus": consensus,
            "actual": actual,
            "prev": item.get("prev"),
            "star": star,
            "surprise_score": round(score, 2),
        })

    if not surprises:
        return None

    # 按 surprise_score 绝对值排序，取前三
    surprises.sort(key=lambda x: abs(x["surprise_score"]), reverse=True)
    top = surprises[:3]
    max_score = abs(top[0]["surprise_score"])
    if max_score >= 8:
        severity = "severe"
    elif max_score >= 5:
        severity = "obvious"
    else:
        severity = "minor"

    lines = []
    for s in top:
        sign = "高于" if s["surprise_score"] > 0 else "低于"
        lines.append(
            f"{s['country']} {s['title']}（{s['period']}）：实际 {s['actual']}{s['unit']}，"
            f"预期 {s['consensus']}{s['unit']}，{sign}预期 {abs(s['surprise_score']):.1f} 个标准差")
    return {
        "level": "L1",
        "type": "calendar_surprise",
        "severity": severity,
        "title": "财经数据预期差",
        "summary": "今日发布的重要经济指标出现显著偏离：" + "；".join(lines),
        "evidence": {
            "narrative": "市场预期某一经济数据落在 consensus 附近",
            "actual": "实际值与 consensus 出现显著偏离",
            "metrics": {
                "surprise_count": len(surprises),
                "top_events": top,
            },
        },
        "signal": "数据爆冷/超预期会驱动 1-3 天的价格修正，需结合 A 股关联资产判断定价程度。",
    }


def scan_today_calendar_focus(date: Optional[str] = None) -> Optional[Dict]:
    """
    L1 盘前：今日高重要性经济事件关注。

    触发条件：今日有待发布的高星级（>=3星）经济指标，且 consensus 与市场当前叙事可能冲突。
    这是预期差的前置扫描——在 actual 公布前标记风险/机会点。
    """
    try:
        from app.flash import calendar
        items = calendar.get_items()
    except Exception as e:
        print(f"[contradiction] 读取财经日历失败: {e}")
        return None

    if not items:
        return None

    target = _today(date)
    focus = []
    for item in items:
        if item.get("kind") != "data":
            continue
        if item.get("date") != target:
            continue
        star = item.get("star") or 0
        if star < 3:
            continue
        consensus = item.get("consensus")
        prev = item.get("prev")
        if consensus is None:
            continue
        focus.append({
            "title": item.get("title", ""),
            "country": item.get("country", ""),
            "period": item.get("period", ""),
            "unit": item.get("unit", ""),
            "star": star,
            "consensus": consensus,
            "prev": prev,
            "time": item.get("time", "")[11:16] if item.get("time") else "",
        })

    if not focus:
        return None

    focus.sort(key=lambda x: (-x["star"], x.get("time") or ""))
    top = focus[:5]
    max_star = top[0]["star"]
    if max_star >= 4:
        severity = "severe"
    else:
        severity = "obvious"

    lines = []
    for f in top:
        lines.append(
            f"{f['time']} {f['country']} {f['title']}（{f['period']}）"
            f"预期 {f['consensus']}{f['unit']}，前值 {f['prev']}{f['unit']}，{'★' * f['star']}"
        )
    return {
        "level": "L1",
        "type": "today_calendar_focus",
        "severity": severity,
        "title": "今日高重要性经济事件",
        "summary": "今日需关注的重要数据发布：" + "；".join(lines),
        "evidence": {
            "narrative": "市场已对 consensus 形成一致预期",
            "actual": "数据尚未公布，但高星级事件极易产生预期差",
            "metrics": {
                "focus_count": len(focus),
                "top_events": top,
            },
        },
        "signal": "实际值偏离 consensus 时，会通过跨资产传导影响 A 股；盘前做好情景预案。",
    }


# 扫描器注册表
L1_SCANNERS = [scan_calendar_surprise, scan_today_calendar_focus]

L2_SCANNERS = [
    scan_index_vs_breadth,
    scan_sector_narrative_vs_flow,
    scan_price_vs_volume,
    scan_northbound_vs_index,
]

ALL_SCANNERS = L1_SCANNERS + L2_SCANNERS


def scan_all(date: Optional[str] = None, level: Optional[str] = None) -> List[Dict]:
    """运行全部扫描器，返回矛盾列表。

    level: L1 / L2 / None（全部）
    """
    target = _today(date)
    scanners = ALL_SCANNERS
    if level == "L1":
        scanners = L1_SCANNERS
    elif level == "L2":
        scanners = L2_SCANNERS

    results = []
    for fn in scanners:
        try:
            item = fn(date=target)
            if item:
                results.append(item)
        except Exception as e:
            print(f"[contradiction] 扫描器 {fn.__name__} 异常: {e}")
    # 按 severity 严重度排序
    severity_order = {"severe": 0, "obvious": 1, "minor": 2}
    results.sort(key=lambda x: (severity_order.get(x.get("severity"), 3), x.get("level")))
    return results


if __name__ == "__main__":
    for c in scan_all():
        print(c["severity"], c["type"], c["title"])
        print(" ", c["summary"][:100])
