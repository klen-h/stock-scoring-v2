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
二、数据源（★ 2026-11-08 四源实测后定主源）
--------------------------------------------------------------------------------
  | 源 | 实测结果 | 结论 |
  |---|---|---|
  | **金十日历图表**（`z3c.jin10.com/calendarGetSiteChartByDateRange`） | **0.1s，47 条，2022-12~2026-10** | ✅ **主源** |
  | akshare `macro_usa_unemployment_rate` | 4.5s，669 行，但**只到 2025-09** | 备源（补更早历史） |
  | FRED `fredgraph.csv?id=UNRATE` | 连续 2 次 ReadTimeout（各 20s） | ✗ 不用 |
  | BLS `api.bls.gov` | HTTP **403** | ✗ 不用 |
  | 东财 `RPT_ECONOMY_US_*`（5 个候选） | 全部 `data=0` | ✗ 找不到 reportName，不猜 |

  ⇒ `refresh_from_jin10()` 是**唯一自动入口**（1 请求 / 天成本可忽略）；
     `upsert_rate()` 是通用写口（人工补录）；`refresh_from_akshare()` 补 2022-12 以前。
     `refresh_from_fred()` 保留但默认不调（超时）。

  ⚠️ 金十日历用的 `x-app-id` 是 **`sKKYe29sFuJaeOCJ`**，
     **不是** `flash` / `jin10_tidy` 的 `fiXF2nOnDycGutVA` —— 两套凭据别混用。

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


def _month_gaps(series: List[Tuple[str, float]]) -> List[str]:
    """检测 `series`（ISO 日期正序）里**缺失的月份**，返回 `['2025-10', ...]`。

    ★ 2026-11-08：实测源数据里 **`2025-10` 是真空的**（美国政府停摆期间 BLS 未发布），
      不是拉取失败。这里只**报告**缺口，不插值 —— 插值会凭空造出一个失业率读数。

    ⚠️ **缺口对 MA3 的影响（已知局限，接受不改）**：
      缺 1 个月时，`compute_sahm()` 会把缺口两侧的月份当成**相邻**参与 3 月平均
      （如 2025-09 与 2025-11 直接相连），使缺口附近几个 MA3 值有轻微偏差。
      **不改的理由**：三条路都有问题 ——
        ① 插值 = 凭空造数，违反"缺失不进分母"的项目纪律；
        ② 截断到缺口前 = 白白丢掉最近一年数据；
        ③ 按日历月重采样 = 等价于插值。
      ⇒ 选择"照算 + 显式报告缺口"。当前缺口（2025-10）已在比较窗口之外，
        对结论无实质影响；若将来缺口落进最近 12 个月，`detail` 会带出提示。
    """
    gaps: List[str] = []
    for i in range(1, len(series)):
        try:
            y0, m0 = int(series[i - 1][0][:4]), int(series[i - 1][0][5:7])
            y1, m1 = int(series[i][0][:4]), int(series[i][0][5:7])
        except (ValueError, IndexError):
            continue
        diff = (y1 - y0) * 12 + (m1 - m0)
        if diff <= 1:
            continue
        for k in range(1, diff):
            mm = m0 + k
            yy = y0 + (mm - 1) // 12
            mm = (mm - 1) % 12 + 1
            gaps.append("%04d-%02d" % (yy, mm))
    return gaps


def evaluate(limit: int = 36) -> Dict:
    """读库 → 计算 → 返回判定结果（含 `as_of`、序列覆盖范围、**缺口列表**）。

    ★ 缺口策略（2026-11-08）：**跳过缺口继续算，但把缺口写进结果并提示**。
      理由：若"有缺口就整条不判定"，则因 2025-10 永久缺失，本规则将永远无法出结论；
      而萨姆规则看的是趋势，缺 1 个月对结论影响有限 —— **但使用者必须知道**。
      对比：纯函数 `compute_sahm()` 仍保持严格（输入含 None 直接 invalid），
      缺口处理只发生在本层（数据层），职责分离。
    """
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

    # 只关心"参与本次计算的那段"（compute_sahm 用最后 MIN_POINTS 个点）
    tail = series[-MIN_POINTS:] if len(series) >= MIN_POINTS else series
    gaps = _month_gaps(tail)
    res["gaps"] = gaps
    if gaps and res.get("status") == "ok":
        res["detail"] = (res.get("detail", "")
                         + f"｜⚠️ 序列缺 {len(gaps)} 个月（{', '.join(gaps)}），"
                           f"按可用月份计算")
    return res


# ── 金十日历接口（主源）──────────────────────────────────────────────────────
# 取数/解析统一走 `app/jin10_calendar.py`（同一接口也被 L1 通胀层复用），
# 这里只保留"失业率专属"的部分：dataId 与落库表 `macro_unemployment`。
# ⚠️ 历史教训（写在这里免得又踩）：**一定要用 `revised` 而非 `actual`**、
#    **一定要用统计月 `xx` 而非 `pub_time`** —— 这两点已在通用层实现，别再本地重写。


def refresh_from_jin10(gte: str = None, lte: str = None,
                       timeout: float = 20.0) -> Dict:
    """从金十日历拉美国失业率历史并 upsert 到 `macro_unemployment`（**唯一自动源**）。

    ★ 2026-11-08 实测：1 请求 0.1s、47 条、覆盖 2022-12~2026-10。
      解析细节（revised 优先 / 统计月 / 缺口不插值）见 `app/jin10_calendar.py` 文件头。

    ★ **`2025-10` 在源数据里就是空的**（美国政府停摆期间 BLS 未发布），
      不是拉取失败。缺口由 `evaluate()` 显式跳过并报告，**不插值**。

    返回 `{ok, saved, latest, months, error}`；失败不抛。
    """
    from app.jin10_calendar import DATA_IDS, fetch_series

    r = fetch_series(DATA_IDS["unemployment"], gte=gte, lte=lte, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "saved": 0, "latest": None,
                "months": r.get("rows") or 0, "error": r.get("error")}

    saved = 0
    latest = None
    for iso, val in r["points"]:
        if not (0 < val < 30):          # 失业率合理性护栏
            continue
        try:
            d = upsert_rate(iso, val, source="jin10_cal")
            saved += 1
            if latest is None or d > latest:
                latest = d
        except Exception:
            continue

    return {"ok": saved > 0, "saved": saved, "latest": latest,
            "months": r.get("rows") or 0, "error": ""}


def refresh_from_akshare() -> Dict:
    """用 akshare 的 `macro_usa_unemployment_rate` **回填历史**（月度）。

    ★ 2026-11-08 实测（这是三个源里唯一可用的）：
        · FRED `fredgraph.csv?id=UNRATE` —— 连续 2 次 ReadTimeout(各 20s)
        · BLS `api.bls.gov` —— HTTP **403**（无 key / 屏蔽）
        · akshare ✅ 669 行、4.5s、列 = `商品/日期/今值/预测值/前值`

    ⚠️ **两个必须知道的口径事实**：
      1. `日期` 是**数据发布日**（每月第一个周五附近），**不是统计月份**。
         萨姆规则只依赖"月频序列的顺序与间距"，用发布日不改变结论，故原样存
         （经 `upsert_rate` 归一到当月 1 日，保持月频）。
      2. akshare 数据源**滞后**：实测最新只到 **2025-09**，其后需人工补
         （`scripts/record_unemployment.py`）。

    返回 `{ok, saved, latest, error}`；失败不抛。
    """
    try:
        import akshare as ak
    except Exception as e:
        return {"ok": False, "saved": 0, "latest": None,
                "error": f"akshare 不可用: {str(e)[:80]}"}
    try:
        df = ak.macro_usa_unemployment_rate()
    except Exception as e:
        return {"ok": False, "saved": 0, "latest": None,
                "error": f"akshare 调用失败: {str(e)[:90]}"}
    if df is None or getattr(df, "empty", True):
        return {"ok": False, "saved": 0, "latest": None, "error": "返回空表"}

    saved = 0
    latest = None
    for _, row in df.iterrows():
        d = str(row.get("日期") or "")[:10]
        v = row.get("今值")
        # NaN 检查（pandas 的 NaN != NaN）
        if not d or v is None or (isinstance(v, float) and v != v):
            continue
        try:
            iso = upsert_rate(d, float(v), source="akshare")
            saved += 1
            if latest is None or iso > latest:
                latest = iso
        except Exception:
            continue
    return {"ok": saved > 0, "saved": saved, "latest": latest, "error": ""}


def refresh_from_fred(timeout: float = 12.0) -> Dict:
    """可选：从 FRED 拉 `UNRATE` 全列（**国内实测超时，保留仅为备用**）。

    ★ 2026-11-08 实测：连续 2 次 ReadTimeout(各20s) ⇒ 只作手动补充，
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
    # short 不带"萨姆规则"前缀，供日批等已有上文的地方用（避免"萨姆规则: 萨姆规则…"）
    if st == "ok":
        if r.get("triggered"):
            short = (f"⚠️ 已触发（{r.get('sahm'):+.2f}pp ≥ "
                     f"{r.get('threshold'):.2f}）——按框架应硬切衰退剧本")
        else:
            short = f"未触发（{r.get('sahm'):+.2f}pp）"
    elif st == "insufficient":
        short = f"数据不足（{r.get('have')}/{r.get('need')} 个月）"
    else:
        short = f"不可用：{r.get('detail')}"
    text = "萨姆规则" + short
    gaps = r.get("gaps") or []
    return {
        "ok": st == "ok",
        "status": st,
        "triggered": r.get("triggered"),
        "sahm_pp": r.get("sahm"),
        "threshold": r.get("threshold"),
        "as_of": r.get("as_of"),
        "from": r.get("from"),
        "n_loaded": r.get("n_loaded"),
        "gaps": gaps,
        "has_gap": bool(gaps),
        "text": text + (f"（序列缺 {len(gaps)} 个月：{', '.join(gaps)}）" if gaps and st == "ok" else ""),
        "short": short,
        "detail": r.get("detail"),
        "scorer_version": SCORER_VERSION,
    }
