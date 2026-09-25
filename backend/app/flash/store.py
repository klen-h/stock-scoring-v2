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

import copy
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
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
    """北京时间 ISO 时间戳（服务器可能跑在 UTC，落盘/展示统一用北京时间）。

    ★ 2026-09-12：改为直接复用 rules.beijing_now_iso()，全项目时间戳只有这一个来源。
    """
    from app.flash.rules import beijing_now_iso
    return beijing_now_iso()


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
    # ★ 2026-09-11 egress：没有新快讯就直接返回——此前每轮轮询都先查一次
    #   现有 ID 全表（实测 401 次/天、238 万行/21天），而绝大多数轮询并无新条目。
    if not new_items:
        return
    # 获取现有 ID（只取近 3 天：表本身只保留最近 300 条）
    try:
        cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        existing = db.fetch("SELECT id FROM flash_news WHERE time >= %s", (cutoff,))
    except Exception:
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
    _RAW_CACHE["ts"] = 0.0      # ★ 写入即失效缓存（下面 load_raw_items 的 TTL 缓存）


# ★ 进程内短缓存（2026-09-11 egress 治理）：load_raw_items 是「整行含正文」的
#   300 条查询，21 天里被调 6534 次、返回 187 万行（≈ 每天 30MB Supabase 流量）——
#   快讯轮询/诊断/前端轮询反复读同一批内容。写入侧（save_raw_data）会立刻失效，
#   所以缓存窗口内不会漏掉新快讯。
_RAW_CACHE = {"ts": 0.0, "data": []}
_RAW_TTL = 120


def load_raw_items() -> list:
    """加载原始快讯"""
    if _RAW_CACHE["data"] and time.time() - _RAW_CACHE["ts"] <= _RAW_TTL:
        return _RAW_CACHE["data"]
    rows = db.fetch("SELECT id, content, time, cluster, is_pushed FROM flash_news "
                    "ORDER BY time DESC LIMIT 300")
    data = [{
        "id": r["id"],
        "content": r["content"],
        "time": r["time"],
        "cluster": r.get("cluster"),
        "isPushed": bool(r.get("is_pushed"))
    } for r in rows]
    _RAW_CACHE["ts"] = time.time()
    _RAW_CACHE["data"] = data
    return data


def load_raw_items_by_ids(ids: list) -> dict:
    """按 id 取快讯原文，返回 {id: item}（id 统一成字符串）。

    ★ 2026-09-12（egress 治理）：LLM 提示词构造（format_cluster_text）原来整取
      300 条（约 210KB）只为引用其中几条 lastUpdateId → 改为按需取（单条 ~700B）。
    """
    want = [str(i) for i in (ids or []) if i not in (None, "")]
    if not want:
        return {}
    rows = db.fetch("SELECT id, content, time, cluster, is_pushed FROM flash_news "
                    "WHERE id = ANY(%s)", (list(dict.fromkeys(want)),))
    return {str(r["id"]): {
        "id": str(r["id"]),
        "content": r["content"],
        "time": r["time"],
        "cluster": r.get("cluster"),
        "isPushed": bool(r.get("is_pushed")),
    } for r in rows}


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


def load_analyses(limit: int = 20, since: str = "") -> list:
    """加载 LLM 诊断历史（最新在前），每条含 {time, model, clusters, output}。

    ★ 数据源必须是数据库表 flash_analyses：save_analysis() 早已迁移到 DB，
      但路由里有多处仍在读迁移前的 data/analyses.json——那个文件停留在迁移
      当天再没被写入，导致「今日诊断」永远显示十几天前的旧诊断（新诊断写进了
      表、接口却读文件）。读 JSON 的旧调用全部改走这里。

    ★ 2026-09-12（egress 治理②）：加 since 下推。通知轮询（每分钟一次）原来固定
      拉 20 条含 output_json 的完整诊断（每次约 50KB，实测 21 天被调 1.5 万次），
      而它只用得到「比 since 新」的那几条。过滤推到 SQL 后，绝大多数轮询返回 0 行。
    """
    if since:
        # ★ 语义等价的关键：先按原口径取最新 limit 条（内层），再按 since 过滤（外层）。
        #   直接写成 WHERE time > %s LIMIT limit 会在「最近 8 小时内不足 limit 条」时
        #   把更旧的记录也带出来（与旧行为不一致）。
        rows = db.fetch(
            "SELECT time, model, clusters_json, output_json FROM ("
            "  SELECT time, model, clusters_json, output_json FROM flash_analyses "
            "  ORDER BY time DESC LIMIT %s) t "
            "WHERE time > %s ORDER BY time DESC", (limit, since))
    else:
        rows = db.fetch(
            "SELECT time, model, clusters_json, output_json FROM flash_analyses "
            "ORDER BY time DESC LIMIT %s", (limit,))
    out = []
    for r in rows:
        try:
            output = json.loads(r["output_json"]) if r.get("output_json") else {}
        except (ValueError, TypeError):
            output = {}
        try:
            clusters = json.loads(r["clusters_json"]) if r.get("clusters_json") else []
        except (ValueError, TypeError):
            clusters = []
        out.append({"time": r["time"], "model": r.get("model"),
                    "clusters": clusters, "output": output})
    return out


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


def load_review_history(phase: str, limit: int = 20, since: str = "") -> list:
    """加载复盘历史（最新在前），供按日期搜索回溯 LLM 输出。

    ★ 2026-09-12（egress 治理②）：加 since 下推 —— 通知轮询固定拉含 markdown
      正文的复盘（3KB/条，实测 4.7 万次调用），而它只需要「比 since 新」的那条。
    """
    if since:
        # ★ 同 load_analyses：先取最新 limit 条，再过滤（语义与旧实现完全一致）
        rows = db.fetch(
            "SELECT time, markdown, signals_json FROM ("
            "  SELECT time, markdown, signals_json FROM flash_reviews "
            "  WHERE phase = %s ORDER BY time DESC LIMIT %s) t "
            "WHERE time > %s ORDER BY time DESC", (phase, limit, since)
        )
    else:
        rows = db.fetch(
            "SELECT time, markdown, signals_json FROM flash_reviews "
            "WHERE phase = %s ORDER BY time DESC LIMIT %s",
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

# ★ 进程内缓存（2026-09-12 egress 治理④）：宏观历史整段（150 条 / 约 42KB）被
#   LLM 趋势上下文等路径反复整份读（实测 6,496 次 ≈ 8~13MB/天）。宏观快照每 3 分钟
#   才写一次，300 秒缓存 + 写入侧失效足够；调用方只读不写（llm.py 趋势上下文）。
_MACRO_CACHE = {"ts": 0.0, "data": []}
_MACRO_TTL = 300


def load_macro_history() -> list:
    """加载宏观历史（按时间正序）。

    ★ 2026-09-12：加 300 秒进程缓存 + 写入侧失效（append_macro_history）。
    """
    if _MACRO_CACHE["data"] and time.time() - _MACRO_CACHE["ts"] <= _MACRO_TTL:
        return _MACRO_CACHE["data"]
    rows = db.fetch("SELECT data_json FROM macro_history ORDER BY time DESC LIMIT 150")
    result = []
    for r in rows:
        try:
            result.append(json.loads(r["data_json"]))
        except (json.JSONDecodeError, KeyError):
            pass
    out = list(reversed(result))  # 按时间正序
    _MACRO_CACHE["ts"] = time.time()
    _MACRO_CACHE["data"] = out
    return out


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
        # ★ 2026-09-25 新增两项（纯新增，旧记录无此键 ⇒ 消费方须按"缺失跳过"处理）：
        #   · `a50` —— 新加坡富时 A50 期货，是**对 A 股最直接**的外盘品种，原先竟然没存
        #     （而它恰恰是算"自 A 股收盘以来外盘变了多少"最该看的那个）。
        #   · `vix` —— 全球风险偏好/恐慌度，风控用。
        "a50": _e("a50"), "vix": _e("vix"),
        # ★ 2026-09-25（用户需求「中概/美股现货」）：必须**显式加进历史** ——
        #   本函数是**手工白名单**（不是自动全量面板）。若只加 `macro._PANEL_MAP`，
        #   面板会有数据、而"自 A 股收盘以来的隔夜变化"（`macro._OVERNIGHT_KEYS`）
        #   因取不到基准而**静默跳过** —— 这类"面板有、隔夜没有"的缺失最难发现。
        #   ⚠️ 冷启动：今天起的记录才有这些键 ⇒ 隔夜块**下一个交易日 15:03 后**才生效。
        "us_dji": _e("us_dji"), "us_spx": _e("us_spx"),
        "us_ixic": _e("us_ixic"), "cn_hxc": _e("cn_hxc"),
        "copperOilRatio": d.get("copper_oil_ratio"),
        "goldSilverRatio": d.get("gold_silver_ratio"),
        "gldRatio": d.get("gold_oil_ratio"),
        "copperGoldRatio": d.get("copper_gold_ratio"),
    }

    try:
        # 检查 3 分钟内是否已有记录（覆盖）
        _MACRO_CACHE["ts"] = 0.0        # ★ 写入即失效读缓存（下面两处写路径共用）
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
    """检查任务是否已完成。
    ★ DB 查询失败按「未完成」处理（fail-open）：窗口循环下个周期会重试，
      宁可极小概率多跑一次，也不要让调用方循环静默跳过/死亡
      （2026-09-08 实测：盘前复盘窗口开着却整天不触发）。
    """
    date = date_str or _bj_date()
    try:
        row = db.fetch_one(
            "SELECT done_date FROM schedule_state WHERE task = %s",
            (task,)
        )
    except Exception as e:
        print(f"[store] is_schedule_done({task}) 查询失败，按未完成处理: {e}")
        return False
    return row and row["done_date"] == date


# ================================================================
#  全市场行情收盘快照（盘后/周末免刷新 + _valid_codes 持久化）
# ================================================================

def save_market_snapshot(stocks: dict, valid_codes: list) -> bool:
    """保存全市场行情收盘快照（单行覆盖，key='latest'）。
    stocks: _cache['stocks'] 全量字典；valid_codes: [(prefix, code), ...]。
    成功返回 True。
    """
    if not stocks:
        return False
    try:
        db.upsert("market_snapshot", {
            "key": "latest",
            "stocks_json": json.dumps(stocks, ensure_ascii=False),
            "valid_codes_json": json.dumps(valid_codes, ensure_ascii=False),
            "saved_at": _now_iso(),
        }, conflict_columns=["key"])
        _SNAP_CACHE["ts"] = 0.0        # ★ 写入即失效读缓存（见 load_market_snapshot）
        # ★ 审查 P2-⑲：联动失效浮筹缓存（flow._FS_CACHE 同读这一行，TTL 30min
        #   且此前不联动 → 15:10 新快照落库后浮筹最长 30min 旧值 + 重复回源）
        try:
            from app.mainforce import flow as _flow
            _flow.invalidate_fs_cache()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[store] 保存行情收盘快照失败: {e}")
        return False


# ★ 进程内缓存（2026-09-12 egress 治理③）：这是全库最大的单行（1.1MB 文本，
#   压缩后过网约 336KB），却被矛盾扫描 / 日报 / 浮筹反推 / 快照恢复等路径反复整份读
#   （实测 1,141 次 ≈ 17MB/天）。收盘快照一天只落一次 → 300 秒缓存 + 写入侧失效。
#   ★ 返回深拷贝：tencent.restore_market_snapshot() 会把 stocks 直接塞进行情缓存并被
#     其它代码原地改写，共享同一对象会污染缓存。
_SNAP_CACHE = {"ts": 0.0, "data": {}}
_SNAP_TTL = 300

# ── ★ 2026-09-18：**跨进程**持久缓存（egress 治理，pg_stat_statements 实测驱动）──
# 为什么上面那个 300 秒进程内缓存不够：
#   · `load_market_snapshot()` 的 `WHERE key='latest'`        → 85 次 / 6 天
#   · `flow.get_float_shares_from_snapshot()` 的 `ORDER BY saved_at DESC LIMIT 1` → 70 次
#   两者读的是**同一行**（≈1.1MB 文本 / 约 336KB 过网），却**各有一套进程内缓存** ⇒
#     ① **每重启一个新进程必整份读一次**（本地 `run.py --reload` 改代码就重启 → 一天几十次）；
#     ② 常驻进程里 300 秒 TTL 会**反复重读**（最坏 ≈288 次/天 ≈ **97MB/天**）。
#   而这份快照**每天只在 15:10 落库一次** ⇒ 短 TTL 纯属浪费，纯粹白烧。
# 做法：落本机 SQLite，用 **`saved_at` 当版本号**（写入方本来就会更新它）。读之前先做一次
#   **只取该列**的极轻探测（单行单列 ≈ 几十字节，不是 336KB）—— 版本没变就直接用本机缓存，
#   **零大流量**。之所以用 `saved_at` 而不是让写入方打 `sync_meta` 版本号：写入方可能在
#   **别的机器**（Actions / 自建服务器），靠数据自身的 `saved_at` 对比**无需写入方配合**
#   即可跨进程失效。
_SNAP_DISK_PATH = os.path.join(DATA_DIR, "market-snapshot-cache.db")
_SNAP_DISK_TTL = 12 * 3600


def _snap_disk_load(saved_at):
    """按 saved_at 命中本机缓存（零大流量）。不符 / 过期 / 异常 → None（调用方回源）。"""
    try:
        if not os.path.exists(_SNAP_DISK_PATH) or saved_at is None:
            return None
        conn = sqlite3.connect(_SNAP_DISK_PATH)
        try:
            r = conn.execute("SELECT saved_at, stocks_json, valid_codes_json, ts "
                             "FROM snap LIMIT 1").fetchone()
            if not r:
                return None
            cur_saved, sj, vj, ts = r
            # 两边都转 str 比较：PG 可能回 datetime，SQLite 存的是 ISO 字符串
            if str(cur_saved) != str(saved_at):
                return None
            if time.time() - float(ts) > _SNAP_DISK_TTL:
                return None
            return {"stocks": json.loads(sj), "valid_codes": json.loads(vj),
                    "saved_at": cur_saved}
        finally:
            conn.close()
    except Exception:
        return None


def _snap_disk_save(out: dict) -> None:
    """写本机缓存（原子替换）。失败静默，不影响主流程。"""
    try:
        os.makedirs(os.path.dirname(_SNAP_DISK_PATH), exist_ok=True)
        tmp = _SNAP_DISK_PATH + ".tmp"
        conn = sqlite3.connect(tmp)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS snap("
                         "saved_at TEXT, stocks_json TEXT, valid_codes_json TEXT, ts REAL)")
            conn.execute("DELETE FROM snap")
            conn.execute("INSERT INTO snap VALUES (?,?,?,?)",
                         (str(out.get("saved_at") or ""),
                          json.dumps(out.get("stocks"), ensure_ascii=False),
                          json.dumps(out.get("valid_codes"), ensure_ascii=False),
                          time.time()))
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, _SNAP_DISK_PATH)
    except Exception:
        pass


def load_market_snapshot() -> dict:
    """加载最新行情收盘快照。返回 {stocks, valid_codes, saved_at}；无则空 dict。

    ★ 2026-09-18：三层 —— 进程内存（300s）→ **本机 SQLite（跨进程，saved_at 版本门控）**
      → 才回源。见上方 `_SNAP_DISK_*` 注释（这是「每重启一次就偷 336KB」的根治点）。
    """
    if _SNAP_CACHE["data"] and time.time() - _SNAP_CACHE["ts"] <= _SNAP_TTL:
        return copy.deepcopy(_SNAP_CACHE["data"])
    # ① 版本探测：**只取 saved_at 一列**（单行几十字节），不做整行读
    saved_at = None
    try:
        row = db.fetch_one("SELECT saved_at FROM market_snapshot WHERE key = %s",
                           ("latest",))
        saved_at = (row or {}).get("saved_at")
    except Exception as e:
        print(f"[store] 行情快照版本探测失败，退回整行读: {e}")
    # ② 本机持久缓存命中 → 零大流量
    if saved_at is not None:
        disk = _snap_disk_load(saved_at)
        if disk is not None:
            _SNAP_CACHE["ts"] = time.time()
            _SNAP_CACHE["data"] = disk
            return copy.deepcopy(disk)
    # ③ 回源（本轮唯一一次整行读）
    row = db.fetch_one("SELECT stocks_json, valid_codes_json, saved_at "
                       "FROM market_snapshot WHERE key = %s", ("latest",))
    if not row:
        return {}
    try:
        out = {
            "stocks": json.loads(row["stocks_json"]),
            "valid_codes": json.loads(row["valid_codes_json"]),
            "saved_at": row.get("saved_at", ""),
        }
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"[store] 读取行情收盘快照失败: {e}")
        return {}
    _SNAP_CACHE["ts"] = time.time()
    _SNAP_CACHE["data"] = out
    _snap_disk_save(out)          # 落盘 → 下一个新进程零大流量
    return copy.deepcopy(out)
