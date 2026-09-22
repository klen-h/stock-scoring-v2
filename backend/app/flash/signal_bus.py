# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】信号总线 —— 「系统今天说了什么」的统一记录与查询（2026-09-23 新增）
================================================================================

为什么需要（用户 2026-09-23 提出「值得整合的功能」）：
  系统有 ~10 个各自独立的推送入口（`health` 数据源告警 / `intraday_alerts` 盘中警示 /
  `scheduler` 午间雷达·日报·周报·转档 / `paper_trading` 模拟盘 / `daily_report` /
  `coach` 纪律与转档 / `mainline` 主线 …），彼此**没有汇总视图** ⇒ 用户（和排查问题的
  我们）只能靠翻企微聊天记录回答「系统今天到底说了什么、有没有漏掉」。

设计（**单点捕获，零侵入**）：
  所有业务推送最终都走 `flash.wechat.push_markdown_batched()` ⇒ 在**那一个函数**里埋
  记录器，就能覆盖全部入口，**不必改 10 个调用方**（改动面 = 1 处）。
  ⚠️ `notify()` 在应用通道失败时会**回落**再调一次 `push_markdown_batched` ⇒ 同一标题
  短窗口内会重复进入 ⇒ 本模块用 (title, content前缀) 做 **90s 去重**。

语义说明（重要，避免误用）：
  · 记录的是「**系统判断要说这件事**」（进入推送函数即记），而不是「一定送到了企微」——
    业务开关关闭 / 未配 webhook 时也会记（页面据此仍能看到系统判断过什么）。
  · 不记录快讯原始条目（那是 `flash` 的领域，量级完全不同）；只记**系统性提示**。
  · 落库失败不影响推送（fail-open，与 `health` / `anomaly` 同类纪律）。

存储：内存环形缓冲（最近 200 条，供 DB 不可用时兜底）+ `push_log` 表（按日期查询）。
================================================================================
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Dict, List, Optional

_MAX_MEM = 200          # 内存环形缓冲上限
_CONTENT_KEEP = 2000    # 落库/返回时的正文截断（页面展示足够）
_DEDUP_SEC = 90.0       # 同标题+同开头正文的去重窗口

_mem: List[Dict] = []
_lock = threading.Lock()
_dedup: Dict[str, float] = {}
_table_ready = False


def _ensure_table() -> None:
    """建表（幂等；跨 PG/SQLite 兼容 —— 不用自增主键，id 由时间戳+标题哈希生成）。"""
    global _table_ready
    if _table_ready:
        return
    try:
        from app.database import db
        db.execute(
            "CREATE TABLE IF NOT EXISTS push_log ("
            "id VARCHAR(80) PRIMARY KEY, "
            "date VARCHAR(10), ts VARCHAR(19), "
            "title VARCHAR(300), content TEXT, "
            "category VARCHAR(20), force_flag INTEGER, "
            "created_at VARCHAR(30))")
        _table_ready = True
    except Exception as e:
        print(f"[signal_bus] 建表失败（不影响推送）: {e}")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def record(title: str, content: str = "", category: Optional[str] = None,
           force: bool = False) -> None:
    """记录一条系统提示（由 `push_markdown_batched` 单点调用）。

    ★ 必须**极致健壮**：任何异常都吞掉 —— 记录失败绝不能影响推送本身（那才是主业）。
    """
    try:
        title = (title or "").strip()
        if not title:
            return
        body = content or ""
        now = time.time()
        key = hashlib.md5((title + body[:80]).encode("utf-8")).hexdigest()[:12]
        with _lock:
            # 去重（notify 回落会二次进入；同标题短时间重复推送也归并）
            last = _dedup.get(key)
            if last and now - last < _DEDUP_SEC:
                return
            _dedup[key] = now
            if len(_dedup) > 400:      # 防字典无限增长
                for k in [k for k, v in _dedup.items() if now - v > _DEDUP_SEC]:
                    _dedup.pop(k, None)
            ts = _now()
            item = {"ts": ts, "date": ts[:10], "title": title,
                    "content": body[:_CONTENT_KEEP],
                    "category": (category or "").strip() or None,
                    "force": bool(force)}
            _mem.append(item)
            if len(_mem) > _MAX_MEM:
                del _mem[:len(_mem) - _MAX_MEM]
        # 落库（fail-open）
        try:
            _ensure_table()
            from app.database import db
            rid = "%s-%s" % (ts.replace(" ", "T"), key)
            existing = db.fetch_one("SELECT id FROM push_log WHERE id = %s", (rid,))
            if not existing:
                db.execute(
                    "INSERT INTO push_log (id, date, ts, title, content, category, "
                    "force_flag, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (rid, item["date"], item["ts"], title, item["content"],
                     item["category"], 1 if item["force"] else 0, ts))
        except Exception as e:
            print(f"[signal_bus] 落库失败（仅内存可见）: {e}")
    except Exception as e:
        print(f"[signal_bus] 记录异常（已忽略）: {e}")


def by_date(date: Optional[str] = None, limit: int = 80) -> List[Dict]:
    """某日全部系统提示（默认今天），按时间倒序。DB 优先，失败回退内存。"""
    target = date or time.strftime("%Y-%m-%d")
    out: List[Dict] = []
    try:
        _ensure_table()
        from app.database import db
        rows = db.fetch(
            "SELECT ts, title, content, category, force_flag FROM push_log "
            "WHERE date = %s ORDER BY ts DESC LIMIT %s", (target, int(limit)))
        for r in rows or []:
            out.append({"ts": r.get("ts"), "title": r.get("title"),
                        "content": r.get("content"),
                        "category": r.get("category"),
                        "force": bool(r.get("force_flag"))})
        if out:
            return out
    except Exception as e:
        print(f"[signal_bus] 查询落库失败（回退内存）: {e}")
    with _lock:
        for x in reversed([m for m in _mem if m["date"] == target]):
            out.append({"ts": x["ts"], "title": x["title"], "content": x["content"],
                        "category": x["category"], "force": x["force"]})
    return out[:limit]


def recent_dates(days: int = 7) -> List[str]:
    """最近有记录的日期（供前端切日）。"""
    try:
        _ensure_table()
        from app.database import db
        rows = db.fetch("SELECT DISTINCT date FROM push_log ORDER BY date DESC LIMIT %s",
                        (int(days),))
        return [r["date"] for r in rows or []]
    except Exception:
        with _lock:
            return sorted({m["date"] for m in _mem}, reverse=True)[:days]


def stats(date: Optional[str] = None) -> Dict:
    """当日统计（按 category 分组计数）—— 供前端顶部概览。"""
    items = by_date(date)
    by_cat: Dict[str, int] = {}
    for x in items:
        c = x.get("category") or "other"
        by_cat[c] = by_cat.get(c, 0) + 1
    return {"date": date or time.strftime("%Y-%m-%d"), "total": len(items),
            "by_category": by_cat}
