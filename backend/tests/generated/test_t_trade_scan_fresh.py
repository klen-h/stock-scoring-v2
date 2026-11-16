# -*- coding: utf-8 -*-
"""做T 扫描数据新鲜度：等 K 线回填 + 标记过期预案。

2026-11-16 事故：15:42 定时扫描时今日 K 线尚未回填，用 11-13 收盘算出
"今日预案"（000878 云南铜业 price_ref=13.01、支撑 12.71）；22:12 手动补跑
用上今日收盘 12.62 后该票不再 feasible，但**旧行没被删/标注**，
于是过期价位继续在界面上冒充"今日预案"。
"""
import unittest.mock as mock

from app import t_trade_exec as TX
from app.flash import scheduler as S


def test_scan_data_ready_checks_kline_date():
    with mock.patch("app.database.db.fetch_one",
                    return_value={"d": "2026-11-16"}):
        assert S._scan_data_ready("2026-11-16") is True
        assert S._scan_data_ready("2026-11-15") is False
    with mock.patch("app.database.db.fetch_one", return_value=None):
        assert S._scan_data_ready("2026-11-16") is False
    # 查询失败按"未就绪"处理 ⇒ 不占当日标记，下周期重试
    with mock.patch("app.database.db.fetch_one",
                    side_effect=Exception("db down")):
        assert S._scan_data_ready("2026-11-16") is False


def test_scan_market_marks_unrefreshed_plans_stale():
    """扫描结束必须把"本次没重算"的当日旧预案标为不可行。"""
    calls = []

    def fake_execute(sql, params=None):
        calls.append((sql, params))
        return 0

    with mock.patch("app.database.db.fetch", return_value=[]), \
            mock.patch("app.database.db.execute", side_effect=fake_execute):
        rep = TX.scan_market("2026-11-16", save=True)

    stale = [(s, p) for s, p in calls if "SET feasible=0" in s]
    assert len(stale) == 1, "必须且只能发一条 stale 标记 UPDATE"
    sql, params = stale[0]
    assert "plan_date=%s" in sql
    assert "feasible=1" in sql
    assert "updated_at<%s" in sql
    assert params == ("2026-11-16", rep["started_at"])
    assert rep["stale_marked"] == 0        # mock 返回影响 0 行


def test_scan_market_stale_marks_count():
    """db.execute 的影响行数要如实回传（便于日志里看出清了多少旧预案）。"""
    def fake_execute(sql, params=None):
        return 7 if "SET feasible=0" in sql else 0

    with mock.patch("app.database.db.fetch", return_value=[]), \
            mock.patch("app.database.db.execute", side_effect=fake_execute):
        rep = TX.scan_market("2026-11-16", save=True)
    assert rep["stale_marked"] == 7
