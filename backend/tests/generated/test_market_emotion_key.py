# -*- coding: utf-8 -*-
"""BK-09 回归：market_emotion_daily 行键必须 = 数据所属交易日（回放数据不落库）。

背景（"写入时刻≠数据所属时刻"四案之第④案）：
  旧行键 = 写入自然日，守卫只挡 val.trading_day（布尔）为空 ⇒ 交易日 00:00-09:15
  与休市日会写出行键错位的行。新实现锚定 _price_date()（行情价格所属交易日）：
  数据日 != 今天 ⇒ 不落库。
"""
import pytest

from app.flash import rules as flash_rules
from app.routers import market

VAL = {
    "as_of": "2026-10-24T10:00:00+08:00",
    "trading_day": True,
    "up": 800, "down": 4000, "limit_up": 11, "limit_down": 3,
    "verdict": "测试预判",
}


def _row(day):
    from app.database import db
    r = db.fetch_one("SELECT * FROM market_emotion_daily WHERE date = %s", (day,))
    if r is None:
        return None
    return r if isinstance(r, dict) else {k: r[k] for k in r.keys()}


@pytest.fixture()
def clean_today_row():
    from app.database import db
    today = flash_rules.beijing_now().strftime("%Y-%m-%d")
    db.execute("DELETE FROM market_emotion_daily WHERE date = %s", (today,))
    yield today
    db.execute("DELETE FROM market_emotion_daily WHERE date = %s", (today,))


def test_replay_data_not_written(monkeypatch, clean_today_row):
    """数据日 != 今天（休市日/盘前回放）⇒ 不落库。旧实现会写出「键=今天、数据=昨日」。"""
    monkeypatch.setattr(market, "_price_date", lambda: "2020-01-01")
    market._save_emotion_daily(dict(VAL))
    assert _row(clean_today_row) is None


def test_today_data_written_with_data_day_key(monkeypatch, clean_today_row):
    """数据日 == 今天 ⇒ 正常落库，行键=今天，预判字段在位。

    落库分支随真实时钟变化（盘前/盘中→主列 verdict；15:00 后→对账列 close_verdict），
    两条路径都证实行键=数据日，故断言二选一命中。
    """
    today = clean_today_row
    monkeypatch.setattr(market, "_price_date", lambda: today)
    market._save_emotion_daily(dict(VAL))
    row = _row(today)
    assert row is not None, "当日真实数据应落库（若未落库，检查 _save_emotion_daily 是否被吞异常）"
    assert row.get("verdict") == "测试预判" or row.get("close_verdict") == "测试预判"
