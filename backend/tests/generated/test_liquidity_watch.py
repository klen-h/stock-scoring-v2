# -*- coding: utf-8 -*-
"""流动性三件套（app/liquidity_watch.py）的业务语义测试。

背景（2026-10-25，eypai 溯源调研 · docs/reports/20261025_eypai_research.md）：
  两融/期指基差/中债10Y 的**实时**链路早已存在（金十两融 + macro 面板），
  本模块补的是**每日落库**。本文件锚定的业务语义：
  1. 两融口径台阶：金十=沪+深（亿），东财兜底=沪深北（元→亿，含北交所）⇒ 序列以
     margin_src 为准，两源解析各自自洽（add 用各自相邻行，绝不跨源相减）；
  2. 基差取**当月**合约（次月只作对照，不进趋势序列）；趋势用非年化 basis_rate；
  3. 中美利差 = (cn10y − us10y) × 100（bp）；
  4. fail-open：单源失败落 NULL + degraded（缺失 ≠ 0）；同日重跑幂等。
DB 走 conftest 强制的 SQLite（绝不连线上 Supabase）。
"""
from app import liquidity_watch as lw

# ── 金十口径 canned（get_margin() 的输出形状，实测 2026-10-25）──────────────
_JIN10 = {
    "date": "2026-10-22", "fund_bal": 24478.8, "fund_bal_chg5": -472.8,
    "fund_buy": 1184.5, "short_bal": 287.8, "partial_latest": True,
    "sse_yi": 13000.1, "szse_yi": 11478.7,
    "hist": [{"date": "2026-10-22", "fund_bal": 24478.8, "fund_buy": 1},
             {"date": "2026-10-21", "fund_bal": 24712.5, "fund_buy": 1}],
}

# ── 东财口径 canned（RPTA_RZRQ_LSHJ result.data，实测 2026-10-25；沪深北，元）──
_EM_ROWS = [
    {"DIM_DATE": "2026-10-22 00:00:00", "RZRQYE": 2455691846989},
    {"DIM_DATE": "2026-10-21 00:00:00", "RZRQYE": 2445691846989},
]

# ── 宏观面板 canned（bonds + 基差当月/次月）───────────────────────────────
_PANEL = {
    "cn10y": {"price": 1.78}, "us10y": {"price": 4.02},
    "_futures_basis": {"items": [
        {"product": "IF", "spot_name": "沪深300", "tenors": [
            {"label": "当月", "code": "nf_IF2612", "basis": 12.3, "basis_rate": 0.094,
             "annualized": 1.1, "oi": 191736, "days_left": 48},
            {"label": "次月", "code": "nf_IF2601", "basis_rate": 0.5}]},
        {"product": "IM", "spot_name": "中证1000", "tenors": [
            {"label": "当月", "code": "nf_IM2612", "basis": -80.0, "basis_rate": -1.2,
             "annualized": None, "oi": 50000, "days_left": 48}]},
    ]},
}


def _clear():
    lw.ensure_table()
    lw.db.execute(f"DELETE FROM {lw._TABLE}")


# ---------- ① 两融解析：金十（沪+深，亿） ----------

def test_jin10_margin_parse():
    m = lw._jin10_margin_from(_JIN10)
    assert m == {"date": "2026-10-22", "src": "jin10", "total_yi": 24478.8,
                 "add_yi": -233.7, "sse_yi": 13000.1, "szse_yi": 11478.7}


def test_jin10_margin_short_hist_add_none():
    m = lw._jin10_margin_from({**_JIN10, "hist": _JIN10["hist"][:1]})
    assert m["add_yi"] is None                 # 相邻行缺失 ⇒ 不猜
    assert lw._jin10_margin_from(None) is None
    assert lw._jin10_margin_from({"date": "2026-10-22"}) is None  # 无余额


# ---------- ② 两融解析：东财兜底（沪深北，元→亿） ----------

def test_em_margin_parse():
    m = lw._em_margin_from_rows(_EM_ROWS)
    assert m["src"] == "eastmoney"
    assert m["date"] == "2026-10-22"
    assert m["total_yi"] == 24556.9             # 元 → 亿
    assert m["add_yi"] == 100.0                 # 相邻行差，同源自洽
    assert m["sse_yi"] is None and m["szse_yi"] is None  # 该源无分拆


def test_em_margin_defensive():
    assert lw._em_margin_from_rows(None) is None
    assert lw._em_margin_from_rows([_EM_ROWS[0]]) is None      # <2 行算不了 add
    assert lw._em_margin_from_rows([{"DIM_DATE": ""}, {}]) is None


# ---------- ③ 面板提取：利差 + 基差取当月 ----------

def test_panel_liquidity():
    pl = lw._panel_liquidity(_PANEL)
    assert pl["cn10y"] == 1.78 and pl["us10y"] == 4.02
    assert pl["spread_bp"] == -224.0            # (1.78-4.02)*100
    assert set(pl["basis"]) == {"IF", "IM"}
    assert pl["basis"]["IF"]["basis_rate"] == 0.094   # 当月，不是次月 0.5
    assert pl["basis"]["IM"]["basis_rate"] == -1.2


def test_panel_liquidity_missing():
    pl = lw._panel_liquidity({"cn10y": {"price": 1.78}})   # 无 us10y ⇒ 无利差
    assert pl["cn10y"] == 1.78 and pl["us10y"] is None
    assert pl["spread_bp"] is None and pl["basis"] == {}
    empty = lw._panel_liquidity(None)
    assert empty == {"cn10y": None, "us10y": None, "spread_bp": None, "basis": {}}


# ---------- ④ 落库幂等 / 读取形状 / fail-open（SQLite 全链路） ----------

def test_run_daily_and_load(monkeypatch):
    _clear()
    monkeypatch.setattr(lw, "_margin_from_jin10", lambda: lw._jin10_margin_from(_JIN10))
    monkeypatch.setattr(lw, "_panel_snapshot", lambda: lw._panel_liquidity(_PANEL))
    r = lw.run_daily(day="2026-10-24")
    assert r["ok"] and r["degraded"] == [] and r["margin_date"] == "2026-10-22"
    lw.run_daily(day="2026-10-24")              # 同日重跑 = 幂等
    n = lw.db.fetch_one(f"SELECT COUNT(*) AS n FROM {lw._TABLE}")["n"]
    assert n == 1
    d = lw.load(30)
    assert d["ok"] and d["date"] == "2026-10-24"
    assert d["latest"]["margin"]["total_yi"] == 24478.8
    assert d["latest"]["margin"]["add_yi"] == -233.7
    assert d["latest"]["bonds"]["spread_bp"] == -224.0
    assert d["latest"]["basis"]["IF"]["basis_rate"] == 0.094
    assert d["history"][0]["date"] == "2026-10-24"       # 正序
    assert d["history"][0]["basis_rate"]["IM"] == -1.2
    assert "不进决策链" in d["note"]


def test_load_chg5_from_series(monkeypatch):
    _clear()
    days = [f"2026-10-{d:02d}" for d in range(12, 18)]   # 6 个交易日
    cur = {"total": 24000.0}

    def fake_margin():
        cur["total"] += 100.0
        return {"date": "2026-10-17", "src": "jin10",
                "total_yi": round(cur["total"], 1), "add_yi": 100.0,
                "sse_yi": None, "szse_yi": None}
    monkeypatch.setattr(lw, "_margin_from_jin10", fake_margin)
    monkeypatch.setattr(lw, "_panel_snapshot",
                        lambda: lw._panel_liquidity(_PANEL))
    for day in days:
        lw.run_daily(day=day)
    d = lw.load(30)
    # chg5 = 最新(24500+600=24600... 依次 24100..24600) - 5 行前
    assert d["latest"]["margin"]["chg5_yi"] == 500.0      # 6 天每天 +100
    assert len(d["history"]) == 6


def test_run_daily_fail_open(monkeypatch):
    _clear()
    monkeypatch.setattr(lw, "_margin_from_jin10", lambda: None)
    monkeypatch.setattr(lw, "_margin_from_eastmoney", lambda: None)
    monkeypatch.setattr(lw, "_panel_snapshot", lambda: lw._panel_liquidity(None))
    r = lw.run_daily(day="2026-10-24")
    assert r["ok"] is True                       # fail-open：绝不抛错
    assert r["degraded"] == ["margin", "bonds", "basis"]
    d = lw.load(30)
    assert d["ok"] and d["latest"]["margin"]["total_yi"] is None   # 缺失≠0
    assert d["degraded"] == "margin,bonds,basis"


def test_load_empty_table():
    _clear()
    d = lw.load()
    assert d["ok"] and d["latest"] is None and d["history"] == []
