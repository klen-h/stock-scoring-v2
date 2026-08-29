"""
================================================================================
【文件作用】回测 API：GET /api/backtest/strategy?name=xxx（引擎计算 + 10 分钟缓存）
================================================================================
name 取值：
  - signals：LLM 信号绩效追踪（已落盘交易统计）
  - warfare：战法选股回测（全体战法，含前70%/后30%切分）
  - macro  ：宏观方向分回测

实现要点：
  - 计算耗时（战法需加载几百只个股日线），结果内存缓存 10 分钟
  - 响应裁剪：逐笔交易只回 Top10（按单笔收益绝对值），净值曲线只回概要
================================================================================
"""

import json
import os
import threading
import time
from typing import Dict, Optional

from fastapi import APIRouter, Query

from app.backtest import strategies
from app.database import db

router = APIRouter()

_CACHE_TTL = 600          # 结果缓存 10 分钟
_cache = {}               # {name: (ts, result_json)}

_STRATEGY_NAMES = {"signals", "warfare", "macro"}


# ── DB 持久缓存：Render 免费档 15 分钟无流量会休眠，重启后内存缓存丢失，
#    冷计算需从 Supabase 拉几百只日线（30s+ 易超时）。持久缓存让冷启动秒回 + 后台刷新。

def _init_cache_table():
    db.execute("""
        CREATE TABLE IF NOT EXISTS backtest_cache (
            strategy TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)


_init_cache_table()


def _cache_save(name: str, resp: dict):
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        db.execute("""
            INSERT INTO backtest_cache (strategy, data_json, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (strategy) DO UPDATE SET
                data_json = EXCLUDED.data_json,
                updated_at = EXCLUDED.updated_at
        """, (name, json.dumps(resp, ensure_ascii=False), now))
    except Exception as e:
        print(f"[backtest] 持久缓存写入失败 {name}: {e}")


def _cache_load(name: str) -> Optional[Dict]:
    try:
        row = db.fetch_one(
            "SELECT data_json, updated_at FROM backtest_cache WHERE strategy = %s", (name,))
        if not row:
            return None
        data = json.loads(row["data_json"])
        try:
            from datetime import datetime
            age = (datetime.now() - datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
            data["stale_hours"] = round(age / 3600, 1)
        except ValueError:
            pass
        return data
    except Exception:
        return None


def _trade_light(t: dict) -> dict:
    """逐笔交易裁剪：去掉每日收益路径，只留展示字段。"""
    return {k: v for k, v in t.items() if k != "daily"}


def _curve_summary(curve: list) -> dict:
    """净值曲线概要（前端画不了全曲线也要有数字可看）。"""
    if not curve:
        return None
    first, last = curve[0], curve[-1]
    peak = max(curve, key=lambda c: c["nav"])
    # 最大回撤段：峰值日 → 之后最低点日
    mdd_pair, peak_nav = None, curve[0]
    for c in curve:
        if c["nav"] > peak_nav["nav"]:
            peak_nav = c
        ratio = c["nav"] / peak_nav["nav"]
        if mdd_pair is None or ratio < mdd_pair[0]:
            mdd_pair = (ratio, peak_nav["date"], c["date"])
    return {
        "start": first["date"], "end": last["date"],
        "days": len(curve),
        "start_nav": round(first["nav"], 4), "end_nav": round(last["nav"], 4),
        "peak_date": peak["date"], "peak_nav": round(peak["nav"], 4),
        "mdd_from": mdd_pair[1], "mdd_to": mdd_pair[2],
        "mdd_ratio": round(mdd_pair[0] - 1, 4),
    }


def _build_response(name: str, result: dict) -> dict:
    """统一响应结构（裁剪大字段）。"""
    resp = {
        "type": name,
        "label": result.get("label", name),
        "sample_note": result.get("sample_note", ""),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if name == "signals":
        resp["total"] = result.get("total")
        resp["by_source"] = result.get("by_source", {})
        return resp
    resp["metrics"] = result.get("metrics")
    if name == "warfare":
        resp["by_strategy"] = result.get("by_strategy") or {}
        resp["top_trades"] = [_trade_light(t) for t in
                              sorted(result.get("trades") or [],
                                     key=lambda t: abs(t["pnl_pct"]),
                                     reverse=True)[:10]]
        resp["curve_summary"] = _curve_summary(result.get("curve") or [])
        for key in ("in_sample", "out_sample"):
            part = result.get(key)
            if part:
                resp[key] = {
                    "label": part.get("label"),
                    "metrics": part.get("metrics"),
                    "sample_note": part.get("sample_note"),
                }
    elif name == "macro":
        resp["curve_summary"] = _curve_summary(result.get("curve") or [])
    return resp


@router.get("/strategy")
def get_strategy(name: str = Query(..., description="signals / warfare / macro")):
    """计算并返回某策略回测结果。

    三层缓存：内存 10 分钟 → DB 持久缓存（秒回 stale + 后台刷新）→ 同步计算（仅首次部署）。"""
    if name not in _STRATEGY_NAMES:
        return {"error": f"未知策略 {name}，可选: {sorted(_STRATEGY_NAMES)}"}
    cached = _cache.get(name)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        resp = cached[1]
        resp["cached"] = True
        return resp

    # DB 持久缓存：有就秒回（标 stale），后台线程刷新（stale-while-revalidate）
    persisted = _cache_load(name)
    if persisted:
        resp = persisted
        resp["cached"] = True
        # 内存仅存 60s：后台刷新失败时，下个请求仍会重试刷新
        _cache[name] = (time.time() - _CACHE_TTL + 60, resp)
        threading.Thread(target=_refresh_background, args=(name,), daemon=True).start()
        return resp

    # 无任何缓存（首次部署/清库）：同步计算
    return _compute_and_cache(name)


def _compute_and_cache(name: str) -> dict:
    """同步计算 + 写内存缓存 + 写 DB 持久缓存。"""
    start = time.time()
    if name == "signals":
        result = strategies.backtest_llm_signals()
    elif name == "warfare":
        result = strategies.backtest_warfare()
    else:
        result = strategies.backtest_macro()
    resp = _build_response(name, result)
    resp["compute_seconds"] = round(time.time() - start, 2)
    _cache[name] = (time.time(), resp)
    _cache_save(name, resp)
    return resp


def _refresh_background(name: str):
    """后台刷新持久缓存。"""
    try:
        _compute_and_cache(name)
        print(f"[backtest] 后台刷新 {name} 完成")
    except Exception as e:
        print(f"[backtest] 后台刷新 {name} 失败: {e}")


def preheat_all():
    """预热三类策略回测（scheduler 盘后调用），写内存 + DB 持久缓存。"""
    for name in sorted(_STRATEGY_NAMES):
        try:
            _compute_and_cache(name)
            print(f"[backtest] 预热 {name} 完成")
        except Exception as e:
            print(f"[backtest] 预热 {name} 失败: {e}")


# ── 周度回测报告归档（scheduler 每周五生成的 markdown 文件）──────────────

@router.get("/reports")
def list_reports():
    """列出已生成的回测报告归档（新→旧；latest.md 为副本不重复列出）。"""
    from app.backtest.run import REPORT_DIR
    if not os.path.isdir(REPORT_DIR):
        return {"reports": []}
    items = []
    for fname in os.listdir(REPORT_DIR):
        if not fname.endswith(".md") or fname == "latest.md":
            continue
        path = os.path.join(REPORT_DIR, fname)
        items.append({
            "name": fname,
            "size": os.path.getsize(path),
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"reports": items}


@router.get("/reports/content")
def get_report_content(name: str = Query(..., description="报告文件名，如 latest.md")):
    """返回指定报告的 markdown 原文（前端 markdown-it 渲染）。

    安全：basename 校验防路径穿越，只允许 .md 纯文件名。"""
    from app.backtest.run import REPORT_DIR
    safe = os.path.basename(name)
    if not safe.endswith(".md") or safe != name:
        return {"error": "非法文件名"}
    path = os.path.join(REPORT_DIR, safe)
    if not os.path.isfile(path):
        return {"error": f"报告不存在: {safe}"}
    with open(path, "r", encoding="utf-8") as f:
        return {"name": safe, "content": f.read()}
