# -*- coding: utf-8 -*-
"""快讯解读 × 持仓联动回归（flash_brief._holdings_impact）。

红线断言：
  ① 持仓不出网——联动是本机确定性规则（news direction × 持仓行业/名称），非 LLM；
  ② Guardrails #5：lean 只输出 受益/承压/多空混合（观察），无买卖指令词；
  ③ 中性 direction 不参与匹配；无持仓 → []。
"""
import unittest.mock as mock

from app import flash_brief as fb

NEWS = [
    {"idx": 1, "gist": "美方放宽外资券商准入", "cause": "金融开放推进",
     "scope": "证券/金融IT", "direction": "利好"},
    {"idx": 2, "gist": "美伊冲突升级油价大涨", "cause": "石油收入受创后油价补涨",
     "scope": "石油石化/油运", "direction": "利空"},
    {"idx": 3, "gist": "常规项目中标", "cause": "常规", "scope": "个股",
     "direction": "中性"},
]
PF = [{"code": "601991", "name": "中信海直", "created_at": "x"},
      {"code": "600030", "name": "中信证券", "created_at": "x"},
      {"code": "300750", "name": "宁德时代", "created_at": "x"}]
STATES = {"601991": {"code": "601991", "phase": "吸筹", "signal": "watch"},
          "600030": {"code": "600030", "phase": "出货", "signal": "avoid"}}
IMAP = {"601991": "石油石化", "600030": "证券", "300750": "电池"}


def _setup(monkeypatch, tmp_path):
    fb._PATH_HACK = None
    monkeypatch.setattr(fb.db, "fetch",
                        lambda sql, params=None: PF if "user_portfolio" in sql
                        else (_ for _ in ()).throw(AssertionError(sql)))
    monkeypatch.setattr("app.mainforce.state.load_latest", lambda codes: STATES)
    monkeypatch.setattr("app.industry_map.get_industry_map", lambda: IMAP)
    import app.tencent as T
    monkeypatch.setitem(T._cache, "stocks", {
        "601991": {"code": "601991", "price": 8.9, "change_pct": 2.1},
        "600030": {"code": "600030", "price": 30.1, "change_pct": 1.2},
        "300750": {"code": "300750", "price": 250.0, "change_pct": 3.5}})
    fb._HOLDINGS_CACHE.update(ts=0.0, val=[])


def test_name_and_industry_matching(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    out = fb._holdings_impact(NEWS)
    by = {x["code"]: x for x in out}
    # 600030 证券：idx1 利好（scope 含"证券"）⇒ 受益方向
    h = by["600030"]
    assert any(x["direction"] == "利好" and x["match"] == "行业命中"
               for x in h["hits"]), h
    assert h["lean"] == "受益方向（观察）"
    assert h["phase"] == "出货" and h["chg_pct"] == 1.2
    # 601991 石油石化：idx2 利空（scope 含"石油石化"）⇒ 承压方向
    h = by["601991"]
    assert any(x["direction"] == "利空" for x in h["hits"]), h
    assert h["lean"] == "承压方向（观察）"
    # 300750 电池：无关联新闻 ⇒ 无直接关联
    assert by["300750"]["lean"] == "无直接关联（观察）"
    assert by["300750"]["hits"] == []


def test_neutral_direction_ignored(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    news = [{"idx": 1, "gist": "宁德时代常规动态", "cause": "常规",
             "scope": "电池", "direction": "中性"}]
    out = fb._holdings_impact(news)
    assert all(x["lean"] == "无直接关联（观察）" for x in out)


def test_no_directive_wording(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for x in fb._holdings_impact(NEWS):
        for banned in ("买入", "卖出", "减仓", "加仓", "止损", "止盈", "清仓"):
            assert banned not in x["lean"], (x["code"], x["lean"])
            for hit in x["hits"]:
                assert banned not in hit["cause"], (x["code"], hit)


def test_no_holdings_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(fb.db, "fetch", lambda sql, params=None: [])
    fb._HOLDINGS_CACHE.update(ts=0.0, val=[])
    assert fb._holdings_impact(NEWS) == []


def test_cache_60s(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    calls = {"n": 0}
    real_fetch = fb.db.fetch

    def counting(sql, params=None):
        if "user_portfolio" in sql:
            calls["n"] += 1
        return PF

    with monkeypatch.context() as m:
        m.setattr(fb.db, "fetch", counting)
        fb._holdings_impact(NEWS)
        fb._holdings_impact(NEWS)
    assert calls["n"] == 1, "60s 缓存未生效"
