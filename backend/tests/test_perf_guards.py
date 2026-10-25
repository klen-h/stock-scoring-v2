# -*- coding: utf-8 -*-
"""性能守卫单测（2026-10-26 审查补齐）。

覆盖两个"看起来只是日志/优化，实际会影响可用性"的点：
1. `gate._miss_watch`：语义是**持续告警**（每满 50 次一条），不是"只提醒一次"；
   且计数必须在并发下不丢（FastAPI 线程池会并发调用）。
2. `tencent.refresh_all_stocks` 单飞闸：并发调用**只跑一次**，其余**等它跑完再返回缓存**
   （不是直接 return ⇒ 调用方读到空池）；owner 卡死超时后允许接管。

跑法：`PYTHONPATH=backend python -m pytest backend/tests/test_perf_guards.py -q`
"""
import threading
import time

import pytest

from app import tencent
from app.mainforce import gate


@pytest.fixture(autouse=True)
def _reset_state():
    """每个用例前把两个模块的进程级状态恢复干净（它们都是全局字典）。"""
    with gate._GATE_MISS_LOCK:
        gate._GATE_MISS.clear()
        gate._GATE_MISS.update({"no_bars": 0, "no_chip_phase": 0})
    with tencent._REFRESH_GATE:
        tencent._REFRESH_INFLIGHT.update(running=False, ts=0.0, event=None, owner=None)
    yield


def test_miss_watch_logs_every_50(capsys):
    """120 次缺口 ⇒ 打 2 条（50 / 100），且计数准确。"""
    for _ in range(120):
        gate._miss_watch("no_bars")
    out = capsys.readouterr().out
    assert gate._GATE_MISS["no_bars"] == 120
    assert out.count("data-miss pass-through: no_bars") == 2


def test_miss_watch_thread_safe():
    """并发自增不丢计数（原 `+=` 无锁会丢）。"""
    def worker():
        for _ in range(2000):
            gate._miss_watch("no_chip_phase")

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert gate._GATE_MISS["no_chip_phase"] == 8000


def test_single_flight_waits_and_returns_cache(monkeypatch):
    """并发调用只跑一次 inner；第二个等它结束并拿到缓存（不是 None/空池）。"""
    calls = []
    started = threading.Event()
    release = threading.Event()

    def fake_inner(force=False):
        calls.append(force)
        started.set()
        release.wait(5)
        tencent._cache["stocks"] = {"000001": {"code": "000001", "price": 1.0}}
        return tencent._cache["stocks"]

    monkeypatch.setattr(tencent, "_refresh_all_stocks_inner", fake_inner)
    monkeypatch.setattr(tencent, "_REFRESH_WAIT_SEC", 5)

    results = []
    t1 = threading.Thread(target=lambda: results.append(tencent.refresh_all_stocks()))
    t1.start()
    assert started.wait(5), "第一个刷新未启动"
    t2 = threading.Thread(target=lambda: results.append(tencent.refresh_all_stocks()))
    t2.start()
    time.sleep(0.2)
    assert len(calls) == 1, "第二个调用不应触发新的 inner（应等待）"

    release.set()
    t1.join(5)
    t2.join(5)
    assert len(calls) == 1
    assert len(results) == 2
    assert results[1] and "000001" in results[1], "等待方应拿到刷新后的缓存"


def test_stale_owner_is_taken_over(monkeypatch):
    """owner 卡超过 _REFRESH_STALE_SEC ⇒ 允许接管（否则行情永久停更）。"""
    with tencent._REFRESH_GATE:
        tencent._REFRESH_INFLIGHT.update(
            running=True, ts=time.time() - tencent._REFRESH_STALE_SEC - 10,
            event=threading.Event())

    called = []
    monkeypatch.setattr(tencent, "_refresh_all_stocks_inner",
                        lambda force=False: called.append(force) or {"x": 1})

    tencent.refresh_all_stocks()
    assert called == [False], "僵死后应接管并真正执行一次刷新"
