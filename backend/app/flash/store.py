"""
================================================================================
【文件作用】快讯/信号数据的 JSON 文件持久化（移植自 storage.js + macro-history.js）
================================================================================

存储位置：backend/data/*.json（data 目录已被 .gitignore，可放任意结构化状态）。
与 flash-monitor 的 public/data/*.json 结构兼容——把旧项目的数据文件直接拷到
backend/data/ 即可无缝续用（状态游标、宏观历史、信号跟踪都能接上）。

设计：
  - 线程锁 + 原子写（先写 .tmp 再 rename），防并发写坏文件
  - 所有 load_* 对损坏/缺失文件返回安全默认值，绝不抛异常
  - 【修复原项目缺陷】LLM 完整输出落盘（analyses.json / reviews.json），
    这是回测 LLM 表现的前提
================================================================================
"""

import json
import os
import threading
from datetime import datetime

# data 目录 = backend/data（app/flash/store.py → 上三级 = backend）
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))

PATHS = {
    "state":       os.path.join(DATA_DIR, "flash_state.json"),    # {lastId, pushedClusters}
    "raw":         os.path.join(DATA_DIR, "flash.json"),          # {date, items(≤300)}
    "analyses":    os.path.join(DATA_DIR, "analyses.json"),       # LLM 诊断全文（≤50）
    "reviews":     os.path.join(DATA_DIR, "reviews.json"),        # 三段复盘全文
    "macro_hist":  os.path.join(DATA_DIR, "macro_history.json"),  # 宏观历史（≤150）
    "etf_close":   os.path.join(DATA_DIR, "etf_close.json"),      # ETF 日收盘（≤30天）
    "tracking":    os.path.join(DATA_DIR, "tracking.json"),       # 信号跟踪状态机
    "schedule":    os.path.join(DATA_DIR, "schedule_state.json"), # 调度器"今日已跑"标记
}

_io_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now().isoformat()


def _bj_date() -> str:
    """北京时间日期字符串（zh-CN 格式，与旧项目兼容：2026/8/15）。"""
    from app.flash.rules import beijing_now
    n = beijing_now()
    return f"{n.year}/{n.month}/{n.day}"


def _load(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _save(path: str, data) -> None:
    """原子写：先写临时文件再替换，避免写一半崩溃损坏数据。"""
    with _io_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


# ================================================================
#  快讯状态（lastId 游标 + 已推送簇）
# ================================================================

def load_state() -> dict:
    return _load(PATHS["state"], {"lastId": "", "pushedClusters": []})


def save_state(state: dict) -> None:
    _save(PATHS["state"], state)


def save_raw_data(all_items: list, new_items: list) -> None:
    """原始快讯落盘（保留最近 300 条，按 id 去重，新在前）。"""
    history = _load(PATHS["raw"], {"date": "", "items": []})
    existing_ids = {i["id"] for i in history.get("items", [])}
    unique_new = [i for i in new_items if i["id"] not in existing_ids]
    history["items"] = unique_new + history.get("items", [])
    history["items"] = history["items"][:300]
    history["date"] = _bj_date()
    history["lastUpdated"] = _now_iso()
    _save(PATHS["raw"], history)


def load_raw_items() -> list:
    return _load(PATHS["raw"], {"items": []}).get("items", [])


# ================================================================
#  LLM 输出全文落盘（修复原项目不落盘的缺陷）
# ================================================================

def save_analysis(analysis: dict, analyzed_clusters: list) -> None:
    """诊断流 LLM 完整输出 + 触发簇摘要，保留最近 50 条。"""
    history = _load(PATHS["analyses"], {"analyses": []})
    history["analyses"].insert(0, {
        "time": _now_iso(),
        "model": analysis.get("_model"),
        "clusters": [{
            "cluster": c.get("_cluster"),
            "hot": c.get("_clusterHot"),
            "size": c.get("_clusterSize"),
            "content": (c.get("content") or "")[:100],
        } for c in analyzed_clusters],
        "output": analysis,       # ★ 完整 JSON 输出
    })
    history["analyses"] = history["analyses"][:50]
    _save(PATHS["analyses"], history)


def load_latest_analysis() -> dict:
    """最新一条诊断（无则空 dict）。"""
    analyses = _load(PATHS["analyses"], {"analyses": []}).get("analyses", [])
    return analyses[0] if analyses else {}


def save_review(phase: str, analysis_md: str, signals: list) -> dict:
    """复盘流落盘：{phase, markdown, signals, time}。每个 phase 保留最近 20 条。"""
    history = _load(PATHS["reviews"], {})
    entry = {"time": _now_iso(), "markdown": analysis_md, "signals": signals}
    history.setdefault(phase, []).insert(0, entry)
    history[phase] = history[phase][:20]
    _save(PATHS["reviews"], history)
    return entry


def load_review(phase: str) -> dict:
    reviews = _load(PATHS["reviews"], {}).get(phase, [])
    return reviews[0] if reviews else {}


# ================================================================
#  宏观历史（趋势上下文用）
# ================================================================

def load_macro_history() -> list:
    return _load(PATHS["macro_hist"], [])


def append_macro_history(panel: dict) -> None:
    """
    追加宏观快照到历史（核心资产价格无效则跳过；3 分钟内重复则覆盖；
    只保留最近 150 条）。移植自 macro-history.js。
    """
    # 用面板数据（键名与旧项目历史格式对齐：wti/nke/us10yt/usdcnh…）
    core_assets = ["brent", "wti", "gold", "nasdaq", "dxy"]
    if any(not (panel.get(k) or {}).get("price") for k in core_assets):
        return

    def _e(key):
        item = panel.get(key) or {}
        return {"price": item.get("price"), "change": item.get("change_pct")}

    d = panel.get("_derived", {})
    entry = {
        "time": _now_iso(),
        "brent": _e("brent"), "wti": _e("wti"), "gold": _e("gold"), "gld": _e("gld"),
        "us10yt": _e("us10y"), "silver": _e("silver"), "copper": _e("copper"),
        "nasdaq": _e("nasdaq"), "nke": _e("nikkei"), "hstech": _e("hstech"),
        "dxy": _e("dxy"), "usdcnh": _e("usdcnh"),
        "copperOilRatio": d.get("copper_oil_ratio"),
        "goldSilverRatio": d.get("gold_silver_ratio"),
        "gldRatio": d.get("gold_oil_ratio"),
        "copperGoldRatio": d.get("copper_gold_ratio"),
    }

    history = load_macro_history()
    if history:
        try:
            last_ts = datetime.fromisoformat(history[-1]["time"]).timestamp()
            if datetime.now().timestamp() - last_ts < 3 * 60:
                history[-1] = entry
            else:
                history.append(entry)
        except (ValueError, KeyError):
            history.append(entry)
    else:
        history.append(entry)
    _save(PATHS["macro_hist"], history[-150:])


# ================================================================
#  ETF 收盘历史
# ================================================================

def save_etf_close(holdings: list) -> None:
    """保存今日 ETF 收盘快照（按北京日期去重，保留 30 天）。"""
    today = _bj_date()
    entry = {
        "date": today, "timestamp": _now_iso(),
        "holdings": [{"name": h["name"], "code": h["code"], "price": h["price"],
                      "prevClose": h["prevClose"], "change": h["change"],
                      "changeStr": h["changeStr"]} for h in holdings],
    }
    history = _load(PATHS["etf_close"], [])
    if isinstance(history, list):
        history = [d for d in history if d.get("date") != today]
    else:
        history = []
    history.insert(0, entry)
    _save(PATHS["etf_close"], history[:30])


def load_etf_close() -> dict:
    history = _load(PATHS["etf_close"], [])
    return history[0] if history else {}


def load_etf_close_history(days: int = 7) -> list:
    history = _load(PATHS["etf_close"], [])
    return history[:days] if isinstance(history, list) else []


# ================================================================
#  调度状态（复盘"今日已跑"标记）
# ================================================================

def load_schedule_state() -> dict:
    return _load(PATHS["schedule"], {"done": {}})


def mark_schedule_done(task: str, date_str: str = None) -> None:
    """标记某任务在某日已执行（用于复盘的每日一次语义）。"""
    st = load_schedule_state()
    st["done"][task] = date_str or _bj_date()
    _save(PATHS["schedule"], st)


def is_schedule_done(task: str, date_str: str = None) -> bool:
    st = load_schedule_state()
    return st.get("done", {}).get(task) == (date_str or _bj_date())
