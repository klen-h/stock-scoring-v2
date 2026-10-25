# -*- coding: utf-8 -*-
"""李大霄视角卡（app/lidaxiao_views.py）的业务语义测试。

背景（2026-10-25 接入，.codebuddy/plans/李大霄视角卡片_xiaopage解读接入__3f8a2c61.md）：
  xiaopage 的 LLM 解读 JSON 存在两类实证漂移（fresh 484 条全量普查），本文件锚定消费侧口径：
  1. direction 枚举漂移（33 种取值）：主体 看跌/看涨/中性偏空/中性偏多/中性 之外散布 28 种
     变体（"看跌（对美股）""看多""看涨/看跌（分化）"…）⇒ 必须包含式归一，不能 == 比较；
  2. 18 条缺 _id（含 2026-09-24 近期条）⇒ 主键合成兜底；缺 date 才跳过（排序/lag 硬依赖）；
  3. 情绪六级边界（口径移植自 xiaopage src/utils/sentiment.ts，两站同分同色）；
  4. 推送端点：共享密钥 403 / 坏条目逐条 skip 不炸整批 / entry_id 幂等。
DB 走 conftest 强制的 SQLite（绝不连线上 Supabase）。
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import lidaxiao_views as lv

_BJ = timezone(timedelta(hours=8))


def _today_str():
    return datetime.now(_BJ).strftime("%Y-%m-%d %H:%M")


def _entry(**kw):
    e = {
        "_id": "lishaoxiao_20261024 21:02", "bvid": "BV1NgHK6jEga",
        "title": "平衡引资和融资", "date": "2026-10-24 21:02",
        "sentiment_score": 40, "disclaimer_level": 4,
        "summary": "摘要", "tags": ["李大霄", "A股"],
        "structured": {"core_views": ["观点1", "观点2"], "sectors": ["银行", "红利"],
                       "operation_advice": "弦外之音", "risk_tips": ["风险"]},
        "deep_analysis": {"final_conclusion": "定调：短期防御为主",
                          "strategy": "大块字段不应入库"},
        "logic_factors": [
            {"factor_name": "弱因子", "weight": 0.1, "direction": "中性"},
            {"factor_name": "强空因子", "weight": 0.9, "direction": "看跌（对美股）"},
            {"factor_name": "强多因子", "weight": 0.8, "direction": "看涨"},
            {"factor_name": "第四因子", "weight": 0.7, "direction": "看跌"},
        ],
        "hit_status": "pending",
    }
    e.update(kw)
    return e


def _clear():
    lv.ensure_table()
    lv.db.execute(f"DELETE FROM {lv._TABLE}")


# ---------- ① direction 归一（33 种真实取值的代表集，2026-10-25 普查） ----------

@pytest.mark.parametrize("raw,expected", [
    ("看跌", "bear"), ("看涨", "bull"), ("中性", "neutral"),
    ("中性偏空", "bear"), ("中性偏多", "bull"),
    ("看跌（对美股）", "bear"),                  # 括注市场限定（普查出现 6 次）
    ("看多", "bull"),                            # ★ 不带"涨"字的变体（普查出现 2 次）
    ("看空", "bear"),
    ("看涨/看跌（分化）", "mixed"),
    ("看跌（对高估股）/看涨（对防御股）", "mixed"),
    ("看跌（对“小灯”），看涨（对“老登”）", "mixed"),
    ("中性偏多（短期）", "bull"),
    ("中性偏空（对美股）", "bear"),
    ("看跌（对中概股及相关板块）", "bear"),
    ("", "neutral"), (None, "neutral"),
])
def test_norm_direction_census(raw, expected):
    assert lv.norm_direction(raw) == expected


# ---------- ② 情绪六级边界（xiaopage sentiment.ts 同口径） ----------

@pytest.mark.parametrize("score,expected", [
    (0, "极度恐慌"), (15, "极度恐慌"), (16, "悲观"), (35, "悲观"),
    (36, "谨慎偏空"), (45, "谨慎偏空"), (46, "谨慎偏多"), (55, "谨慎偏多"),
    (56, "乐观"), (75, "乐观"), (76, "极度狂热"), (100, "极度狂热"),
    (40, "谨慎偏空"),
])
def test_sentiment_level_boundaries(score, expected):
    assert lv.sentiment_level(score) == expected


def test_sentiment_level_invalid():
    assert lv.sentiment_level(None) is None
    assert lv.sentiment_level("abc") is None
    assert lv.sentiment_level("40") == "谨慎偏空"     # 字符串数字也要能归一（上游无类型约束）


# ---------- ③ 主键合成（18 条缺 _id 的实证兜底） ----------

def test_entry_key_fallback_chain():
    assert lv._entry_key({"_id": "x1"}) == "x1"
    assert lv._entry_key({"bvid": "BVabc"}) == "BVabc"
    assert lv._entry_key({"date": "2026-09-24 11:29", "title": "标题"}) \
        == "auto:2026-09-24 11:29|标题"
    assert lv._entry_key({"title": "无日期无id"}) is None


# ---------- ④ 防御解析（_slim） ----------

def test_slim_full_entry():
    row = lv._slim(_entry())
    assert row["entry_id"] == "lishaoxiao_20261024 21:02"
    assert row["sentiment_level"] == "谨慎偏空"
    assert row["final_conclusion"] == "定调：短期防御为主"
    assert "strategy" not in row                     # deep_analysis 其余字段不入库
    fs = json.loads(row["factors_json"])             # 因子按 weight 降序 Top3 + 归一
    assert [f["factor_name"] for f in fs] == ["强空因子", "强多因子", "第四因子"]
    assert fs[0]["direction_norm"] == "bear"
    assert json.loads(row["sectors"]) == ["银行", "红利"]


def test_slim_defensive_legacy():
    # 缺 _id / bvid / logic_factors / structured 的 legacy 形态：不炸、合成主键、字段置空
    row = lv._slim({"date": "2026-09-24 11:29", "title": "旧条目",
                    "sentiment_score": "60", "summary": "s"})
    assert row["entry_id"].startswith("auto:")
    assert row["bvid"] is None
    assert row["factors_json"] is None
    assert row["sentiment_level"] == "乐观"


def test_slim_skip_without_date():
    assert lv._slim({"_id": "x", "title": "无日期"}) is None   # date 是排序/lag 硬依赖


# ---------- ⑤ 入库幂等 / 读取口径（SQLite 全链路） ----------

def test_ingest_skip_and_idempotent():
    _clear()
    r = lv.ingest_entries([_entry(),
                           _entry(_id="lishaoxiao_20261023 20:33",
                                  date="2026-10-23 20:33"),
                           {"no date": 1}])
    assert r["stored"] == 2 and r["skipped"] == 1    # 坏条目逐条 skip 不炸整批
    r2 = lv.ingest_entries([_entry(),
                            _entry(_id="lishaoxiao_20261023 20:33",
                                   date="2026-10-23 20:33")])
    assert r2["stored"] == 2                         # 同批重推 = 覆盖更新
    n = lv.db.fetch_one(f"SELECT COUNT(*) AS n FROM {lv._TABLE}")["n"]
    assert n == 2                                    # 幂等：行数不涨


def test_load_latest_trend_and_stale():
    _clear()
    old = (datetime.now(_BJ) - timedelta(days=20)).strftime("%Y-%m-%d %H:%M")
    lv.ingest_entries([
        _entry(_id="new", date=_today_str(), sentiment_score=50),
        _entry(_id="old", date=old, sentiment_score=80, bvid=""),
    ])
    d = lv.load(limit=3)
    assert d["ok"] and d["latest"]["entry_id"] == "new"          # date DESC 取最新
    assert d["latest"]["video_url"].startswith("https://www.bilibili.com/video/")
    assert d["lag_days"] == 0 and d["stale"] is False
    assert len(d["trend"]) == 1 and d["trend"][0]["score"] == 80  # 轨迹=最新之外的前 N
    assert "非本人原文" in d["note"]
    # stale 场景：只留一条 20 天前的旧解读
    _clear()
    lv.ingest_entries([_entry(_id="only-old", date=old)])
    d2 = lv.load()
    assert d2["lag_days"] == 20 and d2["stale"] is True


def test_load_empty_table():
    _clear()
    d = lv.load()
    assert d["ok"] and d["latest"] is None and d["trend"] == []


# ---------- ⑥ 推送端点（共享密钥 / 直调函数，不启 HTTP） ----------

def test_push_endpoint_auth_and_ingest(monkeypatch):
    from fastapi import HTTPException
    from app.routers import market
    monkeypatch.setattr(market, "_LIDX_TOKEN", "t-ok")
    with pytest.raises(HTTPException) as ei:                     # 未带 token
        market.lidaxiao_push(payload={"entries": []}, x_lidx_token="")
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException):                           # 错 token（不区分防探测）
        market.lidaxiao_push(payload={"entries": []}, x_lidx_token="wrong")
    r = market.lidaxiao_push(payload={"entries": []}, x_lidx_token="t-ok")
    assert r["ok"] is False and "entries" in r["error"]          # 空 entries：ok=False 不炸
    _clear()
    r2 = market.lidaxiao_push(payload={"entries": [_entry()]}, x_lidx_token="t-ok")
    assert r2["ok"] is True and r2["stored"] == 1
    assert r2["latest"]["entry_id"] == "lishaoxiao_20261024 21:02"


def test_get_endpoint_reads_latest():
    from app.routers import market
    _clear()
    lv.ingest_entries([_entry()])
    d = market.lidaxiao_view(limit=5)
    assert d["ok"] and d["latest"]["title"] == "平衡引资和融资"
