# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】全池等权锚（市况第二锚）——客观分段判态，H2'/H3 的单一事实源
================================================================================

背景（docs/plans/20261026_评分分市况重构方案_v0.md §7 判据冻结 v1.0）：
  · 生产 market_regime 的锚是沪深300（sh000300）；2026-10 分化市里 300 距高点
    −18.9% ⇒ regime=defensive，而评分基准池（全池）中位口径处于阴跌、均值口径
    创新高——「权重切换挂 300 锚 vs 基准池是全池」的系统性错位（方案 §1.3/§三 H3）。
  · H2' 阴跌防御因子影子榜需要「阴跌段」判定；终裁口径 = **中位数收益**的 60 日
    滚动分段（历史 38 个阴跌段 / 28 个急跌段，§6.2/§7——均值口径仅 7 段，撑不起
    「样本闸 ≥10 段、影子期 6~12 个月」的冻结判据，故判态口径取中位）。

分段规则（★ 判据冻结口径，只许复现不许改；阈值改动须新开方案节留痕）：
  · 窗口 = 该日及之前共 60 个日收益样本（剔新股首日——上市首日无前收，天然无收益）；
  · 60 日复利收益 ≥ +10%            → bull
  · ≤ −5%：窗口内「中位日收益 < −3%」天数 ≥3 → bear_crash，否则 bear_slow
  · 其余                              → flat

本模块只做「判态 + 落库」，不进任何决策链（不改评分权重/不改准入——那是
影子期 GO 且用户拍板后的事）。表 eq_anchor_history 与 market_regime_history
并跑，供 scripts/eq_anchor_check.py 月度算双锚不一致率/错配率（H3 判据）。

数据来源两条腿：
  · 历史回填：scripts/backfill_eq_anchor.py 从本地 zzshare_daily.db（全市场
    close×factor 复权）一次性灌 2010 起 → 判态函数回填 state；
  · 每日增量：run_daily() 从全市场行情缓存（tencent._cache，日批启动时已刷新，
    零新增 egress）取当日每股涨跌幅 → 中位/均值 → upsert → 增量判态。
================================================================================
"""
import math
from typing import Dict, List, Optional

_TABLE_READY = False

# 分段参数（★ 冻结判据 §1.1/§7：窗口 60 日，熊 −5% / 牛 +10%，急跌=窗口内
#   单日(中位口径)<−3% 天数 ≥3）。集中定义供回填/日批/检验脚本三方复现。
WINDOW = 60
BEAR_TH = -0.05
BULL_TH = 0.10
CRASH_RET = -0.03
CRASH_DAYS = 3

STATE_BULL = "bull"
STATE_BEAR_SLOW = "bear_slow"
STATE_BEAR_CRASH = "bear_crash"
STATE_FLAT = "flat"
# 全池锚四态 → regime 四态的映射（H3 双锚对比用；bear_slow/crash 同映射 defensive）
REGIME_MAP = {STATE_BULL: "offensive", STATE_BEAR_SLOW: "defensive",
              STATE_BEAR_CRASH: "defensive", STATE_FLAT: "neutral"}


def ensure_table() -> None:
    """建 eq_anchor_history（幂等，兼容 PostgreSQL/SQLite）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    from app.database import db
    id_ddl = ("id SERIAL PRIMARY KEY" if db.is_postgres
              else "id INTEGER PRIMARY KEY AUTOINCREMENT")
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS eq_anchor_history (
            {id_ddl},
            anchor_date TEXT NOT NULL UNIQUE,
            ret_median REAL NOT NULL,
            ret_mean REAL,
            universe_n INTEGER,
            state TEXT,
            ret_60d REAL,
            crash_days_60d INTEGER,
            updated_at TEXT
        )
    """)
    _TABLE_READY = True


def judge_states(rets: List[float],
                 window: int = WINDOW) -> List[Optional[dict]]:
    """对按日升序的日收益序列（小数）逐日判态。

    返回与输入等长的列表：窗口不足的前 window-1 项为 None，其余
    {state, ret_60d, crash_days_60d}。复利与急跌计数用前缀积/前缀和，
    O(n) 完成整段滚动窗口。
    """
    n = len(rets)
    out: List[Optional[dict]] = [None] * n
    if n < window:
        return out
    prefix_prod = [1.0] * (n + 1)     # P[i] = prod(1+r)[:i]
    prefix_crash = [0] * (n + 1)      # C[i] = #(<−3%) in r[:i]
    for i, r in enumerate(rets):
        prefix_prod[i + 1] = prefix_prod[i] * (1.0 + r)
        prefix_crash[i + 1] = prefix_crash[i] + (1 if r < CRASH_RET else 0)
    for i in range(window - 1, n):
        ret60 = prefix_prod[i + 1] / prefix_prod[i + 1 - window] - 1.0
        crash = prefix_crash[i + 1] - prefix_crash[i + 1 - window]
        if ret60 >= BULL_TH:
            state = STATE_BULL
        elif ret60 <= BEAR_TH:
            state = STATE_BEAR_CRASH if crash >= CRASH_DAYS else STATE_BEAR_SLOW
        else:
            state = STATE_FLAT
        out[i] = {"state": state, "ret_60d": ret60, "crash_days_60d": crash}
    return out


def load_series(limit: int = 400) -> List[dict]:
    """读最近 limit 行日收益（升序，state 已判或 NULL）。"""
    from app.database import db
    ensure_table()
    rows = db.fetch(
        f"SELECT anchor_date, ret_median, ret_mean, universe_n, state, "
        f"ret_60d, crash_days_60d FROM eq_anchor_history "
        f"ORDER BY anchor_date DESC LIMIT {int(limit)}") or []
    return list(reversed(rows))


def backfill_states(rows: Optional[List[dict]] = None) -> int:
    """对序列重算判态并回填 state 为空的行（已判态的历史行不动——冻结口径防漂移）。

    返回回填行数。rows 缺省时读**全表**（判态需从序列头起算 60 日窗口，
    截尾读取会让早期行永远无态——首跑实测只回填了最近 341/5293 行）。
    """
    from app.database import db
    if rows is None:
        ensure_table()
        rows = db.fetch(
            "SELECT anchor_date, ret_median, ret_mean, universe_n, state, "
            "ret_60d, crash_days_60d FROM eq_anchor_history "
            "ORDER BY anchor_date") or []
    rets = [float(r["ret_median"]) for r in rows]
    judged = judge_states(rets)
    n = 0
    for r, j in zip(rows, judged):
        if j is None or r.get("state"):
            continue
        db.execute(
            "UPDATE eq_anchor_history SET state = %s, ret_60d = %s, "
            "crash_days_60d = %s WHERE anchor_date = %s",
            (j["state"], round(j["ret_60d"], 6), j["crash_days_60d"],
             r["anchor_date"]))
        n += 1
    return n


def _collect_today() -> Optional[dict]:
    """从全市场行情缓存取最近完成交易日的等权日收益（中位+均值，小数）。

    行情缓存由日批启动时刷新（rank_live 同源），本函数零额外网络请求。
    缓存为空 / 无有效涨跌幅时返回 None（调用方跳过，保留旧行）。
    """
    from app.tencent import _cache
    from app.flash.rules import latest_completed_trading_day
    stocks = list((_cache.get("stocks") or {}).values())
    rets = []
    for s in stocks:
        cp = s.get("change_pct")
        if cp is None or (s.get("price", 0) or 0) <= 0:
            continue
        try:
            r = float(cp) / 100.0
        except (TypeError, ValueError):
            continue
        if math.isfinite(r):
            rets.append(r)
    if len(rets) < 100:
        return None
    rets.sort()
    m = len(rets)
    median = (rets[m // 2] if m % 2 else (rets[m // 2 - 1] + rets[m // 2]) / 2.0)
    return {
        "anchor_date": str(latest_completed_trading_day())[:10],
        "ret_median": median,
        "ret_mean": sum(rets) / m,
        "universe_n": m,
    }


def run_daily(force: bool = False) -> dict:
    """日批任务主体：采集当日等权收益 → 落库 → 增量判态。返回当日判定摘要。

    幂等：force=False 时当日行已有 universe_n 则跳过采集（重跑不覆盖）。
    """
    from app.database import db
    ensure_table()
    today_row = None
    try:
        today_row = db.fetch_one(
            "SELECT anchor_date, universe_n FROM eq_anchor_history "
            "ORDER BY anchor_date DESC LIMIT 1")
    except Exception:
        pass
    if today_row and today_row.get("universe_n") and not force:
        return {"date": today_row["anchor_date"], "skipped": True,
                "note": "当日行已存在（幂等跳过）"}

    day = _collect_today()
    if not day:
        return {"ok": False,
                "note": "行情缓存不可用/有效样本<100，本次未写入（保留旧行）"}
    db.upsert("eq_anchor_history", {
        "anchor_date": day["anchor_date"],
        "ret_median": round(day["ret_median"], 6),
        "ret_mean": round(day["ret_mean"], 6),
        "universe_n": day["universe_n"],
        "updated_at": None,
    }, conflict_columns=["anchor_date"])
    backfill_states()
    latest = latest_state()
    print(f"[eq_anchor] {day['anchor_date']} 全池中位 {day['ret_median']:+.3%}"
          f"（n={day['universe_n']}）→ {latest.get('state') or '?'}")
    return {"ok": True, "date": day["anchor_date"],
            "ret_median": day["ret_median"], "universe_n": day["universe_n"],
            **(latest or {})}


def latest_state() -> Dict:
    """最新已判态行（H2' 影子榜门控 / H3 双锚对比的消费接口）。"""
    from app.database import db
    ensure_table()
    row = db.fetch_one(
        "SELECT anchor_date, state, ret_60d, crash_days_60d, ret_median, "
        "universe_n FROM eq_anchor_history WHERE state IS NOT NULL "
        "ORDER BY anchor_date DESC LIMIT 1") or {}
    return dict(row)
