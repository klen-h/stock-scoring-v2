# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】事件驱动信号源 —— E2 政策脉冲（唯一通过预登记检验的事件）
================================================================================
【验证来源】
  · 原始序列：`scripts/build_event_history.py`（全市场 21 年，2005~2026，
    涨跌停用**真实涨跌停价** high_limit/low_limit 精确判定）
  · 预测力检验：`scripts/event_edge_check.py`（预登记判定标准 + block bootstrap）
  · 预登记结论（2026-09-27，判定标准先于结果写死）：
      E1 capitulation（冰点）      → WEAK（归档）
      E2 policy_surge（政策脉冲）  → ★ PASS
      E3 panic_reversal（恐慌反转）→ WEAK（归档）

【E2 实证（全市场等权，T+1 开盘买入、持 20 日；阈值 = up_ratio≥0.90 且涨停比例≥2.0%）】
  全样本：   事件 331 天 / 60 个独立簇，T+20 +5.62%  vs 基准 +3.15%  差 +2.47pp
             bootstrap 95% CI [+4.16%, +7.09%]，P(mean<=0)=0.000，胜率 70.4%
  按 regime：defensive       n=126  +3.87%  vs 该态基准 +2.50%  增量 +1.37pp
             neutral         n=61   +5.41%  vs +2.31%            增量 +3.11pp
             offensive       n=85   +7.23%  vs +3.30%            增量 +3.94pp
             neutral_bearish n=17   +5.88%  vs +5.72%            增量 +0.16pp（样本小，13 簇）
  ★ 阈值演进（每步都经独立预登记）：
    (0.95, 50家) → 181 天/54 簇、差 +3.02pp        ← 原始（池子漂移，不可迁移）
    (0.90, 50家) → 298 天/62 簇、差 +2.99pp        ← P1 放宽（触发 +65%）
    (0.90, 2.0%) → 331 天/60 簇、差 +2.47pp        ← P1-b 比例化（**可迁移**）
  ★ P1-b 的决定性证据：缩池到 58%/40% 时，绝对家数阈值触发天数**偏离 62%/82%**，
    比例口径仅 **3.0%/2.5%** ⇒ 生产（快照 3236 只）与回测（全市场 5557 只）口径一致。

【为什么接入它】
  regime 引擎只用沪深300 均线，在 V 型反转处**系统性滞后**（2024-09-24 政策底、
  2024-09-30 涨停潮均被判 defensive）。E2 是**盘中即可观测**的先行条件：
  防御期内一旦出现 E2，其后 20 日显著优于防御期平均水平 ⇒ 是对冲均线滞后的候选。

【⚠️ 本版本（v0）的边界 —— 必须遵守】
  1. **不进决策链**：只产出信号 + 落库 + 展示，**不修改** regime 状态、战法准入、
     仓位建议。升级为「defensive→neutral 提前解除」需先积累实盘样本复核。
  2. 生产判定用 `change_pct >= 9.9` 近似涨停（复用 `_market_breadth_now`，实时行情无
     `high_limit`），对 20cm（创业板/科创板）会**高估**涨停家数 ⇒ 判定偏宽松。
     ★ P1-b 已解决**池子大小**漂移（绝对家数→比例口径），但"9.9 近似"仍是另一层
       口径差异，两条方向相反（近似高估 + 池子缩小低估），净影响未量化。
  3. neutral_bearish 内 E2 样本仍小（16 天 / 12 簇），且该态自身基准高达 +5.72%
     ⇒ 与"不放宽 nb"的生产闸门结论不冲突（v0 不改任何状态），只在展示层提示。
  4. 未剔除「次日一字涨停买不进」⇒ 实证收益偏乐观（已扣 T+1 跳空，但未扣买不进）。

【接口】
  detect_events()        当日事件判定（盘中/盘后均可，依赖实时行情缓存）
  get_event_signal()     带缓存的事件信号（供 regime detail / API 调用）
  record_event_snapshot() 盘后落库 market_events（供未来实盘样本复核）
================================================================================
"""
import time
from datetime import datetime, timedelta, timezone

from app.database import db

_BEIJING_TZ = timezone(timedelta(hours=8))

# ── 阈值（改这里必须同步改脚本 + 留痕）──
# ★ 2026-09-27 经 P1 敏感性分析放宽 E2：up_ratio 0.95 → **0.90**。
#   依据 `scripts/event_threshold_sensitivity.py`（独立预登记 + Bonferroni）：
#   edge 几乎无损（+3.02pp → +2.99pp），触发 +65%（181 → 298 天），
#   2022/2023/2026 从「零事件」变为有事件，防御内增量 +1.83pp。
# ★ 2026-09-27 P1-b **绝对家数 → 比例口径**（`scripts/event_threshold_ratio.py`，独立预登记）：
#   `limit_up >= 50 家` 的严格度随池子大小漂移（zzshare 日样本中位 2444 只 /
#   生产实时快照 3236 只 / 全市场 5557 只各不相同）—— 缩池后触发天数**偏离 62~82%**；
#   比例口径同测试下仅偏离 **2.5~3.0%** ⇒ 生产与回测可迁移。
#   代价：edge +2.99pp → **+2.47pp**（仍 PASS，P=0.0006，Bonferroni 校正后）。
#   ⚠️ E1 的 `limit_down >= 200 家` 同样存在漂移，但其主判据是 `up_ratio <= 0.10`
#      （比例量、无漂移），且 E1 仅作背景提示 ⇒ 暂不改（改动须另行验证）。
E1_LIMIT_DOWN = 200
E1_UP_RATIO = 0.10
E2_UP_RATIO = 0.90
E2_LIMIT_UP_RATIO = 0.020     # 涨停家数 / (涨家数 + 跌家数) ≥ 2.0%
# ★ E4 涨停家数激增（2026-09-27 新增，时间切分样本外预登记复核通过）——
#   判据：当日涨停家数 > 2.0 × 滚动20日均值（自适应阈值，规避绝对家数随池子漂移）。
#   与 E2 是**同一机制**（涨停潮→中小盘风险偏好回升），但用更简单的"涨停家数"指标，
#   样本外 edge 更强（中证1000 ETF T+20 +5.82pp，P≈0.0004，见 E4_STATS）。
E4_LIMIT_UP_BOOST = 2.0       # 涨停家数 > 2.0 × 滚动20日均值
E4_MA_WINDOW = 20             # 滚动均值窗口（交易日）

# ── E2 历史统计（预登记检验产出，供展示「历史预期」；重跑脚本后同步更新）──
# ★ 2026-09-27 P1-b：更新为**比例口径** (up_ratio≥0.90 且 lu_ratio≥2.0%) 的统计。
#   演进记录：(0.95, 50家) 181/54/+6.17%/+3.02pp
#            → (0.90, 50家) 298/62/+6.14%/+2.99pp（P1 放宽）
#            → (0.90, 2.0%) 331/60/+5.62%/+2.47pp（P1-b 比例化，可迁移）
E2_STATS = {
    "n": 331, "n_clusters": 60, "window": "2005-02-02 ~ 2026-07-27",
    "h20_mean": 5.62, "h20_base": 3.15, "h20_diff": 2.47, "h20_win": 70.4,
    "p_leq0": 0.0,
    "by_regime": {
        "defensive": {"n": 126, "mean": 3.87, "base": 2.50, "diff": 1.37},
        "neutral_bearish": {"n": 17, "mean": 5.88, "base": 5.72, "diff": 0.16},
        "neutral": {"n": 61, "mean": 5.41, "base": 2.31, "diff": 3.11},
        "offensive": {"n": 85, "mean": 7.23, "base": 3.30, "diff": 3.94},
    },
    "source": "scripts/event_edge_check.py --up-ratio 0.90 --limit-up-ratio 0.02"
              "（2026-09-27，P1-b 比例化）",
}

# ── E2 可执行标的（§8.4 真实指数终验 + 成本口径，2026-09-27）──
#   口径：事件日 T → **T+1 开盘买 → T+20 收盘卖**，已扣双边 0.3% 成本；
#   主口径固定 T+20（与全部验证一致）；**不加止损**（实测 -7% 止损为负贡献 -0.22pp）。
#   ⚠️ 定位：**历史统计参考**，不是投资建议，不进决策链（展示层新增，须保留此声明）。
EXEC_STATS = {
    "hold_days": 20,
    "cost_pct": 0.3,
    "targets": [
        {"name": "中证1000", "etf": "512100", "net_edge": 2.46, "p": 0.0012, "n": 143},
        {"name": "中证500", "etf": "510500", "net_edge": 1.73, "p": 0.0011, "n": 298},
        {"name": "创业板指", "etf": "159915", "net_edge": 1.23, "p": 0.0273, "n": 182},
    ],
    "note": "沪深300 扣成本后转负(-0.18pp)；止损 -7% 为负贡献，未采用",
    "source": "scripts/event_realindex_check.py + event_hold_cost_check.py（2026-09-27）",
}

# ── E4 涨停家数激增的历史统计（★ 时间切分样本外预登记，2026-09-27）──
# 判据：涨停家数 > 2.0 × 滚动20日均值；标的=中证1000 ETF；T+1 开盘买、持 20 日。
# 复核：训练段(2005-2015)扫 boost 定参 → boost=2.0 最优(+5.95pp) → 测试段(2016-2026)
#       样本外 +5.82pp（P=0.0004，30 簇）⇒ 两段几乎一致，非过拟合。
# 中证500 阈值不稳定（训练段最优 3.0 但测试段样本不足）；创业板全程弱（最优 +0.80pp）。
# ★ 定位：历史统计参考，非投资建议，不进决策链（与 E2 同 v0 边界）。
E4_STATS = {
    "name": "涨停家数激增（涨停家数 > 2.0×滚动20日均值）",
    "threshold": "limit_up > 2.0 × MA20(limit_up)",
    "target": "中证1000 ETF (512100)",
    "hold_days": 20,
    "train": {"period": "2005-2015", "edge_pp": 5.95},
    "test": {"period": "2016-2026", "edge_pp": 5.82, "p": 0.0004, "n_clusters": 30},
    "note": "中证500 阈值不稳（3.0 样本不足）；创业板无效；ETF 有跟踪误差未扣",
    "source": "scripts/etf_timing_prereg.py + etf_timing_sensitivity.py（2026-09-27）",
}

_CACHE = {"ts": 0.0, "data": None}
_CACHE_TTL = 300          # 5 分钟（盘中事件状态可能变化）


def _ensure_table() -> None:
    """market_events 表（幂等）—— 每日一条事件快照，供未来实盘复核。"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS market_events (
            date TEXT PRIMARY KEY,
            up_ratio REAL,
            limit_up INTEGER,
            limit_down INTEGER,
            n_up INTEGER,
            n_down INTEGER,
            e1_capitulation INTEGER,
            e2_policy_surge INTEGER,
            e4_limit_surge INTEGER DEFAULT 0,
            limit_up_ma20 REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # ★ E4 加列（兼容已存在的旧表：CREATE IF NOT EXISTS 不会给已有表补列）
    for _col, _ddl in [("e4_limit_surge", "INTEGER DEFAULT 0"),
                       ("limit_up_ma20", "REAL")]:
        try:
            db.execute(f"ALTER TABLE market_events ADD COLUMN {_col} {_ddl}")
        except Exception:
            pass    # 列已存在
    try:
        from app.flash.rules import beijing_now
        _ = beijing_now
    except Exception:
        pass


def detect_events(force: bool = False) -> dict:
    """当日事件判定（依赖实时行情缓存的当日全市场宽度）。

    返回：
      {available, date, up_ratio, limit_up, limit_down, n_up, n_down,
       e1_capitulation, e2_policy_surge, e2_stats, note}
    行情缓存为空（重启/盘前）时 available=False，各事件为 False（宁缺勿错）。
    """
    if not force and _CACHE["data"] and time.time() - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["data"]

    from app.backtest.market_regime import _market_breadth_now
    br = _market_breadth_now() or {}
    up = br.get("up") or 0
    down = br.get("down") or 0
    limit_up = br.get("limit_up") or 0
    limit_down = br.get("limit_down") or 0
    up_ratio = br.get("up_ratio")

    available = bool(br) and (up + down) > 0
    denom = up + down
    lu_ratio = (limit_up / denom) if denom else 0.0
    e1 = bool(available and (limit_down >= E1_LIMIT_DOWN
                             or (up_ratio is not None and up_ratio <= E1_UP_RATIO)))
    # ★ P1-b：比例口径（池子无关）；见常量区注释
    e2 = bool(available and up_ratio is not None
              and up_ratio >= E2_UP_RATIO and lu_ratio >= E2_LIMIT_UP_RATIO)

    # ★ 日期口径（项目规范）：凡"数据所属日期"一律用**交易日**口径，绝不用自然日。
    #   实测踩到：周日返回 09-27，而宽度数据实际来自 09-24 收盘快照（周日/节假/盘前都会错位）。
    try:
        from app.flash.rules import latest_completed_trading_day
        today = latest_completed_trading_day()
    except Exception:
        today = datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d")

    # ★ E4：涨停家数 > 2.0 × 滚动20日均值（滚动均值用 market_events 历史 + 当日，
    #   含当日、无前视；需至少 19 天历史才开始判定，早期自动 insufficient）
    lu_ma20 = None
    try:
        _ensure_table()
        _hist = db.fetch(
            "SELECT limit_up FROM market_events WHERE date < %s "
            "ORDER BY date DESC LIMIT 19", (today,)) or []
        if len(_hist) >= E4_MA_WINDOW - 1:
            lu_ma20 = (sum(r["limit_up"] for r in _hist) + limit_up) / float(E4_MA_WINDOW)
    except Exception as e:
        print(f"[events] E4 滚动均值读取失败: {e}")       # ASCII（铁律⑥）
    e4 = bool(available and lu_ma20 is not None
              and limit_up > E4_LIMIT_UP_BOOST * lu_ma20)

    result = {
        "available": available,
        "date": today,
        "up_ratio": round(up_ratio, 4) if up_ratio is not None else None,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "limit_up_ratio": round(lu_ratio, 4),
        "n_up": up,
        "n_down": down,
        "e1_capitulation": e1,
        "e2_policy_surge": e2,
        "e2_stats": E2_STATS if e2 else None,
        # ★ 可执行标的（仅 E2 触发时给出；历史统计参考，非投资建议）
        "exec_stats": EXEC_STATS if e2 else None,
        # ★ E4 涨停家数激增（历史统计参考，非投资建议，不进决策链）
        "e4_limit_surge": e4,
        "limit_up_ma20": round(lu_ma20, 2) if lu_ma20 is not None else None,
        "e4_stats": E4_STATS if e4 else None,
        "note": ("盘中口径：涨停用 change_pct>=9.9 近似（20cm 板块会高估家数），"
                 "与回测的精确口径略有差异；本版仅展示，不进决策链"
                 if available else "行情缓存为空，事件不可判定"),
    }
    _CACHE.update(ts=time.time(), data=result)
    return result


def get_event_signal() -> dict:
    """供 regime detail / API 调用的事件信号（异常时返回 available=False）。"""
    try:
        return detect_events()
    except Exception as e:
        print(f"[events] 事件判定失败: {e}")
        return {"available": False, "e2_policy_surge": False, "e1_capitulation": False,
                "note": f"事件判定异常: {e}"}


def record_event_snapshot(date: str = None) -> int:
    """盘后落库当日事件快照（幂等覆盖）。返回写入行数。"""
    ev = detect_events(force=True)
    if not ev.get("available"):
        return 0
    d = date or ev["date"]
    try:
        _ensure_table()
        db.execute("""
            INSERT INTO market_events
            (date, up_ratio, limit_up, limit_down, n_up, n_down,
             e1_capitulation, e2_policy_surge, e4_limit_surge, limit_up_ma20)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date) DO UPDATE
            SET up_ratio = EXCLUDED.up_ratio, limit_up = EXCLUDED.limit_up,
                limit_down = EXCLUDED.limit_down, n_up = EXCLUDED.n_up,
                n_down = EXCLUDED.n_down, e1_capitulation = EXCLUDED.e1_capitulation,
                e2_policy_surge = EXCLUDED.e2_policy_surge,
                e4_limit_surge = EXCLUDED.e4_limit_surge,
                limit_up_ma20 = EXCLUDED.limit_up_ma20
        """, (d, ev["up_ratio"], ev["limit_up"], ev["limit_down"],
              ev["n_up"], ev["n_down"],
              1 if ev["e1_capitulation"] else 0,
              1 if ev["e2_policy_surge"] else 0,
              1 if ev["e4_limit_surge"] else 0,
              ev["limit_up_ma20"]))
        print(f"[events] {d} 事件快照已落库"
              f"（E2={ev['e2_policy_surge']}, E4={ev['e4_limit_surge']}）")
        return 1
    except Exception as e:
        print(f"[events] 落库失败: {e}")
        return 0


def event_summary_line() -> str:
    """一行摘要（日报/LLM prompt 复用）。无事件返回空串。"""
    ev = get_event_signal()
    if not ev.get("available"):
        return ""
    ur = ev.get("up_ratio")
    ur_s = f"{ur:.0%}" if ur is not None else "—"
    if ev.get("e2_policy_surge"):
        s = E2_STATS
        _def = s["by_regime"]["defensive"]
        _ex = (ev.get("exec_stats") or {}).get("targets") or []
        _ex_line = ""
        if _ex:
            _top = _ex[0]
            _ex_line = (f" 历史同态可执行标的（扣 0.3% 成本、持 20 日）："
                        f"{_top['name']}({_top['etf']}) +{_top['net_edge']}pp"
                        f"（P={_top['p']}）等 {len(_ex)} 个；沪深300 无效。")
        return (f"⚡ 政策脉冲信号（E2）：涨家数占比 {ur_s}、涨停 {ev['limit_up']} 家 —— "
                f"历史同态后 20 日等权 +{s['h20_mean']}%（基准 +{s['h20_base']}%，"
                f"n={s['n']}，胜率 {s['h20_win']}%）；防御期内增量 "
                f"+{_def['diff']}pp（n={_def['n']}）。" + _ex_line +
                f" ⚠️ 展示项（历史统计参考，非投资建议），未进决策链")
    if ev.get("e4_limit_surge"):
        _s = E4_STATS
        return (f"🔥 涨停家数激增（E4）：涨停 {ev['limit_up']} 家 > 2.0×滚动20日均值"
                f"（{ev.get('limit_up_ma20')}）—— 历史同态中证1000 ETF 持 20 日 "
                f"训练段 +{_s['train']['edge_pp']}pp / 样本外 +{_s['test']['edge_pp']}pp"
                f"（P={_s['test']['p']}）。⚠️ 展示项（历史统计参考，非投资建议），未进决策链")
    if ev.get("e1_capitulation"):
        return (f"❄️ 冰点信号（E1）：涨家数占比 {ur_s}、跌停 {ev['limit_down']} 家 —— "
                f"历史上无额外 edge（已归档，仅作背景提示）")
    return f"当日无事件信号（涨家数占比 {ur_s}、涨停 {ev['limit_up']}、跌停 {ev['limit_down']}）"
