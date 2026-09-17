# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】「本部署在服务哪个用户」的唯一口径 —— 供 user_portfolio 的全局读取使用
================================================================================

背景（2026-09-17 实测）：
  项目从单用户演进到多用户（`migrate_user_isolation.py` 给 user_portfolio 加了
  `user_id`），**前端接口全部按 user_id 过滤**，但**后台全局组件仍在全表读** →
  会把别的账号的持仓当成本部署用户的：

    · `coach/rules.py::_real_holdings()`      → 教练盯**所有账号**的持仓
    · `coach/position_sizing.py`              → 仓位建议含所有账号
    · `daily_report.py::_portfolio()`         → 日报含所有账号
    · `flash/scheduler.py`（舆情 / 快照池 ×2） → 推**别人**的股票
    · `flash/llm.py`                          → LLM 点评提到别人的持仓

  实测案例：`admin(uid=1)` 的教练一直提示 `sky(uid=2)` 的海德股份 000567，
  而 admin 在界面上根本看不到这条记录 → 现象即"持仓删了，教练还提示"。

取谁：
  · 优先环境变量 `PRIMARY_USER_ID`（显式指定，多用户部署可用它切换）；
  · 未配置 → 取 `users` 表里 **id 最小**的用户（=项目默认账号 admin）；
  · 两者都拿不到 → 返回 None，调用方**回退到"不过滤"**（保持旧行为，
    不因本模块故障而让教练整体失效 —— fail-open）。

用法：
    from app.portfolio_scope import portfolio_where
    _w, _p = portfolio_where()
    rows = db.fetch(f"SELECT * FROM user_portfolio {_w} ORDER BY created_at ASC", _p)
================================================================================
"""
import os
import time

_PRIMARY_DEFAULT = 1
_TTL = 300.0
_cache = {"ts": 0.0, "uid": None}


def primary_user_id():
    """本部署的「主用户」id；取不到返回 None（调用方应回退到不过滤）。"""
    now = time.time()
    if _cache["uid"] is not None and now - _cache["ts"] < _TTL:
        return _cache["uid"]
    uid = None
    raw = (os.environ.get("PRIMARY_USER_ID") or "").strip()
    if raw.isdigit():
        uid = int(raw)
    else:
        try:
            from app.database import db
            row = db.fetch_one("SELECT MIN(id) AS uid FROM users")
            v = (row or {}).get("uid")
            if v is not None:
                uid = int(v)
        except Exception:
            uid = None
        if uid is None:
            uid = _PRIMARY_DEFAULT
    _cache.update({"ts": now, "uid": uid})
    return uid


def portfolio_where(alias: str = "") -> tuple:
    """返回 (SQL 片段, 参数)。

    片段形如 `WHERE user_id = %s`（带 alias 时 `WHERE a.user_id = %s`）；
    拿不到主用户时返回 `("", ())` —— 调用方保持原来的全表行为。
    """
    uid = primary_user_id()
    if uid is None:
        return "", ()
    prefix = f"{alias}." if alias else ""
    return f"WHERE {prefix}user_id = %s", (uid,)
