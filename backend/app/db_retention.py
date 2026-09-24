# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】数据库保留期清理（Supabase 500MB 上限治理）—— 2026-09-24 新增
================================================================================
背景：Supabase 免费档 **500MB/项目**，用户 2026-09-24 实测已用 **231MB（46%）**，
增速 ≈ **2.3MB/交易日**（≈48MB/月）⇒ 约 **4~6 个月**撞线。★ 而**超限后果比 Render
OOM 更严重**：项目进入**只读**（写入全失败）⇒ 日批全挂，且**不会自动恢复**。

真实分表（实测）显示结构是「**大的不涨、涨的不大**」：
  · **存量层**（占 75%）：`backtest_prices` 100MB / `mainflow_history` 22MB /
    `kline_cache` 18MB / `stock_finance_zz` 15MB / `indicator_cache` 7.7MB
    —— 但**日增合计仅 ~0.5MB** ⇒ 它们不威胁撞线，却占着空间（要"迁/裁"才能拿回，
    属**另一类决策**，见 memory 2026-09-24）。
  · **增量层**：`ranking_live` 0.89MB/日、`mainforce_state` 0.67MB/日 … ⇒ **本模块管这层**。
⚠️ 本模块**只处理"已逐一核实过全部读取点、确认只读最新"的表** ⇒ 清理**零功能影响**。
   未核实的表**不要**往 `_TABLES` 里加（宁可留着，也不要误删被回测/信号跟踪依赖的历史）。

⚠️⚠️ **反直觉陷阱（本模块最要紧的设计约束）**：保留期**不是"立刻变小"，而是
「先涨到上限再平」**！例：`mainforce_state` 现在只有 **11 天 = 7.5MB**，若设「保留 90 天」
⇒ 会**先涨到 ~61MB（涨 8 倍）**；`ranking_live` 同理（7.6 天 → 90 天会涨到 ~82MB）。
⇒ **天数必须按"能接受的上限"倒推，宁可短。** 默认 **14 天**（当前数据仅 7~11 天
⇒ **首轮不删任何东西**，零风险）。

✅ **为什么这两张表可以放心裁（2026-09-24 逐一核实全部读取点）**：
  · `mainforce_state` —— 服务侧 6 处读取**全部只读「最新/当天」**：
      `mainforce/state.py:287` 用 `DISTINCT ON (code) ... ORDER BY code, date DESC`（每只取最新）、
      `state.py:208` 只用当天行做断点续传、`routers/system.py:69` 只看 `MAX(date)`（数据新鲜度）、
      `routers/scoring.py:1996` 只取 `code`/`name`、`daily_report.py:425` 与 `:787` 均
      `WHERE date = (SELECT MAX(date) ...)`。
      唯一读历史的是**体检脚本** `scripts/rhythm_profile.py`（统计 `COUNT(DISTINCT date)`，
      天数字变小不影响判定）⇒ 无功能影响。
  · `ranking_live` —— 仅 2 处读取（`scoring/live_ranking.py:287,300`、
    `portfolio_radar.py:172,177`），**都是先 `MAX(rank_date)` 再按该日期查** ⇒ 完全不读历史日期。

不放在 `memory_watch.py` 里：那个盯"进程内存"，本模块盯"库体积"，同为运维哨兵、互不依赖。
================================================================================
"""

import os
from datetime import timedelta
from typing import Dict, List, Optional

from app.database import db

# 保留天数（全局默认）。⚠️ 不要为了"看起来省"而设大 —— 见上方陷阱。
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS") or 14)

# ★ 安全阀：无论保留期怎么设，**至少保留最近 K 个不同日期**。
#   防的场景：日批连挂 N 天 ⇒ 最新日期本身就"超期" ⇒ 单纯 `date < cutoff` 会把
#   **整张表删空**（数据全没、且不可恢复）。有这一层，最多只删到"最近第 3 天"。
KEEP_MIN_DAYS = 3

# (表名, 日期列, 保留天数, 说明)。加表前**必须先核实所有读取点只读最新**（见文件头）。
_TABLES = [
    ("mainforce_state", "date", RETENTION_DAYS,
     "主力行为状态（服务侧只读最新；仅体检脚本统计天数）"),
    ("ranking_live", "rank_date", RETENTION_DAYS,
     "全量精算榜（读取点均先 MAX(rank_date) 再按该日查）"),
]


def _cutoff(days: int) -> str:
    """N 天前的**北京时间日期**（ISO `YYYY-MM-DD`）。

    用字符串而非日期类型：项目双库（PG/SQLite）下 `date` 列都是 TEXT，字典序即时间序
    ⇒ 字符串比较两侧一致（同 `memory_watch` 对 `ts` 的处理）。
    """
    from app.flash.rules import beijing_now
    return (beijing_now() - timedelta(days=max(1, int(days)))).date().isoformat()


def _cleanup_one(table: str, col: str, days: int) -> Dict:
    """清理单表。返回 {table, deleted, cutoff, kept_from, retain_days, error}。

    失败**只记录不抛**：清理是"锦上添花"，绝不能反噬日批主流程。
    """
    out: Dict = {"table": table, "retain_days": days, "deleted": 0,
                 "cutoff": None, "kept_from": None, "error": None}
    try:
        # ★ 安全阀：取「最近 KEEP_MIN_DAYS 个不同日期」中最早的那个
        rows = db.fetch(
            f"SELECT MIN(d) AS mn FROM (SELECT DISTINCT {col} AS d FROM {table} "
            f"ORDER BY {col} DESC LIMIT %s) x", (KEEP_MIN_DAYS,))
        keep_from = str((rows[0] or {}).get("mn") or "") if rows else ""
        if not keep_from:
            out["error"] = "表为空或日期列无值，跳过"
            return out
        cutoff = _cutoff(days)
        out["cutoff"] = cutoff
        out["kept_from"] = keep_from[:10]
        # 双条件：既**超过保留期**，又**不属于最近 K 天**（安全阀）
        n = db.execute(f"DELETE FROM {table} WHERE {col} < %s AND {col} < %s",
                       (cutoff, keep_from[:10]))
        out["deleted"] = int(n or 0)
    except Exception as e:
        out["error"] = str(e)[:100]
    return out


def cleanup(days: Optional[int] = None, tables: Optional[list] = None) -> List[Dict]:
    """按保留期清理全部受管表，返回逐表结果。

    参数：days 覆盖默认保留天数；tables 覆盖受管表清单（便于测试/一次性清理）。
    """
    d = max(1, int(days or RETENTION_DAYS))
    out: List[Dict] = []
    for name, col, d0, note in (tables if tables is not None else _TABLES):
        r = _cleanup_one(name, col, d if days is not None else d0)
        r["note"] = note
        out.append(r)
    return out


def summarize(rows: List[Dict]) -> str:
    """一行摘要（日批日志用）。"""
    parts = []
    for r in rows:
        if r.get("error"):
            parts.append(f"{r['table']} 跳过({r['error']})")
        else:
            parts.append(f"{r['table']} 删 {r['deleted']} 行（留 {r['retain_days']} 天）")
    return "；".join(parts) if parts else "无受管表"
