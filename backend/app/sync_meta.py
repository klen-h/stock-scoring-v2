"""
================================================================================
【文件作用】数据集版本号（sync_meta 表）——给"每天只变一次"的远端数据做版本门控
================================================================================
要解决的问题（2026-09-12，egress 治理第二步）：
  `mainflow_history` 的整表读是 8.7MB/次，原先只靠 30 分钟 TTL 兜着：
    · 资金流每天 19:00 回填一次 → TTL 到期后的重拉纯属白烧 egress（4.7 次/天）；
    · 生产方（GitHub Actions 日批）与消费方（Render 常驻进程）是两个进程，
      内存缓存互不可见，只能靠"猜时间"，既浪费又可能读到旧数据。

机制（比 TTL 更省也更准）：
  · 生产方写完数据 → `touch(key)` 打一个版本号（就用 updated_at）
  · 消费方缓存数据 + 记住当时的版本号；TTL 到期时**先查版本号**（十几字节的一行）
      - 版本没变 → 直接续期，不重拉（数据没变 = 0 流量）
      - 版本变了 → 重拉一次（数据一变立刻可见，不用等 TTL）
      - 版本表不可用（空表/库异常）→ 退化为长 TTL 兜底，绝不因它报错

对外函数：
  touch(key, rows=None, note="")   生产方打版本（失败静默，不影响主流程）
  version(key) -> str | None       版本号（进程内 30s 缓存，避免高频查询）
  versions() -> dict               全部版本（排障/状态接口用）
================================================================================
"""

import threading
import time
from datetime import datetime

from app.database import db

_READY = False
_LOCK = threading.Lock()
_VER_CACHE = {}                 # {key: (ts, version)}，进程内 30s 缓存
_VER_CACHE_TTL = 30


def _ensure_table():
    global _READY
    if _READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS sync_meta (
            key TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            rows BIGINT,
            note TEXT
        )
    """)
    _READY = True


def touch(key: str, rows: int = None, note: str = "") -> None:
    """标记某数据集已更新（版本号 = 当前时间）。失败静默：生产方不能因它失败。"""
    global _VER_CACHE
    now = datetime.now().isoformat()
    try:
        _ensure_table()
        db.upsert("sync_meta",
                  {"key": key, "updated_at": now, "rows": rows, "note": (note or "")[:200]},
                  conflict_columns=["key"])
        with _LOCK:
            _VER_CACHE.pop(key, None)      # 本进程立即看到新版本
        print(f"[sync_meta] {key} 版本更新: {now[:19]} (rows={rows})")
    except Exception as e:
        print(f"[sync_meta] {key} 版本写入失败（不影响数据本身）: {e}")


def version(key: str) -> str:
    """数据集当前版本号；表不可用/无记录返回 None（调用方退化为长 TTL）。"""
    now = time.time()
    with _LOCK:
        hit = _VER_CACHE.get(key)
        if hit and now - hit[0] <= _VER_CACHE_TTL:
            return hit[1]
    ver = None
    try:
        _ensure_table()
        row = db.fetch_one("SELECT updated_at FROM sync_meta WHERE key = %s", (key,))
        ver = (row or {}).get("updated_at") or None
    except Exception as e:
        print(f"[sync_meta] 版本读取失败（退化长 TTL）: {e}")
    with _LOCK:
        _VER_CACHE[key] = (now, ver)
    return ver


def versions() -> dict:
    """全部版本号（状态接口/排障）。失败返回 {}。"""
    try:
        _ensure_table()
        rows = db.fetch("SELECT key, updated_at, rows, note FROM sync_meta")
        return {r["key"]: {"updated_at": r.get("updated_at"), "rows": r.get("rows"),
                           "note": r.get("note")} for r in (rows or [])}
    except Exception as e:
        print(f"[sync_meta] 版本清单读取失败: {e}")
        return {}
