# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】数据包（Pack）数据源：肥数据的"非 Supabase"读取层
================================================================================

背景：Supabase 免费版 egress 5GB/周期被读爆（10.6GB，2026-09-05），
大头是 backtest_prices(71MB)/kline_cache(19MB)/indicator_cache(9MB) 被反复读。
这三类数据本质是"腾讯行情的本地副本"，可随时重拉 —— 故由 GitHub Actions
每日拉取打包发 GitHub Pages（零流量费），本模块负责加载与访问。

三档数据源（环境变量 DATA_SOURCE）：
  db     默认。行为与历史版本完全一致（读 Supabase），不启用本模块逻辑。
  pack   从 GitHub Pages 下载 pack（磁盘缓存，30h 新鲜度），读包。
  local  只读本地 backend/data/pack/ 下的包（本地开发零流量；缺文件只警告
         不崩，访问返回 None 由调用方走原有兜底）。

包结构（scripts/generate_backend_pack.py 产出）：
{
  "version": 1, "date": "2026-09-05",
  "codes":     {"000001": {"name": "...", "market_cap": 123.4}, ...},
  "klines":    {"000001": [["2024-09-01", o, h, l, c, v], ... ≤500 根], ...},
  "indicators":{"000001": {ma5..., "_series": [...]}, ...}
}

内存占用：~800 只 × 500 根 dict ≈ 150MB（Render 512MB 可承受）。
================================================================================
"""

import gzip
import json
import os
import threading
import time

_DATA_SOURCE = (os.environ.get("DATA_SOURCE", "db") or "db").strip().lower()
PACK_URL = (os.environ.get("PACK_URL")
            or "https://klen-h.github.io/stock-scoring-v2/data/backend-pack-latest.json.gz")
_PACK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "pack"))
_PACK_FILE = os.path.join(_PACK_DIR, "backend-pack-latest.json.gz")
_PACK_MAX_AGE_H = 30          # 超过则视为陈旧（pack 模式自动重下；local 模式只警告）
_DOWNLOAD_TIMEOUT = 120

_lock = threading.Lock()
_pack = None                  # {"date": str, "by_code": {code: {...}}}
_load_error = None            # 首次加载失败的报错（只打一次）


def enabled() -> bool:
    """DATA_SOURCE 处于 pack/local 时返回 True（调用方据此切换读取路径）。"""
    return _DATA_SOURCE in ("pack", "local")


def source_name() -> str:
    return _DATA_SOURCE


def pack_file() -> str:
    return _PACK_FILE


def _warn_once(msg):
    global _load_error
    if _load_error != msg:
        _load_error = msg
        print(f"[pack_source] ⚠️ {msg}")


def _local_fresh() -> bool:
    if not os.path.exists(_PACK_FILE):
        return False
    age_h = (time.time() - os.path.getmtime(_PACK_FILE)) / 3600.0
    return age_h <= _PACK_MAX_AGE_H


def _download():
    import requests
    os.makedirs(_PACK_DIR, exist_ok=True)
    print(f"[pack_source] 下载数据包: {PACK_URL}")
    r = requests.get(PACK_URL, timeout=_DOWNLOAD_TIMEOUT, stream=True)
    r.raise_for_status()
    tmp = _PACK_FILE + ".tmp"
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    os.replace(tmp, _PACK_FILE)
    print(f"[pack_source] 已缓存到 {_PACK_FILE} "
          f"({os.path.getsize(_PACK_FILE) / 1048576:.1f} MB)")


def _parse(raw: dict) -> dict:
    """把包里的紧凑数组转成调用方需要的 dict 形态，丢弃中间结构控内存。"""
    by_code = {}
    codes = raw.get("codes") or {}
    for code, bars in (raw.get("klines") or {}).items():
        meta = codes.get(code) or {}
        by_code[code] = {
            "bars": [{"date": b[0], "open": b[1], "high": b[2],
                      "low": b[3], "close": b[4], "volume": b[5]} for b in bars],
            "ind": (raw.get("indicators") or {}).get(code),
            "name": meta.get("name", ""),
            "cap": meta.get("market_cap", 0) or 0,
        }
    return {"date": raw.get("date", ""), "by_code": by_code}


def _load():
    """加载 pack 到内存。local 模式不联网；pack 模式陈旧则自动重下。"""
    global _pack
    if _DATA_SOURCE == "local" and not os.path.exists(_PACK_FILE):
        _warn_once(f"DATA_SOURCE=local 但本地无数据包：请先运行 "
                   f"`python scripts/sync_local.py`（期望路径 {_PACK_FILE}）")
        return None
    if _DATA_SOURCE == "pack" and not _local_fresh():
        try:
            _download()
        except Exception as e:
            if not os.path.exists(_PACK_FILE):
                _warn_once(f"数据包下载失败且无本地缓存: {e}")
                return None
            _warn_once(f"数据包下载失败（{e}），使用本地陈旧缓存")
    try:
        with gzip.open(_PACK_FILE, "rt", encoding="utf-8") as f:
            raw = json.load(f)
        _pack = _parse(raw)
        print(f"[pack_source] 数据包就绪: {_pack['date']}，"
              f"{len(_pack['by_code'])} 只（DATA_SOURCE={_DATA_SOURCE}）")
        return _pack
    except Exception as e:
        _warn_once(f"数据包解析失败: {e}")
        return None


def get_pack():
    """懒加载 + 进程内单例。未启用或加载失败返回 None（调用方走原有兜底）。"""
    if not enabled():
        return None
    if _pack is None:
        with _lock:
            if _pack is None:
                _load()
    return _pack


# ────────────────────────── 访问接口 ──────────────────────────

def get_klines(code: str):
    """单只日线（升序 dict 列表，含 date/open/high/low/close/volume）。未命中 None。"""
    p = get_pack()
    if not p:
        return None
    e = p["by_code"].get(code)
    return e["bars"] if e else None


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
    p = get_pack()
    if not p:
        return None
    e = p["by_code"].get(code)
    return (e or {}).get("ind") or None


def get_name_cap(code: str) -> tuple:
    p = get_pack()
    e = (p or {}).get("by_code", {}).get(code)
    return ((e or {}).get("name", ""), (e or {}).get("cap", 0) or 0)


def get_codes() -> list:
    p = get_pack()
    return sorted((p or {}).get("by_code", {}).keys())


def status() -> dict:
    """概览（供 kline_cache.get_cache_status 在 pack 模式下伪装返回）。"""
    p = get_pack()
    if not p:
        return {"total": 0, "date": ""}
    return {"total": len(p["by_code"]), "date": p["date"]}
