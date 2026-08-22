"""
================================================================================
【文件作用】快讯/信号数据的数据库持久化（移植自 JSON 文件存储）
================================================================================

存储位置：数据库（SQLite 或 PostgreSQL，由 DATABASE_URL 环境变量决定）

设计：
  - 保持与原 JSON 文件相同的函数签名，其他模块无需修改
  - 数据库操作通过 app.database.db 全局单例
  - 自动处理 JSON 序列化/反序列化
================================================================================
"""

import json
import os
from datetime import datetime
from app.database import db

# ── 数据目录（兼容旧代码引用）──
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")

# ── 文件路径映射（兼容 routers/flash.py 的备份/恢复功能）──
PATHS = {
    "flash": os.path.join(DATA_DIR, "flash.json"),
    "analyses": os.path.join(DATA_DIR, "analyses.json"),
    "reviews": os.path.join(DATA_DIR, "reviews.json"),
    "tracking": os.path.join(DATA_DIR, "tracking.json"),
    "macro_history": os.path.join(DATA_DIR, "macro_history.json"),
    "etf_close": os.path.join(DATA_DIR, "etf_close.json"),
    "flash_state": os.path.join(DATA_DIR, "flash_state.json"),
    "schedule_state": os.path.join(DATA_DIR, "schedule_state.json"),
    "strategies": os.path.join(DATA_DIR, "strategies.json"),
}


def _load(path: str, default=None):
    """加载 JSON 文件（兼容旧接口，用于尚未迁移到数据库的数据）"""
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _save(path: str, data):
    """保存 JSON 文件（兼容旧接口）"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    """北京时间 ISO 时间戳（服务器可能跑在 UTC，落盘/展示统一用北京时间）。"""
    from app.flash.rules import beijing_now
    return beijing_now().isoformat()


def _bj_date() -> str:
    """北京时间日期字符串（与旧项目兼容：2026/8/15）。"""
    from app.flash.rules import beijing_now
    n = beijing_now()
    return f"{n.year}/{n.month}/{n.day}"


# ================================================================
#  快讯状态（lastId 游标 + 已推送簇）
# ================================================================

def load_state() -> dict:
    """加载快讯状态"""
    lastId_row = db.fetch_one("SELECT value FROM flash_state WHERE key = %s", ("lastId",))
    pushed_row = db.fetch_one("SELECT value FROM flash_state WHERE key = %s", ("pushedClusters",))
    return {
        "lastId": lastId_row["value"] if lastId_row else "",
        "pushedClusters": json.loads(pushed_row["value"]) if pushed_row else []
    }


def save_state(state: dict) -> None:
    """保存快讯状态"""
    db.upsert("flash_state", {"key": "lastId", "value": state.get("lastId", "")}, conflict_columns=["key"])
    db.upsert("flash_state", {"key": "pushedClusters", "value": json.dumps(state.get("pushedClusters", []))}, conflict_columns=["key"])


def save_raw_data(all_items: list, new_items: list) -> None:
    """原始快讯落盘（保留最近 300 条，按 id 去重，新在前）。"""
    # 获取现有 ID
    existing = db.fetch("SELECT id FROM flash_news")
    existing_ids = {row["id"] for row in existing}
    
    # 过滤新数据
    unique_new = [i for i in new_items if i.get("id") and i["id"] not in existing_ids]
    
    # 插入新数据
    for item in unique_new:
        try:
            db.upsert("flash_news", {
                "id": item.get("id", ""),
                "content": item.get("content", ""),
                "time": item.get("time", ""),
                "cluster": item.get("cluster", ""),
                "is_pushed": 1 if item.get("isPushed") else 0
            }, conflict_columns=["id"])
        except Exception as e:
            print(f"[store] 保存快讯失败: {e}")
    
    # 清理旧数据（保留 300 条）
    db.execute("""
        DELETE FROM flash_news WHERE id NOT IN (
            SELECT id FROM flash_news ORDER BY time DESC LIMIT 300
        )
    """)


def load_raw_items() -> list:
    """加载原始快讯"""
    rows = db.fetch("SELECT * FROM flash_news ORDER BY time DESC LIMIT 300")
    return [{
        "id": r["id"],
        "content": r["content"],
        "time": r["time"],
        "cluster": r.get("cluster"),
        "isPushed": bool(r.get("is_pushed"))
    } for r in rows]


# ================================================================
#  LLM 输出全文落盘
# ================================================================

def save_analysis(analysis: dict, analyzed_clusters: list) -> None:
    """诊断流 LLM 完整输出 + 触发簇摘要，保留最近 50 条。"""
    try:
        db.execute(
            "INSERT INTO flash_analyses (time, model, clusters_json, output_json) "
            "VALUES (%s, %s, %s, %s)",
            (_now_iso(), analysis.get("_model"),
             json.dumps([{
                 "cluster": c.get("_cluster"),
                 "hot": c.get("_clusterHot"),
                 "size": c.get("_clusterSize"),
                 "content": (c.get("content") or "")[:100],
             } for c in analyzed_clusters], ensure_ascii=False),
             json.dumps(analysis, ensure_ascii=False))
        )
        # 保留 50 条
        db.execute("""
            DELETE FROM flash_analyses WHERE id NOT IN (
                SELECT id FROM flash_analyses ORDER BY time DESC LIMIT 50
            )
        """)
    except Exception as e:
        print(f"[store] 保存诊断失败: {e}")


def load_latest_analysis() -> dict:
    """最新一条诊断（无则空 dict）。"""
    row = db.fetch_one("SELECT * FROM flash_analyses ORDER BY time DESC LIMIT 1")
    if not row:
        return {}
    try:
        return json.loads(row["output_json"])
    except (json.JSONDecodeError, KeyError):
        return {}


def save_review(phase: str, analysis_md: str, signals: list) -> dict:
    """复盘流落盘。每个 phase 保留最近 20 条。"""
    entry = {"time": _now_iso(), "markdown": analysis_md, "signals": signals}
    try:
        db.execute(
            "INSERT INTO flash_reviews (phase, markdown, signals_json, time) VALUES (%s, %s, %s, %s)",
            (phase, analysis_md, json.dumps(signals, ensure_ascii=False), entry["time"])
        )
        # 保留 20 条
        db.execute("""
            DELETE FROM flash_reviews WHERE phase = %s AND id NOT IN (
                SELECT id FROM flash_reviews WHERE phase = %s ORDER BY time DESC LIMIT 20
            )
        """, (phase, phase))
    except Exception as e:
        print(f"[store] 保存复盘失败: {e}")
    return entry


def load_review(phase: str) -> dict:
    """加载最新复盘"""
    row = db.fetch_one(
        "SELECT * FROM flash_reviews WHERE phase = %s ORDER BY time DESC LIMIT 1",
        (phase,)
    )
    if not row:
        return {}
    return {
        "time": row["time"],
        "markdown": row["markdown"],
        "signals": json.loads(row["signals_json"]) if row.get("signals_json") else []
    }


def load_review_history(phase: str, limit: int = 20) -> list:
    """加载复盘历史（最新在前），供按日期搜索回溯 LLM 输出。"""
    rows = db.fetch(
        "SELECT * FROM flash_reviews WHERE phase = %s ORDER BY time DESC LIMIT %s",
        (phase, limit)
    )
    out = []
    for row in rows:
        out.append({
            "time": row["time"],
            "markdown": row["markdown"],
            "signals": json.loads(row["signals_json"]) if row.get("signals_json") else []
        })
    return out


# ================================================================
#  宏观历史（趋势上下文用）
# ================================================================

def load_macro_history() -> list:
    """加载宏观历史"""
    rows = db.fetch("SELECT * FROM macro_history ORDER BY time DESC LIMIT 150")
    result = []
    for r in rows:
        try:
            result.append(json.loads(r["data_json"]))
        except (json.JSONDecodeError, KeyError):
            pass
    return list(reversed(result))  # 按时间正序


def append_macro_history(panel: dict) -> None:
    """追加宏观快照到历史（核心资产价格无效则跳过）。"""
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

    try:
        # 检查 3 分钟内是否已有记录（覆盖）
        latest = db.fetch_one("SELECT id, time FROM macro_history ORDER BY time DESC LIMIT 1")
        if latest:
            try:
                last_ts = datetime.fromisoformat(latest["time"]).timestamp()
                if datetime.now().timestamp() - last_ts < 3 * 60:
                    # 覆盖最新记录
                    db.execute(
                        "UPDATE macro_history SET data_json = %s WHERE id = %s",
                        (json.dumps(entry, ensure_ascii=False), latest["id"])
                    )
                    return
            except (ValueError, KeyError):
                pass
        
        # 插入新记录
        db.execute(
            "INSERT INTO macro_history (time, data_json) VALUES (%s, %s)",
            (entry["time"], json.dumps(entry, ensure_ascii=False))
        )
        
        # 保留 150 条
        db.execute("""
            DELETE FROM macro_history WHERE id NOT IN (
                SELECT id FROM macro_history ORDER BY time DESC LIMIT 150
            )
        """)
    except Exception as e:
        print(f"[store] 保存宏观历史失败: {e}")


# ================================================================
#  宏观每日快照（早盘锁定，按日期归档）
# ================================================================

def save_macro_daily(snapshot: dict, date_str: str = None) -> None:
    """保存某日宏观快照（早盘锁定；同日期覆盖，保证一天一份）。"""
    date = date_str or _bj_date()
    try:
        db.upsert("macro_daily", {
            "date": date,
            "data_json": json.dumps(snapshot, ensure_ascii=False)
        }, conflict_columns=["date"])
    except Exception as e:
        print(f"[store] 保存宏观每日快照失败: {e}")


def load_macro_daily(date_str: str = None) -> dict:
    """加载某日宏观快照（默认今日；无则返回空 dict）。"""
    date = date_str or _bj_date()
    row = db.fetch_one("SELECT * FROM macro_daily WHERE date = %s", (date,))
    if not row:
        return {}
    try:
        return json.loads(row["data_json"])
    except (json.JSONDecodeError, KeyError):
        return {}


def load_macro_daily_history(days: int = 30) -> list:
    """加载近 N 日宏观快照（日期正序，最新在后）。"""
    rows = db.fetch("SELECT * FROM macro_daily ORDER BY date DESC LIMIT %s", (days,))
    out = []
    for r in rows:
        try:
            out.append({"date": r["date"], "snapshot": json.loads(r["data_json"])})
        except (json.JSONDecodeError, KeyError):
            pass
    return list(reversed(out))


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
    try:
        db.upsert("etf_close", {
            "date": today,
            "timestamp": entry["timestamp"],
            "holdings_json": json.dumps(entry["holdings"], ensure_ascii=False)
        }, conflict_columns=["date"])
        # 保留 30 天
        db.execute("""
            DELETE FROM etf_close WHERE id NOT IN (
                SELECT id FROM etf_close ORDER BY date DESC LIMIT 30
            )
        """)
    except Exception as e:
        print(f"[store] 保存 ETF 收盘失败: {e}")


def load_etf_close() -> dict:
    """加载最新 ETF 收盘"""
    row = db.fetch_one("SELECT * FROM etf_close ORDER BY date DESC LIMIT 1")
    if not row:
        return {}
    try:
        return {
            "date": row["date"],
            "timestamp": row["timestamp"],
            "holdings": json.loads(row["holdings_json"])
        }
    except (json.JSONDecodeError, KeyError):
        return {}


def load_etf_close_history(days: int = 7) -> list:
    """加载 ETF 收盘历史"""
    rows = db.fetch("SELECT * FROM etf_close ORDER BY date DESC LIMIT %s", (days,))
    result = []
    for r in rows:
        try:
            result.append({
                "date": r["date"],
                "timestamp": r["timestamp"],
                "holdings": json.loads(r["holdings_json"])
            })
        except (json.JSONDecodeError, KeyError):
            pass
    return result


# ================================================================
#  调度状态（复盘"今日已跑"标记）
# ================================================================

def load_schedule_state() -> dict:
    """加载调度状态"""
    rows = db.fetch("SELECT * FROM schedule_state")
    return {"done": {r["task"]: r["done_date"] for r in rows}}


def mark_schedule_done(task: str, date_str: str = None) -> None:
    """标记某任务在某日已执行"""
    date = date_str or _bj_date()
    db.upsert("schedule_state", {"task": task, "done_date": date}, conflict_columns=["task"])


def is_schedule_done(task: str, date_str: str = None) -> bool:
    """检查任务是否已完成"""
    date = date_str or _bj_date()
    row = db.fetch_one(
        "SELECT done_date FROM schedule_state WHERE task = %s",
        (task,)
    )
    return row and row["done_date"] == date
