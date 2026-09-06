# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】两融余额明细 + A股情绪温度计（金十 mp-api）
================================================================================

数据源（金十 mp-api dynamic-data，认证同 cb_gold）：
  tb_name=_vir_96   两融明细（日度，亿元）
      sh/sz_fund_bal      沪/深融资余额
      sh/sz_stock_bal     沪/深融券余额
      sh/sz_fund_stock_bal 两融合计（融资+融券）
      sh/sz_fund_buy      融资买入额
      sh/sz_stock_sell    融券卖出额
  tb_name=_vir_135  A股情绪温度计（日度，score 0-100）
      score               综合温度：0-30 寒冷 / 30-70 平稳 / 70-100 炎热
      bf_index            巴菲特指标（总市值/GDP）
      yield_sp            股债利差    hs300_peg 大盘市盈率
      be_ratio            存款市值比  turnover  日成交额（亿）
      turnover_ratio      换手率比    tm2_ratio 两融余额市值比%
      investor            新开户数    ipo       新募股资规模
      sif_basis           股指期货基差率
      *_score             各子项的 0-100 得分（官方权重：宏观估值35% +
                          流动性情绪40% + 机构衍生品25%）

用途（先知雷达数据层补充的"融资明细 + 散户情绪"两项）：
  - 日报"两融与情绪"小节
  - 矛盾扫描 L2 散户维度背离（情绪热但杠杆资金撤 → 拉高出货结构）
缓存：日度数据，24h；拉取失败返回 None（调用方静默降级）。
================================================================================
"""

import os
import time
from datetime import datetime, timedelta

import requests

_URL = "https://mp-api.jin10.com/api/dynamic-data/child"
_HEADERS = {
    "x-app-id": os.environ.get("JIN10_MP_APP_ID", "fiXF2nOnDycGutVA"),
    "x-version": "1.0",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"),
    "Referer": "https://www.jin10.com/",
    "Origin": "https://www.jin10.com",
    "accept": "application/json, text/plain, */*",
}

_TTL = 86400
_margin_cache = {"data": None, "ts": 0.0}
_sentiment_cache = {"data": None, "ts": 0.0}

# 情绪子项中文（报告展示用）
SENTIMENT_SUB_CN = {
    "bf_index": "巴菲特指标", "yield_sp": "股债利差", "be_ratio": "存款市值比",
    "hs300_peg": "大盘市盈率", "turnover_ratio": "换手率比", "turnover": "日成交额",
    "rd_ratio": "成交额M2比", "investor": "新开户数", "tm2_ratio": "两融余额市值比",
    "sif_basis": "股指期货基差率", "ipo": "新募股资规模",
}


def _fetch(tb_name: str, limit: int = 100) -> list:
    r = requests.get(_URL, params={
        "tb_name": tb_name, "order": "date,desc", "page": 1, "limit": limit,
    }, headers=_HEADERS, timeout=20)
    if r.status_code != 200:
        print(f"[jin10] {tb_name} HTTP {r.status_code}")
        return []
    return r.json().get("data") or []


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def get_margin(days: int = 10) -> dict | None:
    """
    两融明细（亿元，沪+深合计）。★ 数据坑：深市字段比沪市晚约一天出
    （最新行 sz_* 可能为 null）——合计/对比只用沪深齐全的行，
    最新行不齐全时降级用最近一期完整行并标注。
    返回：
      {date, fund_bal(两融合计), fund_bal_chg5(较5个完整交易日前净变化),
       fund_buy(当日融资买入额), short_bal(融券余额), partial(最新行是否缺深市),
       hist: [{date, fund_bal, fund_buy} × 完整行]}
    """
    now = time.time()
    if _margin_cache["data"] and now - _margin_cache["ts"] < _TTL:
        return _margin_cache["data"]
    try:
        rows = _fetch("_vir_96", limit=max(days + 5, 15))
        if not rows:
            return None

        def _complete(r) -> bool:
            return (r.get("sh_fund_stock_bal") is not None
                    and r.get("sz_fund_stock_bal") is not None)

        def _total(r) -> tuple:
            bal = (_f(r.get("sh_fund_stock_bal")) or 0) + \
                  (_f(r.get("sz_fund_stock_bal")) or 0)
            buy = (_f(r.get("sh_fund_buy")) or 0) + (_f(r.get("sz_fund_buy")) or 0)
            return bal, buy

        partial_latest = not _complete(rows[0])
        complete = [r for r in rows if _complete(r)]
        if not complete:
            return None
        latest = complete[0]
        fund_bal, fund_buy = _total(latest)
        short_bal = (_f(latest.get("sh_stock_bal")) or 0) + \
                    (_f(latest.get("sz_stock_bal")) or 0)

        hist = [{"date": r.get("date"), "fund_bal": round(_total(r)[0], 1),
                 "fund_buy": round(_total(r)[1], 1)} for r in complete[:days]]

        chg5 = None
        if len(hist) >= 6:
            chg5 = round(fund_bal - hist[5]["fund_bal"], 1)

        data = {"date": latest.get("date"), "fund_bal": round(fund_bal, 1),
                "fund_bal_chg5": chg5, "fund_buy": round(fund_buy, 1),
                "short_bal": round(short_bal, 1), "partial_latest": partial_latest,
                "hist": hist}
        _margin_cache.update(data=data, ts=now)
        return data
    except Exception as e:
        print(f"[jin10] 两融明细拉取失败: {e}")
    return None


def get_sentiment(days: int = 10) -> dict | None:
    """
    A股情绪温度计。返回：
      {date, score(0-100), zone(寒冷/平稳/炎热), subs: [{key, name, value, score}]
       （按得分降序——过热/过冷子项一目了然）, hist: [{date, score}]}
    """
    now = time.time()
    if _sentiment_cache["data"] and now - _sentiment_cache["ts"] < _TTL:
        return _sentiment_cache["data"]
    try:
        rows = _fetch("_vir_135", limit=max(days + 2, 12))
        if not rows:
            return None
        latest = rows[0]
        score = _f(latest.get("score"))
        if score is None:
            return None
        zone = "炎热" if score >= 70 else ("寒冷" if score <= 30 else "平稳")

        subs = []
        for key, cn in SENTIMENT_SUB_CN.items():
            v = _f(latest.get(key))
            s = _f(latest.get(f"{key}_score"))
            if s is not None:
                subs.append({"key": key, "name": cn, "value": v, "score": round(s, 1)})
        subs.sort(key=lambda x: -x["score"])

        hist = [{"date": r.get("date"), "score": _f(r.get("score"))}
                for r in rows[:days] if _f(r.get("score")) is not None]

        data = {"date": latest.get("date"), "score": round(score, 1), "zone": zone,
                "subs": subs, "hist": hist}
        _sentiment_cache.update(data=data, ts=now)
        return data
    except Exception as e:
        print(f"[jin10] 情绪温度计拉取失败: {e}")
    return None


def margin_line() -> str:
    """两融一行（LLM prompt/日报复用）；失败返回空串。"""
    d = get_margin()
    if not d:
        return ""
    s = f"两融余额({d['date']}): {d['fund_bal']:.0f}亿"
    if d.get("partial_latest"):
        s += "（最新日深市未出，取最近完整日）"
    if d.get("fund_bal_chg5") is not None:
        chg = d["fund_bal_chg5"]
        s += f"，5日净{'增' if chg >= 0 else '减'} {abs(chg):.0f}亿"
    s += f"，当日融资买入 {d['fund_buy']:.0f}亿"
    return s


def sentiment_line() -> str:
    """情绪温度计一行；失败返回空串。"""
    d = get_sentiment()
    if not d:
        return ""
    s = f"情绪温度计({d['date']}): {d['score']}分/{d['zone']}区"
    hot = [x for x in d["subs"] if x["score"] >= 80][:2]
    cold = [x for x in d["subs"] if x["score"] <= 20][:2]
    if hot:
        s += "，过热：" + "、".join(f"{x['name']}({x['score']:.0f})" for x in hot)
    if cold:
        s += "，过冷：" + "、".join(f"{x['name']}({x['score']:.0f})" for x in cold)
    return s
