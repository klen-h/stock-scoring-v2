# -*- coding: utf-8 -*-
"""模拟仓平仓记账（2026-11-18 用户："自动用模拟仓模拟"）。

原 `close_sim(code)` 只把 status 改成 closed，**不记任何盈亏** ⇒
界面永远只有"达目标✓"这种纸面状态，回答不了"模拟到底赚了多少"。
"""
import unittest.mock as mock

from app import t_trade_exec as TX

ROW = {"code": "600483", "name": "福能股份", "plan_id": "tp_x",
       "shares": 300, "cost": 11.10, "open_price": 11.10,
       "status": "holding"}


def test_close_by_plan_records_pnl():
    seen = {}

    def fake_execute(sql, params=None):
        seen["sql"], seen["params"] = sql, params
        return 1

    with mock.patch("app.t_trade_exec.db.fetch_one", return_value=ROW), \
            mock.patch("app.t_trade_exec.db.execute", side_effect=fake_execute):
        r = TX.close_sim_by_plan("tp_x", 11.40, "target")

    assert r["shares"] == 300
    assert r["pnl"] == 90.0              # (11.40-11.10)*300
    assert r["pnl_pct"] == 2.7
    assert "status='closed'" in seen["sql"]
    assert "pnl=%s" in seen["sql"]
    assert seen["params"][0] == 11.4 and seen["params"][2] == "target"


def test_close_by_plan_records_loss():
    with mock.patch("app.t_trade_exec.db.fetch_one", return_value=ROW), \
            mock.patch("app.t_trade_exec.db.execute", return_value=1):
        r = TX.close_sim_by_plan("tp_x", 10.95, "stop")
    assert r["pnl"] == -45.0             # (10.95-11.10)*300
    assert r["pnl_pct"] == -1.35


def test_close_by_plan_is_idempotent():
    """并发/重复触发时只记一次账（db.execute 影响 0 行 ⇒ 返回 None）。"""
    with mock.patch("app.t_trade_exec.db.fetch_one", return_value=ROW), \
            mock.patch("app.t_trade_exec.db.execute", return_value=0):
        assert TX.close_sim_by_plan("tp_x", 11.40, "target") is None


def test_close_by_plan_no_position():
    with mock.patch("app.t_trade_exec.db.fetch_one", return_value=None):
        assert TX.close_sim_by_plan("tp_x", 11.40, "target") is None


def test_close_by_plan_rejects_bad_input():
    with mock.patch("app.t_trade_exec.db.fetch_one", return_value=ROW):
        assert TX.close_sim_by_plan("", 11.40) is None
        assert TX.close_sim_by_plan("tp_x", 0) is None
