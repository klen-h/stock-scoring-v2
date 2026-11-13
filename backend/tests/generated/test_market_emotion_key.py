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
    # ★ 2026-11-13：补 `has_live` —— 盘后补记 `close_*` 的新守卫要求"有实时行情"。
    #   不加这条，测试在 15:00 之后跑会走到 close 分支被拒写（实测 22:xx 跑失败）。
    #   `quote_as_of` 故意留空：守卫对该字段缺失时放行（老版本兼容），
    #   专门由下面的 `_close_data_trustworthy` 用例覆盖"有值但过期"的分支。
    "has_live": True,
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
    market._ensure_emotion_table()   # sqlite 测试库需先建表（PG 由线上建好）
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


# ── ★★ 2026-11-13：盘后 `close_*` 的可信度守卫（详见 `market._close_data_trustworthy`）──

def test_close_guard_rejects_unusable_quotes():
    """四种"数据不属于今天"的情形必须拒写；正常情形放行。

    背景：旧守卫只看 `_price_date()`（纯时钟），导致
      · 缓存为空 ⇒ 把当天真值覆盖成 None（实测 11-13 被覆盖过）；
      · 缓存冻结 ⇒ 写入与昨日完全相同的 up/down（实测 10-16/10-26）。
    """
    day = "2026-11-13"
    good = {"has_live": True, "quote_from_snapshot": False,
            "quote_as_of": day + " 15:00", "up": 2103, "down": 2668}
    assert market._close_data_trustworthy(good, day) is True
    assert market._close_data_trustworthy(dict(good, has_live=False), day) is False
    assert market._close_data_trustworthy(dict(good, quote_from_snapshot=True), day) is False
    assert market._close_data_trustworthy(dict(good, up=None), day) is False
    assert market._close_data_trustworthy(dict(good, down=None), day) is False
    assert market._close_data_trustworthy(
        dict(good, quote_as_of="2026-11-12 15:00"), day) is False
    # 字段缺失（老版本/无缓存）时不该被误伤
    assert market._close_data_trustworthy(dict(good, quote_as_of=None), day) is True


def test_close_not_overwritten_when_no_live_quotes(monkeypatch, clean_today_row):
    """盘后但**没有实时行情** ⇒ 不得覆盖已有 `close_*`（防写 None）。"""
    from datetime import datetime as _dt
    today = clean_today_row
    monkeypatch.setattr(market, "_price_date", lambda: today)
    # 强制"盘后"分支，否则本用例在盘前跑不到 close 路径
    monkeypatch.setattr(market, "_bj_now",
                        lambda: _dt.strptime(today + " 22:00", "%Y-%m-%d %H:%M"))
    # 先写入一份"正确"的收盘数据
    market._save_emotion_daily(dict(VAL))
    before = dict(_row(today))
    assert before.get("close_up"), "前置条件：先要有一次成功写入"

    # 再来一次没有实时行情的调用（模拟缓存为空/冻结）
    # ⚠️ `as_of` 必须与第一次不同：否则会先命中上方"同一 as_of 已记过 ⇒ return"的幂等
    #    分支，测到的就不是守卫了（测试会假通过）。
    market._save_emotion_daily(dict(VAL, has_live=False, up=None, down=None,
                                    as_of="2026-10-24T22:30:00+08:00"))
    after = _row(today)
    assert after.get("close_up") == before.get("close_up")
    assert after.get("close_down") == before.get("close_down")
