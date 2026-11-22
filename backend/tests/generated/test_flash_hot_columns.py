# -*- coding: utf-8 -*-
"""金十 hot/important 列：补列幂等 + 爆级触发（2026-11-22 审查修复）。

覆盖的是审查发现的 P0：
  `_news_section` / `detect_trigger` 已在 SELECT hot，而补列原本只在
  `save_raw_data` 里、且位于 `if not new_items: return` 之后
  ⇒ Cookie 失效时列永不建立、快讯块永久报错。
修法是把补列抽成 `ensure_hot_columns()` 并在**读路径**也调用。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import flash_brief as fb
from app.flash import store


@pytest.fixture(autouse=True)
def _reset_ready():
    store._HOT_READY = False
    yield
    store._HOT_READY = False


def test_ensure_hot_columns_is_idempotent():
    """第一次执行两条 DDL，第二次直接返回（不重复打库）。"""
    calls = []
    with patch.object(store.db, "execute", lambda sql, *a, **k: calls.append(sql) or 0):
        store.ensure_hot_columns()
        assert len(calls) == 2
        store.ensure_hot_columns()
        assert len(calls) == 2          # 已置位，不再执行


def test_ensure_hot_columns_tolerates_duplicate_column():
    """列已存在（duplicate column）⇒ 视为成功并置位，不算失败。"""
    def _raise(sql, *a, **k):
        raise Exception('duplicate column name: hot')

    with patch.object(store.db, "execute", _raise):
        store.ensure_hot_columns()
    assert store._HOT_READY is True


def test_ensure_hot_columns_retries_on_real_error():
    """网络/权限等真实异常 ⇒ 不置位，下次还会重试（避免永久缺列）。"""
    def _raise(sql, *a, **k):
        raise Exception("connection reset by peer")

    with patch.object(store.db, "execute", _raise):
        store.ensure_hot_columns()
    assert store._HOT_READY is False


def test_save_raw_data_ensures_columns_before_early_return():
    """★ P0 核心：无新快讯时 `save_raw_data` 提前返回，但补列**必须已经发生**。"""
    calls = []
    with patch.object(store.db, "execute", lambda sql, *a, **k: calls.append(sql) or 0), \
         patch.object(store.db, "fetch", lambda *a, **k: []):
        store.save_raw_data(all_items=[], new_items=[])     # 无新数据 ⇒ 提前 return
    assert any("ADD COLUMN hot" in c for c in calls), "补列被提前 return 跳过了"


def test_detect_trigger_fires_on_boom(monkeypatch):
    """30 分钟内出现 1 条 hot='爆' ⇒ 触发（金十编辑标记=高置信）。"""
    rows = [{"content": "", "cluster": "其他", "time": "2026-11-22 10:00:00",
             "hot": "爆"}]
    monkeypatch.setattr(store, "ensure_hot_columns", lambda: None)
    monkeypatch.setattr(fb.db, "fetch", lambda *a, **k: rows)
    reasons = fb.detect_trigger({})
    assert any("爆级" in r for r in reasons)


def test_detect_trigger_silent_without_boom(monkeypatch):
    """无爆 + 无强快讯 + 无指数异动 ⇒ 不触发（对照，防误伤）。"""
    rows = [{"content": "", "cluster": "其他", "time": "2026-11-22 10:00:00",
             "hot": None},
            {"content": "", "cluster": "其他", "time": "2026-11-22 10:01:00",
             "hot": "沸"}]           # 沸/热不算爆级
    monkeypatch.setattr(store, "ensure_hot_columns", lambda: None)
    monkeypatch.setattr(fb.db, "fetch", lambda *a, **k: rows)
    assert fb.detect_trigger({}) == []
