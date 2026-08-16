"""
================================================================================
【文件作用】数据源健康监控（成功率滚动窗口 + 连续失败告警 + 恢复通知）
================================================================================

监控 4 个外部数据源：金十快讯 / 新浪宏观面板 / 腾讯ETF行情 / 东方财富。
各源在被调用处埋点（health.record），调度器另有探针循环保证空闲时段也定期采样。

告警规则：
  - 连续失败 ≥3 次 → 产生告警事件（如"金十快讯连续失败，检查 Cookie"）
  - 恢复成功且此前已告警 → 产生恢复事件
  告警事件直接进 /api/flash/notifications，前端页面通知铃铛自动弹手机/桌面提醒
  —— 这解决"金十 Cookie 过期后事件流悄悄死掉"的问题。

状态持久化：告警历史存 data/source_health.json（重启不丢），计数器内存即可。
================================================================================
"""

import threading
from collections import deque
from datetime import datetime

from app.flash import store

# 源 → 展示名（前端状态条用）
SOURCE_NAMES = {
    "jin10": "金十快讯",
    "sina_macro": "宏观面板",
    "tencent_etf": "ETF行情",
    "eastmoney": "东财板块",
}

_FAIL_THRESHOLD = 3          # 连续失败 N 次告警
_ALERTS_PATH = store.PATHS.get("health") or store.DATA_DIR + "/source_health.json"

_lock = threading.Lock()
_state = {}                  # {source: {events: deque[bool], consec_fail, alerted, last_ok, last_fail, last_error}}
_alerts = []                 # [{type, time, title, body}]，最近 20 条


def _now() -> str:
    return datetime.now().isoformat()


def _load_alerts() -> None:
    global _alerts
    _alerts = store._load(_ALERTS_PATH, {"alerts": []}).get("alerts", [])


def _save_alerts() -> None:
    store._save(_ALERTS_PATH, {"alerts": _alerts[-20:]})


_load_alerts()


def record(source: str, ok: bool, error: str = "") -> None:
    """
    埋点入口：各数据源在被调用处上报成功/失败。
    连续失败达到阈值 → 告警事件；从告警状态恢复 → 恢复事件。
    """
    with _lock:
        st = _state.setdefault(source, {
            "events": deque(maxlen=50), "consec_fail": 0, "alerted": False,
            "last_ok": None, "last_fail": None, "last_error": "",
        })
        st["events"].append(bool(ok))
        name = SOURCE_NAMES.get(source, source)
        if ok:
            st["consec_fail"] = 0
            st["last_ok"] = _now()
            if st["alerted"]:
                st["alerted"] = False
                _alerts.append({
                    "type": "source_recovered", "time": _now(),
                    "title": f"✅ {name}已恢复",
                    "body": "数据源恢复正常，快讯/诊断继续自动更新",
                })
                _save_alerts()
        else:
            st["consec_fail"] += 1
            st["last_fail"] = _now()
            st["last_error"] = error[:200]
            if st["consec_fail"] >= _FAIL_THRESHOLD and not st["alerted"]:
                st["alerted"] = True
                hint = "，Cookie 可能过期" if source == "jin10" else ""
                _alerts.append({
                    "type": "source_alert", "time": _now(),
                    "title": f"⚠️ 数据源异常：{name}",
                    "body": f"连续 {st['consec_fail']} 次失败{hint}（{error[:80] or '无返回数据'}）",
                })
                _save_alerts()
                print(f"[health] ⚠️ {name} 连续失败 {st['consec_fail']} 次，已告警")


def get_health() -> dict:
    """
    健康快照：{source: {status(健康/异常/无数据), ok_rate, consecutive_fails, last_ok, last_error}}
    ok_rate = 最近 50 次成功率（无采样时为 None）。
    """
    with _lock:
        out = {}
        for source, name in SOURCE_NAMES.items():
            st = _state.get(source)
            if not st or not st["events"]:
                out[source] = {"name": name, "status": "无数据",
                               "ok_rate": None, "consecutive_fails": 0,
                               "last_ok": None, "last_error": ""}
                continue
            ok_rate = round(sum(st["events"]) / len(st["events"]) * 100)
            out[source] = {
                "name": name,
                "status": "异常" if st["consec_fail"] >= _FAIL_THRESHOLD else "健康",
                "ok_rate": ok_rate,
                "consecutive_fails": st["consec_fail"],
                "last_ok": st["last_ok"],
                "last_error": st["last_error"],
            }
        return out


def recent_alerts(since: str = "") -> list:
    """返回 since 之后的告警/恢复事件（供 notifications 接口）。"""
    if not since:
        return _alerts[-10:]
    try:
        since_dt = datetime.fromisoformat(since)
        return [a for a in _alerts
                if datetime.fromisoformat(a.get("time", "2000-01-01")) > since_dt]
    except ValueError:
        return _alerts[-10:]
