# -*- coding: utf-8 -*-
"""list_plans 短缓存：命中不查库、key 隔离、返回副本防污染（2026-11-22）。"""
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import t_trade_exec as TX


def _rows():
    return [{"code": "000001", "plan_date": "2026-11-20", "feasible": 1},
            {"code": "000002", "plan_date": "2026-11-20", "feasible": 1}]


@pytest.fixture(autouse=True)
def _reset():
    TX._PLANS_CACHE.update({"key": None, "ts": 0.0, "rows": None})
    yield
    TX._PLANS_CACHE.update({"key": None, "ts": 0.0, "rows": None})


def test_second_call_hits_cache_no_db():
    with patch.object(TX, "ensure_tables"), \
         patch.object(TX.db, "fetch", return_value=_rows()) as f1, \
         patch.object(TX.db, "fetch_one", return_value={"d": None}):
        assert len(TX.list_plans("2026-11-20", feasible_only=True)) == 2
        assert f1.call_count == 1
        # 第二次：数据库不该再被碰
        assert len(TX.list_plans("2026-11-20", feasible_only=True)) == 2
        assert f1.call_count == 1


def test_cache_key_separates_feasible_flag():
    with patch.object(TX, "ensure_tables"), \
         patch.object(TX.db, "fetch", return_value=_rows()) as f1, \
         patch.object(TX.db, "fetch_one", return_value={"d": None}):
        TX.list_plans("2026-11-20", feasible_only=True)
        TX.list_plans("2026-11-20", feasible_only=False)
        assert f1.call_count == 2      # 两个 key，各自查一次


def test_returns_copy_not_cache_object():
    with patch.object(TX, "ensure_tables"), \
         patch.object(TX.db, "fetch", return_value=_rows()), \
         patch.object(TX.db, "fetch_one", return_value={"d": None}):
        a = TX.list_plans("2026-11-20", feasible_only=True)
        a[0]["code"] = "MUTATED"
        b = TX.list_plans("2026-11-20", feasible_only=True)
        assert b[0]["code"] == "000001"


def test_ttl_expiry_refetches():
    with patch.object(TX, "ensure_tables"), \
         patch.object(TX.db, "fetch", return_value=_rows()) as f1, \
         patch.object(TX.db, "fetch_one", return_value={"d": None}):
        TX.list_plans("2026-11-20", feasible_only=True)
        TX._PLANS_CACHE["ts"] = time.time() - TX._PLANS_TTL - 1
        TX.list_plans("2026-11-20", feasible_only=True)
        assert f1.call_count == 2
