# -*- coding: utf-8 -*-
"""调度去重（`store.is_schedule_done` / `mark_schedule_done`）的日期格式回归测试。

2026-11-16 事故：企微每 5 分钟重复推同一条「日终观察：3 个破位 / 0 个新主题」。

根因：
  `watch_alert.run_daily()` 写标记时用 `day = strftime("%Y-%m-%d")`（横杠），
  但读标记时 `is_schedule_done(tag)` 不传日期 ⇒ 内部 `_bj_date()`
  为了兼容老项目返回 `2026/11/16`（斜杠）。两者字符串比较**永远不等**
  ⇒ 带去重的一次性推送退化成"每轮重推"。

本文件锁住"日期格式无关"这个不变量，防止 50+ 个调用点里再有人踩。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.flash import store


def test_norm_schedule_date_both_formats():
    """斜杠 / 横杠必须归一成同一个值。"""
    assert store._norm_schedule_date("2026/11/16") == "2026-11-16"
    assert store._norm_schedule_date("2026-11-16") == "2026-11-16"
    assert store._norm_schedule_date(" 2026/11/16 ") == "2026-11-16"
    assert store._norm_schedule_date("2026/11/16 20:32:33") == "2026-11-16"
    assert store._norm_schedule_date(None) == ""
    assert store._norm_schedule_date("") == ""


class _FakeDB:
    """只需要 `fetch_one`，避免测试碰生产 `schedule_state`。

    ⚠️ 必须**按 task 过滤** —— 否则查任何任务都返回"已跑过"，
    会把"另一天/另一个任务应该返回 False"的断言也弄成 True。
    """

    def __init__(self, done_date, task="watch_alert:2026-11-16"):
        self.done_date = done_date
        self.task = task

    def fetch_one(self, sql, params=None):
        wanted = (params or (None,))[0]
        if wanted != self.task:
            return None
        return {"done_date": self.done_date}


def test_is_schedule_done_ignores_slash_vs_dash(monkeypatch):
    """★ 核心回归：DB 存横杠、默认参数是斜杠，也必须判定为"已跑过"。"""
    monkeypatch.setattr(store, "db", _FakeDB("2026-11-16"))
    # `_bj_date()` 返回老项目兼容格式（斜杠）
    monkeypatch.setattr(store, "_bj_date", lambda: "2026/11/16")

    # 不传日期（旧调用方式）—— 事故中失效的那条路径
    assert store.is_schedule_done("watch_alert:2026-11-16") is True
    # 显式传横杠（新调用方式）
    assert store.is_schedule_done("watch_alert:2026-11-16", "2026-11-16") is True
    # 显式传斜杠也应命中
    assert store.is_schedule_done("watch_alert:2026-11-16", "2026/11/16") is True
    # 不同日期仍是 False（不能因为规范化而"永远已跑过"）
    assert store.is_schedule_done("watch_alert:2026-11-15") is False


def test_is_schedule_done_accepts_legacy_slash_in_db(monkeypatch):
    """历史数据是斜杠格式，不需要数据迁移就能正确判定。"""
    monkeypatch.setattr(store, "db", _FakeDB("2026/11/10", "evt_theme:中东战争"))
    monkeypatch.setattr(store, "_bj_date", lambda: "2026/11/10")
    assert store.is_schedule_done("evt_theme:中东战争") is True
    assert store.is_schedule_done("evt_theme:中东战争", "2026-11-10") is True


def test_is_schedule_done_db_error_fails_open(monkeypatch):
    """★ DB 真读不到时仍然 fail-open（宁可多推一次，不能静默不推）。"""

    class _BrokenDB:
        def fetch_one(self, sql, params=None):
            raise RuntimeError("db down")

    monkeypatch.setattr(store, "db", _BrokenDB())
    assert store.is_schedule_done("anything") is False


class _CaptureDB:
    def __init__(self):
        self.payload = None

    def upsert(self, table, payload, conflict_columns=None):
        self.payload = payload


def test_mark_schedule_done_writes_dash_format(monkeypatch):
    """无论传哪种格式，落库统一横杠（历史斜杠数据由读取侧兼容）。"""
    fake = _CaptureDB()
    monkeypatch.setattr(store, "db", fake)
    store.mark_schedule_done("t", "2026/11/16")
    assert fake.payload["done_date"] == "2026-11-16"
    store.mark_schedule_done("t", "2026-11-17")
    assert fake.payload["done_date"] == "2026-11-17"
