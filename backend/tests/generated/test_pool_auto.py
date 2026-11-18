# -*- coding: utf-8 -*-
"""自动选池（2026-11-19 用户："池子系统通过挑选自动加入池"）。

红线：
  ① **手动加的票永远不能被自动选池删掉**（`source='manual'`）；
  ② 排除已持仓/已有模拟仓的票（它们本来就在 watch_codes 里）；
  ③ 按 `space_atr_mult` 排序 —— 不是 `space_pct`（后者只是价格的函数）。
"""
import unittest.mock as mock

from app import t_trade_exec as TX

PLANS = [
    {"code": "600001", "name": "A", "space_atr_mult": 1.8, "space_pct": 5.0},
    {"code": "600002", "name": "B", "space_atr_mult": 1.6, "space_pct": 3.0},
    {"code": "600003", "name": "C", "space_atr_mult": 1.4, "space_pct": 4.0},
    {"code": "600004", "name": "D", "space_atr_mult": 1.2, "space_pct": 2.0},
]
MANUAL = [{"code": "600009"}]


def _run(fetch_side, execute_side=0, upsert=None, held=None):
    """按调用顺序喂 db.fetch：t_plans → manual → pool_codes。"""
    ups = upsert or (lambda *a, **k: None)
    with mock.patch("app.t_trade_exec.db.fetch_one",
                    return_value={"d": "2026-11-19"}), \
            mock.patch("app.t_trade_exec.db.fetch", side_effect=fetch_side), \
            mock.patch("app.t_trade_exec.db.execute", return_value=execute_side), \
            mock.patch("app.t_trade_exec.db.upsert", side_effect=ups), \
            mock.patch("app.t_trade_exec.holdings", return_value=held or {}), \
            mock.patch("app.t_trade_exec.resolve_name", side_effect=lambda c, n="": n or c):
        return TX.auto_pick_pool("2026-11-19", top=2)


def test_picks_top_by_atr_mult_and_never_touches_manual():
    """挑前 2 只（按 ATR 倍数），并跳过手动加的票。"""
    upserted = []
    r = _run([PLANS, MANUAL, []], execute_side=3,
             upsert=lambda t, d, **k: upserted.append(d["code"]))
    assert r["picked"] == ["600001", "600002"]
    assert r["removed"] == 3           # 只清了 auto 行
    assert r["excluded_manual"] == 1
    assert all(u != "600009" for u in upserted)


def test_upserted_rows_are_marked_auto():
    upserted = []
    _run([PLANS, [], []], execute_side=0,
         upsert=lambda t, d, **k: upserted.append(d))
    assert len(upserted) == 2
    assert all(d["source"] == "auto" for d in upserted)
    assert all("自动挑选" in d["note"] for d in upserted)


def test_excludes_held_positions():
    """已有仓位（真实或模拟）的票不进池 —— 它们本来就在 watch_codes 里。"""
    r = _run([PLANS, [], []], held={"600001": {"shares": 300}})
    assert "600001" not in r["picked"]
    assert r["picked"] == ["600002", "600003"]
    assert r["excluded_held"] == 1


def test_no_plan_batch_is_safe():
    """没有任何预案批次 ⇒ 不动池子，返回说明。"""
    with mock.patch("app.t_trade_exec.db.fetch_one", return_value={"d": None}), \
            mock.patch("app.t_trade_exec.db.fetch", return_value=[]):
        r = TX.auto_pick_pool("2026-11-19", top=2)
    assert r["picked"] == []
    assert r["plan_date"] is None
    assert "先跑一次盘后扫描" in r["note"]


def test_add_to_pool_upgrades_auto_to_manual():
    """★ 手动加一只"自动挑的票"要升级为 manual，否则明天会被自动清掉。"""
    ex = {"source": "auto"}
    seen = {}

    def fake_exec(sql, params=None):
        seen["sql"], seen["params"] = sql, params
        return 1

    with mock.patch("app.t_trade_exec.db.fetch_one", return_value=ex), \
            mock.patch("app.t_trade_exec.db.execute", side_effect=fake_exec), \
            mock.patch("app.t_trade_exec.pool_codes", return_value=[]):
        r = TX.add_to_pool(["600001"], note="我要盯")
    assert r["upgraded"] == ["600001"]
    assert "source='manual'" in seen["sql"]


def test_add_to_pool_skips_existing_manual():
    with mock.patch("app.t_trade_exec.db.fetch_one", return_value={"source": "manual"}), \
            mock.patch("app.t_trade_exec.pool_codes", return_value=[]):
        r = TX.add_to_pool(["600001"])
    assert r["skipped"] == ["600001"]
    assert r["upgraded"] == []
