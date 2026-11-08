# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】萨姆规则引擎（Sahm Rule）—— **纯计算器，不产生任何买卖信号**
================================================================================
来源：`docs/plans/全球宏观周期判断与资产配置逻辑框架.md` §4.3（2026-11-08 审查件）。
该文档把它定为「最高优先级覆盖逻辑」——触发即硬切衰退剧本。本文只做**计算与留痕**，
**不接状态机、不接评分、不接闸门、不接仓位**（S3 才谈怎么消费，且仍需拍板）。

--------------------------------------------------------------------------------
一、口径（★ 2026-11-08 按标准定义修正，审查时发现原文档表述有歧义）
--------------------------------------------------------------------------------
  原文档写「失业率 3 个月均值较 12 个月低点上升 ≥0.5pp」。
  **"低点"指单月失业率最低值，还是 3 月均值的最低值？** 两者结果不同（后者更早触发）。

  本标准口径（Sahm 原始定义）：
    MA3(i) = (U[i] + U[i-1] + U[i-2]) / 3
    SAHM   = MA3(now) − min( MA3 在 **过去 12 个月内** 的最小值 )
    触发   = SAHM ≥ 0.50 pp

  ⇒ 数据量要求：MA3 需要 13 个点（当期 + 过去 12 个月），
     而 MA3 本身要 3 个原始点 ⇒ **原始序列至少 15 个月**。

--------------------------------------------------------------------------------
二、数据源（**新增的唯一外部依赖，当前走人工录入**）
--------------------------------------------------------------------------------
  实测（2026-11-08）：
    · FRED `fredgraph.csv?id=UNRATE` —— 国内 **连续 2 次 ReadTimeout（各 20s）**，
      与 `app/macro.py` 注释"国内常超时"一致 ⇒ 不作主源。
    · 东财 `datacenter-web` 宏观库 `RPT_ECONOMY_US_UNEMPLOYMENT` 等 5 个候选 reportName
      全部 `data=0` ⇒ 未找到可用 reportName，不猜。

  ⇒ 月度数据一年只有 12 个点，**人工录入比接一个老超时的接口更可靠**。
     `upsert_rate()` 是唯一写口；`refresh_from_fred()` 保留为可选加速器（默认不调）。

--------------------------------------------------------------------------------
三、★★ 铁律：**"数据不足" ≠ "未触发"**
--------------------------------------------------------------------------------
  项目已有同型教训：`macro_regime_class`「缺失证据不进分母」、
  `policy_bottom_watch`「两层抓取失败都显示不可用，不等于没有公告」。

  这里同样：序列不足 15 个月 / 含缺口 / 读表失败 ⇒ 返回 `status != "ok"`，
  **`triggered` 为 None（三态：True/False/None）**。
  消费方**不得**把 None 当成 False 然后"继续推进阶段"——那等于"没数据就说没事"。
  正确做法：显示「萨姆规则不可用」并冻结推进（见框架文档 §4.3 修订）。

--------------------------------------------------------------------------------
四、表
--------------------------------------------------------------------------------
  macro_unemployment(date TEXT PK, rate REAL, source TEXT, created_at TEXT)
  · date 用 **ISO `YYYY-MM-DD`**（月度数据取当月 1 日），
    ⚠️ 不沿用 `_bj_date()` 的斜杠格式 —— `macro_daily` 的日期排序事故（2026-11-08 已修）
    就是前车之鉴，新表一律 ISO。
================================================================================
"""
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.database import db

_TABLE = "macro_unemployment"
_READY = False

# 判据版本：改任何阈值/窗口必须递增（同 `macro_regime_class.SCORER_VERSION` 纪律）
SCORER_VERSION = "2026-11-08.1"

SAHM_THRESHOLD = 0.50      # pp
COMPARE_WINDOW = 12        # 过去 12 个月
MA_WINDOW = 3              # 3 个月移动平均
MIN_POINTS = MA_WINDOW + COMPARE_WINDOW      # 15


def ensure_table() -> None:
    """建表（幂等）。"""
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT PRIMARY KEY,
            rate REAL,
            source TEXT,
            created_at TEXT
        )
    """)
    _READY = True


# ── 纯函数核心（单测锚点：backend/test_sahm_rule.py）────────────────────────

def compute_sahm(rates: List[float],
                 threshold: float = SAHM_THRESHOLD,
                 compare_window: int = COMPARE_WINDOW,
                 ma_window: int = MA_WINDOW) -> Dict:
    """萨姆规则核心计算（**纯函数**：不连库、不读全局，给定输入必然同一输出）。

    参数：
      rates          : 月度失业率序列，**升序**（最旧在前，最新在末尾），单位 %。
      threshold      : 触发阈值（pp），默认 0.50。
      compare_window : 比较窗口（月），默认 12。
      ma_window      : 平滑窗口（月），默认 3。

    返回 dict：
      status    : `ok` | `insufficient` | `invalid`
      sahm      : 当前值（pp，保留 4 位）；非 ok 时为 None
      triggered : True / False / **None（不可判定）**
      detail    : 说明文本
      以及 `current_ma3` / `low_ma3` / `need` / `have` / `threshold` 等辅助字段。

    ★ 三态语义：`triggered is None` 表示**无法判定**，调用方必须与 False 区分对待。
    """
    need = ma_window + compare_window
    seq = list(rates or [])

    # 缺口检查：含 None/非数值 ⇒ 不判定（不做插值猜测）
    clean: List[float] = []
    bad = 0
    for v in seq:
        if v is None:
            bad += 1
            continue
        try:
            clean.append(float(v))
        except (TypeError, ValueError):
            bad += 1
    if bad:
        return {"status": "invalid", "sahm": None, "triggered": None,
                "detail": f"序列含 {bad} 个缺失/非数值项，不做插值（缺口不猜）",
                "need": need, "have": len(clean), "threshold": threshold}

    if len(clean) < need:
        return {"status": "insufficient", "sahm": None, "triggered": None,
                "detail": f"需要至少 {need} 个月数据，当前仅 {len(clean)} 个月",
                "need": need, "have": len(clean), "threshold": threshold}

    # MA3：索引 i 对应原始序列 [i-ma_window+1 .. i]
    ma3 = [sum(clean[i - ma_window + 1:i + 1]) / float(ma_window)
           for i in range(ma_window - 1, len(clean))]

    cur = ma3[-1]
    # 过去 compare_window 个月的 MA3（不含当期）
    window = ma3[-compare_window - 1:-1]
    low = min(window) if window else cur
    val = cur - low
    triggered = val >= threshold

    return {
        "status": "ok",
        "sahm": round(val, 4),
        "triggered": bool(triggered),
        "current_ma3": round(cur, 4),
        "low_ma3": round(low, 4),
        "threshold": threshold,
        "need": need,
        "have": len(clean),
        "ma3_n": len(ma3),
        "scorer_version": SCORER_VERSION,
        "detail": (f"MA3={cur:.2f}，过去{compare_window}月最低 MA3={low:.2f}，"
                   f"差 {val:+.2f}pp（阈值 {threshold:.2f}）"),
    }


# ── 数据读写入（人工录入为主）────────────────────────────────────────────────

def _iso_month(s: str) -> str:
    """把 `2026-11` / `2026/11/1` / `2026-11-01` 统一成 **当月 1 日的 ISO**。"""
    import re
    m = re.match(r"^(\d{4})[-/](\d{1,2})", str(s or ""))
    if not m:
        raise ValueError(f"日期格式无法解析: {s!r}（应形如 2026-11 或 2026-11-01）")
    return "%s-%02d-01" % (m.group(1), int(m.group(2)))


def upsert_rate(month: str, rate: float, source: str = "manual") -> str:
    """录入/覆盖某月失业率。`month` 可写 `2026-11`（自动归一到当月 1 日）。

    返回归一后的 date。
    """
    ensure_table()
    d = _iso_month(month)
    db.execute(
        f"INSERT INTO {_TABLE} (date, rate, source, created_at) VALUES (%s,%s,%s,%s) "
        f"ON CONFLICT (date) DO UPDATE SET rate=EXCLUDED.rate, "
        f"source=EXCLUDED.source, created_at=EXCLUDED.created_at",
        (d, float(rate), str(source or "manual"),
         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
    )
    return d


def load_series(limit: int = 36) -> List[Tuple[str, float]]:
    """读最近 `limit` 个月，**按日期正序**（最旧在前）。

    ★ date 全是 ISO ⇒ 可直接字符串排序；但仍显式 ORDER BY 以保证语义清晰。
    """
    ensure_table()
    rows = db.fetch(
        f"SELECT date, rate FROM {_TABLE} ORDER BY date DESC LIMIT %s",
        (max(1, int(limit)),)) or []
    out = [(str(r.get("date")), float(r.get("rate"))) for r in rows
           if r.get("rate") is not None]
    out.sort(key=lambda x: x[0])
    return out


def evaluate(limit: int = 36) -> Dict:
    """读库 → 计算 → 返回判定结果（含 `as_of` 与序列覆盖范围）。"""
    try:
        series = load_series(limit)
    except Exception as e:
        return {"status": "invalid", "sahm": None, "triggered": None,
                "detail": f"读取 {_TABLE} 失败: {str(e)[:80]}"}

    res = compute_sahm([v for _, v in series])
    res["as_of"] = series[-1][0] if series else None
    res["from"] = series[0][0] if series else None
    res["n_loaded"] = len(series)
    res["table"] = _TABLE
    return res


def refresh_from_fred(timeout: float = 12.0) -> Dict:
    """可选加速器：从 FRED 拉 `UNRATE` 全列并逐月 upsert。**默认不调**。

    ★ 2026-11-08 实测：国内连续 2 次 ReadTimeout(各20s) ⇒ 只作手动补充，
      **不要挂进日批**（否则每天白等一次超时）。失败不抛，返回 dict 说明。
    """
    import csv
    import io

    import requests

    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=UNRATE"
    try:
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception as e:
        return {"ok": False, "saved": 0, "error": f"下载失败: {str(e)[:90]}"}

    saved = 0
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        d = (row.get("observation_date") or row.get("DATE") or "").strip()
        v = (row.get("UNRATE") or "").strip()
        if not d or not v or v in (".", "NA"):
            continue
        try:
            upsert_rate(d, float(v), source="fred")
            saved += 1
        except Exception:
            continue
    return {"ok": saved > 0, "saved": saved, "error": ""}


# ── 只读视图（给 S3 状态机 / 前端卡片用，暂不接线）────────────────────────────

def view() -> Dict:
    """对外只读快照：判定结果 + 人话描述。**不含任何操作建议**。"""
    r = evaluate()
    st = r.get("status")
    if st == "ok":
        if r.get("triggered"):
            text = (f"⚠️ 萨姆规则已触发（{r.get('sahm'):+.2f}pp ≥ "
                    f"{r.get('threshold'):.2f}）——按框架应硬切衰退剧本")
        else:
            text = f"萨姆规则未触发（{r.get('sahm'):+.2f}pp）"
    elif st == "insufficient":
        text = f"萨姆规则数据不足（{r.get('have')}/{r.get('need')} 个月）"
    else:
        text = f"萨姆规则不可用：{r.get('detail')}"
    return {
        "ok": st == "ok",
        "status": st,
        "triggered": r.get("triggered"),
        "sahm_pp": r.get("sahm"),
        "threshold": r.get("threshold"),
        "as_of": r.get("as_of"),
        "text": text,
        "detail": r.get("detail"),
        "scorer_version": SCORER_VERSION,
    }
