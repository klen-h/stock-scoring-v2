# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】数据包（Pack）数据源：肥数据的"非 Supabase"读取层（SQLite 版）
================================================================================

背景：Supabase 免费版 egress 5GB/周期被读爆（10.6GB，2026-09-05），
大头是 backtest_prices(71MB)/kline_cache(19MB)/indicator_cache(9MB) 被反复读。
这三类数据本质是"腾讯行情的本地副本"，由 GitHub Actions 每日拉取打包成
SQLite 发 GitHub Pages（零流量费），本模块负责下载与按需查询。

为什么是 SQLite 而不是内存 JSON：45MB JSON 解析成 dict 常驻 150-200MB
（Render 512MB 直接 OOM，2026-09-05 实测）；SQLite 落磁盘按需查单只（<5ms），
常驻内存 ≈0。

三档数据源（环境变量 DATA_SOURCE）：
  db     默认。行为与历史版本完全一致（读 Supabase），不启用本模块逻辑。
  pack   从 GitHub Pages 下载 backend-pack.db.gz（磁盘缓存，30h 新鲜度），读包。
  local  只读本地 backend/data/pack/ 下的包（本地开发零流量；缺文件只警告
         不崩，访问返回 None/[] 由调用方走原有兜底）。

包结构（scripts/generate_backend_pack.py 产出）：
  klines      (code, date, open, high, low, close, volume)  PK(code, date)
  indicators  (code PRIMARY KEY, json)   ← json 内含 _series
  codes       (code PRIMARY KEY, name, market_cap)
  meta        (key PRIMARY KEY, value)   ← pack_date

线程模型：每次查询独立开/关连接（只读，无长事务），多线程安全。
"""

import gzip
import json
import os
import shutil
import sqlite3
import threading
import time

_DATA_SOURCE = (os.environ.get("DATA_SOURCE", "db") or "db").strip().lower()
PACK_URL = (os.environ.get("PACK_URL")
            or "https://klen-h.github.io/stock-scoring-v2/data/backend-pack.db.gz")
_PACK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "pack"))
_PACK_DB = os.path.join(_PACK_DIR, "backend-pack.db")
_PACK_DB_GZ = _PACK_DB + ".gz"
_PACK_MAX_AGE_H = 30          # 超过则视为陈旧（pack 模式自动重下；local 模式只警告）
_DOWNLOAD_TIMEOUT = 180

_lock = threading.Lock()
_ready_checked = False
_load_error = None            # 首次失败的报错（只打一次）


def enabled() -> bool:
    """DATA_SOURCE 处于 pack/local 时返回 True（调用方据此切换读取路径）。"""
    return _DATA_SOURCE in ("pack", "local")


def source_name() -> str:
    return _DATA_SOURCE


def pack_file() -> str:
    return _PACK_DB


def _warn_once(msg):
    global _load_error
    if _load_error != msg:
        _load_error = msg
        # 不用 emoji：Windows GBK 控制台/日志重向下 print(⚠️) 会 UnicodeEncodeError
        print(f"[pack_source] [WARN] {msg}")


def _db_fresh() -> bool:
    if not os.path.exists(_PACK_DB):
        return False
    age_h = (time.time() - os.path.getmtime(_PACK_DB)) / 3600.0
    return age_h <= _PACK_MAX_AGE_H


def _download_and_unpack(bust_cache: bool = True):
    import requests
    os.makedirs(_PACK_DIR, exist_ok=True)
    url = PACK_URL
    if bust_cache:
        # ★ 缓存击穿（2026-09-10 事故）：backend-pack 的 deploy job 推完 gh-pages 后，
        #   Pages 站点部署还有 1~2 分钟空窗，且 CDN（Fastly）默认 max-age=600——
        #   这期间下载会拿到「昨天的包」。日批 20:34 首下就是 09-09 旧包，之后又被
        #   重试 bug 锁死。默认带唯一查询串强制回源（包只在进程启动/重试时下一次）。
        url = f"{url}{'&' if '?' in url else '?'}_={int(time.time())}"
    print(f"[pack_source] 下载数据包: {url}")
    r = requests.get(url, timeout=_DOWNLOAD_TIMEOUT, stream=True)
    r.raise_for_status()
    gz_tmp = _PACK_DB_GZ + ".tmp"
    with open(gz_tmp, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    db_tmp = _PACK_DB + ".tmp"
    # ★ 流式解压：原来的 f_in.read() 会一次性把整个 db（实测 126MB）读进
    #   Python bytes → 内存瞬间 +126MB。Render 免费实例仅 512MB（还要跑
    #   FastAPI + 各定时任务），这个尖峰足以直接 OOM；且临时磁盘每次重启
    #   都要重跑一遍 → 崩溃循环。分块拷贝后内存恒定 ≈1MB。
    with gzip.open(gz_tmp, "rb") as f_in, open(db_tmp, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, 1 << 20)
    os.replace(db_tmp, _PACK_DB)
    os.replace(gz_tmp, _PACK_DB_GZ)
    print(f"[pack_source] 已就绪: {_PACK_DB} ({os.path.getsize(_PACK_DB) / 1048576:.1f} MB)")


def _ensure_ready() -> bool:
    """确保本地 .db 就绪。就绪 True；否则警告一次并 False（调用方走原有兜底）。"""
    global _ready_checked
    if _ready_checked and os.path.exists(_PACK_DB):
        _maybe_refresh()          # ★ 长驻进程周期自检（见函数说明）
        return True
    with _lock:
        if _ready_checked and os.path.exists(_PACK_DB):
            return True
        if _DATA_SOURCE == "local" and not os.path.exists(_PACK_DB):
            _warn_once(f"DATA_SOURCE=local 但本地无数据包：请先运行 "
                       f"`python scripts/sync_local.py`（期望路径 {_PACK_DB}）")
            _ready_checked = True
            return False
        if _DATA_SOURCE == "pack" and not _db_fresh():
            try:
                _download_and_unpack()
            except Exception as e:
                if not os.path.exists(_PACK_DB):
                    _warn_once(f"数据包下载失败且无本地缓存: {e}")
                    _ready_checked = True
                    return False
                _warn_once(f"数据包下载失败（{e}），使用本地陈旧缓存")
        _ready_checked = True
        return os.path.exists(_PACK_DB)


def _query(sql: str, params: tuple = (), fetch: str = "all") -> list:
    """只读查询：独立连接 + 立即关闭（多线程安全，磁盘库常驻内存 ≈0）。"""
    if not _ensure_ready():
        return []
    conn = sqlite3.connect(_PACK_DB)
    conn.row_factory = sqlite3.Row
    try:
        # ★ 限制 SQLite 内存占用：容器的内存统计会算进 page cache / mmap，
        #   126MB 的库若放任 mmap 会明显推高 RSS（512MB 实例扛不住）。
        #   mmap_size=0 → 走普通 read（略慢但内存可控）；cache_size 限 ~8MB。
        try:
            conn.execute("PRAGMA mmap_size=0")
            conn.execute("PRAGMA cache_size=-8000")
            conn.execute("PRAGMA temp_store=FILE")
        except Exception:
            pass
        cur = conn.execute(sql, params)
        return cur.fetchall() if fetch == "all" else cur.fetchone()
    finally:
        conn.close()


def _pack_date() -> str:
    rows = _query("SELECT value FROM meta WHERE key = 'pack_date'", fetch="one")
    if rows:
        return rows["value"] if isinstance(rows, sqlite3.Row) else rows[0]
    return ""


def redownload() -> bool:
    """强制重新下载数据包（删本地 + 缓存击穿），供「等新包发布」的重试循环使用。

    ★ 2026-09-10 事故根因：日批见「数据包日期 != 今天」后只重置了 _ready_checked，
      而 _ensure_ready 用 _db_fresh()（看文件 mtime）判断新鲜度——刚下载过的包 mtime
      就是"刚刚"，永远算新鲜 → 30 分钟重试 10 次一次都没重下，死等旧包跑完全程。
      凡「等待新包发布」的场景必须走这里，不能只置 _ready_checked。
    """
    global _ready_checked, _stale_cache
    with _lock:
        for p in (_PACK_DB, _PACK_DB_GZ):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        try:
            _download_and_unpack(bust_cache=True)
            _ready_checked = True
            _stale_cache = (0.0, False)
            return True
        except Exception as e:
            # 无包可用 → 调用方回退 DB（与首次下载失败同语义）；下次查询会自动重试
            _warn_once(f"强制重新下载数据包失败: {e}")
            _ready_checked = False
            return False


_stale_cache = (0.0, False)   # (上次检查时间, 结果)，10 分钟内复用
_last_fresh_check = 0.0       # 上次"包新鲜度自检"时间（长驻进程周期刷新用）
_FRESH_CHECK_SEC = 1800       # 自检间隔：30 分钟


def _pack_date_raw() -> str:
    """直连 SQLite 读 pack_date（不走 _ensure_ready → 避免递归调用）。"""
    if not os.path.exists(_PACK_DB):
        return ""
    try:
        conn = sqlite3.connect(_PACK_DB)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'pack_date'").fetchone()
        finally:
            conn.close()
    except Exception:
        return ""
    return (row[0] if row else "") or ""


def _parse_pack_date(date_str: str):
    """pack_date → date。兼容 YYYYMMDD（generate_backend_pack 写出的紧凑格式）
    与 YYYY-MM-DD 两种（★ 2026-09-10 前只认后者 → 解析永远失败、陈旧判定失效）。"""
    from datetime import datetime
    if not date_str:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _pack_outdated() -> bool:
    """包日期未覆盖到「上一个工作日」→ True（周末/节假日按工作日近似）。"""
    from datetime import datetime, timedelta
    d = _parse_pack_date(_pack_date_raw())
    if d is None:
        return False
    ref = datetime.now().date()
    for _ in range(15):
        if ref.weekday() < 5:
            break
        ref -= timedelta(days=1)
    return d < (ref - timedelta(days=1))


def _maybe_refresh():
    """长驻进程周期自检：包过期（mtime >30h 或 pack_date 落后 >1 个工作日）就重下。

    ★ 2026-09-10 现场：线上包已是 09-10，而 Render 的 /api/score/kline-cache/status
      返回 newest_update=20260909 —— 因为 _ready_checked 首次置 True 后就短路了
      _db_fresh()，进程不重启就永不复查，整天给前端供旧 K 线/指标（"k线没更新"
      的真相之一）。这里在就绪快路径上挂一个 30 分钟一次的轻量检查（仅看 mtime /
      meta，不查远端），过期才真正重下。
    """
    global _last_fresh_check, _stale_cache
    if _DATA_SOURCE != "pack":
        return
    now = time.time()
    if now - _last_fresh_check < _FRESH_CHECK_SEC:
        return
    _last_fresh_check = now
    if _db_fresh() and not _pack_outdated():
        return
    with _lock:
        try:
            print("[pack_source] 本地包已过期（mtime 或 pack_date 落后），重新下载…")
            _download_and_unpack(bust_cache=True)
            _stale_cache = (0.0, False)
            print(f"[pack_source] 刷新完成: pack_date={_pack_date_raw()}")
        except Exception as e:
            # 刷新失败不改 _ready_checked：继续用旧包（比回退 DB 省流量），
            # 30 分钟后自然再试
            _warn_once(f"过期数据包刷新失败（继续用旧包）: {e}")


def _is_stale() -> bool:
    """pack_date 未覆盖到「上一个工作日」→ 陈旧（Actions 连续失败时读侧回退 DB，
    避免静默用两三天前的指标算分；DB 模式的 36h 过期自愈在 pack 模式靠这里补齐）。
    周末/节假日按工作日近似（与全库其它判断同口径）。"""
    global _stale_cache
    now = time.time()
    ts, val = _stale_cache
    if now - ts < 600:
        return val
    from datetime import datetime, timedelta
    d = _parse_pack_date(_pack_date())
    if d is None:
        _stale_cache = (now, False)
        return False
    ref = datetime.now().date()
    for _ in range(15):
        if ref.weekday() < 5:
            break
        ref -= timedelta(days=1)
    val = d < (ref - timedelta(days=1))
    _stale_cache = (now, val)
    return val


# ────────────────────────── 访问接口（签名与 JSON 版一致） ──────────────────────────

def get_klines(code: str):
    """单只日线（升序 dict 列表，含 date/open/high/low/close/volume）。未命中 None。"""
    if _is_stale():
        return None   # 包明显过期 → 调用方回退 DB/实时，宁缺毋旧
    rows = _query("SELECT date, open, high, low, close, volume FROM klines "
                  "WHERE code = ? ORDER BY date ASC", (code,))
    if not rows:
        return None
    return [{"date": r["date"], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]}
            for r in rows]


def get_prices(code: str, start: str = None, end: str = None) -> list:
    """backtest_prices 口径：未命中返回 []（等价于表里没这只）。"""
    bars = get_klines(code)
    if bars is None:
        return []
    if start:
        bars = [b for b in bars if b["date"] >= start]
    if end:
        bars = [b for b in bars if b["date"] <= end]
    return bars


def get_indicators(code: str):
    """预计算指标（含 _series），形态与 indicator_cache 表内 JSON 一致。"""
    if _is_stale():
        return None   # 包过期 → 调用方回退 DB
    rows = _query("SELECT json FROM indicators WHERE code = ?", (code,), fetch="one")
    if not rows:
        return None
    raw = rows["json"] if isinstance(rows, sqlite3.Row) else rows[0]
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data or None
    except (ValueError, TypeError):
        return None


def get_name_cap(code: str) -> tuple:
    rows = _query("SELECT name, market_cap FROM codes WHERE code = ?", (code,),
                  fetch="one")
    if not rows:
        return "", 0
    if isinstance(rows, sqlite3.Row):
        return rows["name"] or "", rows["market_cap"] or 0
    return rows[0] or "", rows[1] or 0


def get_codes() -> list:
    rows = _query("SELECT code FROM codes ORDER BY code")
    return [r["code"] if isinstance(r, sqlite3.Row) else r[0] for r in rows]


def status() -> dict:
    """概览（供 kline_cache.get_cache_status 在 pack 模式下伪装返回）。"""
    if not _ensure_ready():
        return {"total": 0, "date": ""}
    rows = _query("SELECT COUNT(*) AS n FROM codes", fetch="one")
    total = rows["n"] if isinstance(rows, sqlite3.Row) else (rows[0] if rows else 0)
    return {"total": total, "date": _pack_date()}

