# -*- coding: utf-8 -*-
"""推演登记 + 每日对照（2026-11-22 P0）。

重点锁三条纪律：
  1. 取不到数据必须 nodata，**不能返回 0**（0 会被读成"已实现"）
  2. 登记校验宁严勿松（概率合计 / 未知指标 / 非法 op 一律拒绝）
  3. 无有效推演时 run_daily 直接跳过，不烧 LLM
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import thesis_scenarios as ts


@pytest.fixture(autouse=True)
def _ready(monkeypatch):
    ts._READY = True
    yield
    ts._READY = True


BASE = {
    "title": "11-23~11-27 周推演",
    "horizon_start": "2026-11-23",
    "horizon_end": "2026-11-27",
    "scenarios": [{"name": "基准", "prob": 55, "path": "先抑后稳"},
                  {"name": "悲观", "prob": 45, "path": "破位"}],
    "checks": [{"key": "limit_down", "op": "<=", "value": 20}],
}


def test_cmp_all_ops_and_nodata():
    assert ts._cmp("<=", 10, 20) == "pass"
    assert ts._cmp("<=", 30, 20) == "fail"
    assert ts._cmp(">=", 3500, 3500) == "pass"
    assert ts._cmp("<", 20, 20) == "fail"
    assert ts._cmp(">", 21, 20) == "pass"
    assert ts._cmp("==", 5, 5) == "pass"
    # ★ 缺数据必须是 nodata，不能当成 fail 或 pass
    assert ts._cmp("<=", None, 20) == "nodata"
    assert ts._cmp("<=", 20, None) == "nodata"
    # 非法 op 退回默认 <=（不抛）
    assert ts._cmp("!=", 10, 20) == "pass"


def test_create_rejects_missing_title():
    p = dict(BASE, title="")
    r = ts.create(p)
    assert r["ok"] is False and "title" in r["error"]


def test_create_rejects_bad_date():
    r = ts.create(dict(BASE, horizon_start="2026/11/23"))
    assert r["ok"] is False and "YYYY-MM-DD" in r["error"]


def test_create_rejects_prob_sum():
    p = dict(BASE, scenarios=[{"name": "基准", "prob": 55},
                              {"name": "悲观", "prob": 40}])
    r = ts.create(p)
    assert r["ok"] is False and "100" in r["error"]


def test_create_rejects_unknown_metric():
    p = dict(BASE, checks=[{"key": "made_up_metric", "op": "<=", "value": 1}])
    r = ts.create(p)
    assert r["ok"] is False and "未知指标" in r["error"]


def test_create_rejects_bad_op():
    p = dict(BASE, checks=[{"key": "limit_down", "op": "!=", "value": 20}])
    r = ts.create(p)
    assert r["ok"] is False and "op" in r["error"]


def test_create_ok(monkeypatch):
    saved = {}
    monkeypatch.setattr(ts.db, "insert",
                        lambda table, data: saved.update(data) or 1)
    monkeypatch.setattr(ts.db, "execute", lambda *a, **k: 0)
    r = ts.create(BASE)
    assert r["ok"] is True and len(r["id"]) == 12
    assert saved["status"] == "active"
    assert "limit_down" in saved["checks_json"]


def test_evaluate_day_marks_nodata_not_zero(monkeypatch):
    """★ 关键：某项取不到值时 verdict=nodata、actual=None，绝不能是 0。"""
    def fake_metric(key, day):
        return (None, "无数据") if key == "sh_close" else (68.0, "")

    monkeypatch.setattr(ts, "metric_value", fake_metric)
    thesis = {"checks": [
        {"key": "limit_down", "label": "跌停家数", "op": "<=", "value": 20},
        {"key": "sh_close", "label": "上证收盘", "op": ">=", "value": 3500},
    ]}
    ev = ts.evaluate_day(thesis, "2026-11-20")
    a = {x["key"]: x for x in ev["checked"]}
    assert a["limit_down"]["verdict"] == "fail"
    assert a["sh_close"]["verdict"] == "nodata"
    assert a["sh_close"]["actual"] is None
    assert ev["n_pass"] == 0 and ev["n_fail"] == 1 and ev["n_nodata"] == 1


def test_run_daily_skips_without_thesis(monkeypatch):
    monkeypatch.setattr(ts, "list_theses", lambda active_only=False, day=None: [])
    called = []
    monkeypatch.setattr(ts, "llm_review", lambda *a, **k: called.append(1))
    r = ts.run_daily("2026-11-20")
    assert r["ok"] is True and "无有效推演" in r["skipped"]
    assert called == []          # 没有推演就不该调 LLM


def test_markdown_flags_missing_data():
    thesis = {"title": "T", "horizon_start": "2026-11-23",
              "horizon_end": "2026-11-27"}
    ev = {"checked": [{"key": "sh_close", "label": "上证收盘", "op": ">=",
                       "target": 3500, "actual": None, "verdict": "nodata",
                       "note": "无上证指数记录"}],
          "n_pass": 0, "n_fail": 0, "n_nodata": 1}
    md = ts._markdown(thesis, "2026-11-20", ev, None)
    assert "无数据" in md and "—" in md
    assert "仅确定性对照" in md
