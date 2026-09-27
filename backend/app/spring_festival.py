# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】春节「日历例外」—— 防御市里唯一被验证过的可用窗口（P2-a 落地）
================================================================================

【为什么有这个东西】
  项目长期结论：**regime 只看沪深300 均线 => 在 V 型反转处系统性滞后**，
  而 E2/E3 等宽度事件要么极稀有（2026 年 0 次）、要么无 edge（E3 WEAK）。
  => 用户在 2026-09-16（反弹第一天）问"什么情况下会买"，答案是"三条路径全失效"。

  用户随后提出方向：**事件驱动 / 提前预案**。查证发现——
    · `flash_calendar`（金十）只存 15 天窗口，**无历史积累** => 「财经日历事件 vs 反弹日」
      的相关性**无法回溯验证**；
    · 事件"利好/利空"方向**无客观映射**（同一 FOMC 随预期差反向）；
    · 系统**没有任何"事件前布局"机制**（`risk_events` 只做负面避险）。
  => 退化为**日期确定的固定日历效应**，先做春节（零数据成本、日期可自动定位）。

【★★ 验证依据（2026-09-27，脚本 scripts/spring_festival_*.py，判据预登记）】
  定位：用「1~2 月最长休市缺口」自动定位春节，**实测 22/22 与农历正月初一吻合**。

  主检验（`spring_festival_effect.py`，20 次检验 / Bonferroni α=0.0025 / 置换 20000 次）：
    基准 up_ratio = 0.5113 => **T+2 +18.59pp（P=0.0003, 18/22）｜ T+3 +19.13pp（P=0.0001, 17/22）**
    ★ `lu_ratio`（涨停比例）**全部不显著** => 春节后是「**普涨**」（宽度高），
      **与 E2 的「涨停潮」机制相反**——两个信号互补。

  机制（`spring_festival_returns.py`）：**只是中小盘现象**
    中证500 T+3 **+2.35%（P=0.0011）**｜中证1000 T+3 **+2.76%（P=0.0030）**
    ｜**沪深300 T+3 +0.68%（P=0.17，不显著）**

  证伪对照（`spring_festival_falsify.py`，中证500 节前买持 3 日）：
    春节 **+2.34%（P=0.0011, 17/20）** vs **国庆 −0.27%（P=0.72, 8/19）** vs 五一 +0.72%（P=0.22）
    => ✅ **排除「长假效应」**（国庆同为长假却完全无效）=> 春节是**特有**的。

  可操作性（`spring_festival_entry.py` + `spring_festival_robust.py`）：
    · **无需持股过节**：节后首日跳空仅 **+0.18%**
    · 「**节后 T+1 收盘买入持 3 日**」：中证500 **+1.75%（扣 0.10% 双边成本 +1.65%，
      P=0.0144，17/20）**；中证1000 **+2.56%（P=0.0050，12/12）**
    · ✅ **2020 疫情没受伤**（该口径 2020 = **+0.45%**，剔除后 +1.78% ≈ +1.75%）——
      「节后买入」天然规避"节后首日暴跌"
    · ✅ **不衰减**：中证500 **2016~2026 = 11/11**、近 10 年 10/10、近 5 年 5/5
      ⚠️ 唯一大亏年 **2007（−6.91%）**＝「2·27 大跌」当天

【★ 诚实局限（必须与本模块一起披露）】
  · **无法与「2 月春季躁动」分离**：年内序数铺开后 18~30 号是**整片高地**
    （春节区间 +1.07% vs 其他 +0.09%），且该观测**自相关**，不能做独立推断。
    => 本效应可能是「春节 + 2 月季节性」的叠加。**从可操作性看两者等价**（都需在 2 月初布局）。
  · **沪深300 无效** => 只给中小盘宽基，**不给大盘标的**。
  · 中证1000 仅 12 个样本（2014 起）。
  · 成本按双边 0.10% 估（佣金+滑点），实际以券商为准。

【定位（★ 与 E2 一致的口径纪律）】
  本模块**只产出「决策卡的一个独立字段」**，**不修改 `trade_gate.evaluate`**：
    · 不改 `条件C`（`REGIME_ALLOWED`）—— 那是"买入资格唯一事实源"，被评分榜 50 只
      与观察池复用，改它会污染展示口径；
    · 不改 `REGIME_POSITION`（仓位档位唯源）。
  理由：E2 的教训（"口径唯一事实源"）与项目纪律（"不造未经检验的闸门"）。
  => 本例外是**叠加在决策卡上的提示通道**，用户自行决定是否采纳。

【日期来源】
  主：**硬编码春节表**（正月初一 = 天文事实；与 `flash.rules.HOLIDAYS` 的硬编码惯例一致）。
  辅：`flash_calendar` 缓存的**实际 A 股休市日**（只读缓存、不触发网络）用于**临期校正**。
  ⚠️ 节假日调休区间由国务院逐年公告 => **远期只能给"预计窗口"（±2 自然日）**；
    临近时若日历缓存已含该年数据，则升级为 `precision="actual"`。
================================================================================
"""

from datetime import date, timedelta

# ================================================================
#  一、春节（农历正月初一）公历日期表
# ================================================================
# 2005~2026：由本项目交易日历「1~2 月最长休市缺口」自动定位并**逐日核对**（22/22 吻合）。
# 2027~2040：多源交叉核对（注明：远期个别年份若新月时刻贴近午夜，不同机构历表可能
#           存在**一日之差**，正式历书以紫金山天文台颁历为准）=> 生产使用前建议复核。
SPRING_FESTIVAL = {
    2005: "2005-02-09", 2006: "2006-01-29", 2007: "2007-02-18", 2008: "2008-02-07",
    2009: "2009-01-26", 2010: "2010-02-14", 2011: "2011-02-03", 2012: "2012-01-23",
    2013: "2013-02-10", 2014: "2014-01-31", 2015: "2015-02-19", 2016: "2016-02-08",
    2017: "2017-01-28", 2018: "2018-02-16", 2019: "2019-02-05", 2020: "2020-01-25",
    2021: "2021-02-12", 2022: "2022-02-01", 2023: "2023-01-22", 2024: "2024-02-10",
    2025: "2025-01-29", 2026: "2026-02-17", 2027: "2027-02-06", 2028: "2028-01-26",
    2029: "2029-02-13", 2030: "2030-02-03", 2031: "2031-01-23", 2032: "2032-02-11",
    2033: "2033-01-31", 2034: "2034-02-19", 2035: "2035-02-08", 2036: "2036-01-28",
    2037: "2037-02-15", 2038: "2038-02-04", 2039: "2039-01-24", 2040: "2040-02-12",
}

# ── 窗口参数（来自 P2-a 实测，非拍脑袋）──────────────────────────────
# 「节后 T+1 收盘买入持 3 日」是最优可执行口径（中证500 +1.65% 扣成本后，
#   P=0.0144、17/20 正收益；无需持股过节）=> 预警窗给到 3 周，让用户有准备时间。
PRE_ALERT_DAYS = 21          # 节前预警：距「节前最后交易日」≤ 21 自然日（约 15 个交易日）
POST_WATCH_DAYS = 14         # 节后跟踪：距「节后第一个交易日」≤ 14 自然日（约 10 个交易日，覆盖 T+1~T+5）
HOLD_DAYS = 3                # 建议持有交易日（P2-a 最优：T+1 收盘买、持 3 日）
DEFAULT_PCT = 20             # 建议仓位（％）—— 防御档原为 ≤10%，本例外临时放宽
# ★ 标的（与 P2-a 机制一致：**只给中小盘宽基**，沪深300 已证伪不列）
TARGETS = (
    {"code": "510500", "name": "中证500ETF", "note": "T+3 +2.35%（P=0.0011）｜样本 20 年"},
    {"code": "512100", "name": "中证1000ETF", "note": "T+3 +2.76%（P=0.0030）｜样本 12 年"},
)

# ★ 远期预估口径（见 `resolve_window` 分支 ③）：用「除夕 ~ 初六」的**固定假期区间**
#   推导前后的最近交易日，**口径与 ①② 完全一致**。
#   为什么不用"固定偏移量"：实测会产生"节后首日落在假期区间内"的自相矛盾
#   （曾用 `fest+6` 作 first_td，而 holiday_end 也取 `fest+6` ⇒ 节后首日 = 假期末日
#   ⇒ `action=buy` 永远不出现）；且调休由国务院逐年公告、假期长短每年不同
#   （实测单日误差可达 ±3 天：2005 节前差 +3、2017/2024 节后差 +3）⇒ 远期只作预警。


def _d(s: str) -> date:
    y, m, dd = (int(x) for x in s.split("-"))
    return date(y, m, dd)


def _iso(d: date) -> str:
    return d.isoformat()


def _festival(year: int):
    """该年春节（正月初一）；无数据返回 None。"""
    s = SPRING_FESTIVAL.get(year)
    return _d(s) if s else None


def _rules_holiday_range(year: int):
    """从 `flash.rules.HOLIDAYS` 取该年**春节假期区间**（项目权威表，逐年维护）。

    为什么优先用它：`HOLIDAYS` 是项目"交易日判定"的唯一依据（`is_trading_day` 依赖它），
    按年追加、来源为交易所公告 => 一旦维护了当年数据，定位即为**精确值**（无需等日历缓存）。
    """
    try:
        from app.flash.rules import HOLIDAYS
        for (lo, hi, name) in HOLIDAYS.get(year, []):
            if "春节" in (name or ""):
                return (date(year, *lo), date(year, *hi))
    except Exception as e:
        print(f"[spring_festival] rules HOLIDAYS read failed: {e}")    # ASCII（铁律⑥）
    return None


# ================================================================
#  二、A 股休市日（只读 flash_calendar 缓存，不触发网络）
# ================================================================

def _calendar_holidays() -> set:
    """金十日历缓存里的 A 股（沪深/北交所）休市日集合。失败返回空集。

    ★ 只读 `calendar.load()`（**不**调用 refresh）—— 决策卡是"只读不写库不发推送"的路径，
      绝不能因为这里而引入网络请求。
    """
    try:
        from app.flash import calendar as cal
        items = (cal.load() or {}).get("items") or []
    except Exception as e:
        print(f"[spring_festival] calendar read failed: {e}")   # ASCII（铁律⑥）
        return set()
    out = set()
    for it in items:
        if (it.get("kind") or "") != "holiday":
            continue
        ex = it.get("exchange") or ""
        if "沪深" in ex or "北交所" in ex:
            dt = it.get("date") or ""
            if dt:
                out.add(dt[:10])
    return out


# 休市日集合的进程内缓存（5 分钟）：决策卡一次调用会经 `window`/`resolve_window`
# 反复用到它，而它每次都要读 `flash_calendar`（一次 DB 查询）+ 展开 `HOLIDAYS`。
# 与项目既有惯例一致（`trade_gate._contra_cache` 30s / `trader_brief._STRAT_Q_CACHE` 10min）。
_hol_cache = {"ts": 0.0, "val": None}
_HOL_TTL = 300.0


def _all_holidays() -> set:
    """全部已知 A 股休市日 = 金十日历缓存 ∪ `flash.rules.HOLIDAYS`（区间展开）。

    ★ 为什么合并两源：`HOLIDAYS` 是项目**交易日判定的唯一依据**（`is_trading_day` 依赖它），
      来源为交易所公告且按年维护 —— 一旦维护了当年数据，定位即为精确值；
      而金十日历是"随时可重拉"的补充源（可能比 `HOLIDAYS` 更早拿到次年数据）。
      两者取并集 => **谁先有数据谁生效**，且 `_is_td` 的判定口径同时与项目一致。
    """
    import time as _t
    _now = _t.time()
    if _hol_cache["val"] is not None and _now - _hol_cache["ts"] < _HOL_TTL:
        return _hol_cache["val"]
    days = set(_calendar_holidays())
    try:
        from app.flash.rules import HOLIDAYS
        for y, ranges in (HOLIDAYS or {}).items():
            for (lo, hi, _n) in (ranges or []):
                try:
                    d0, d1 = date(int(y), *lo), date(int(y), *hi)
                except Exception:
                    continue
                x = d0
                while x <= d1:
                    days.add(_iso(x))
                    x += timedelta(days=1)
    except Exception as e:
        print(f"[spring_festival] HOLIDAYS expand failed: {e}")        # ASCII（铁律⑥）
    _hol_cache.update({"ts": _now, "val": days})
    return days


def _longest_gap(days: set):
    """把休市日集合聚成连续区间，返回最长区间 (start_date, end_date)；无则 None。"""
    if not days:
        return None
    ds = sorted(_d(x) for x in days if x)
    best, cur_s, cur_e = None, ds[0], ds[0]
    for x in ds[1:]:
        if (x - cur_e).days <= 3:        # 跨周末也算同一次连休
            cur_e = x
        else:
            if best is None or (cur_e - cur_s).days > (best[1] - best[0]).days:
                best = (cur_s, cur_e)
            cur_s = cur_e = x
    if best is None or (cur_e - cur_s).days > (best[1] - best[0]).days:
        best = (cur_s, cur_e)
    return best if (best[1] - best[0]).days >= 5 else None    # 春节连休必 ≥7 天


def _is_td(d: date, holidays: set) -> bool:
    """交易日判定：非周末 + 非日历休市日（含未来年份，只要缓存有）。"""
    if d.weekday() >= 5:
        return False
    return _iso(d) not in holidays


def _prev_td(d: date, holidays: set) -> date:
    x = d - timedelta(days=1)
    for _ in range(20):
        if _is_td(x, holidays):
            return x
        x -= timedelta(days=1)
    return x


def _next_td(d: date, holidays: set) -> date:
    x = d + timedelta(days=1)
    for _ in range(20):
        if _is_td(x, holidays):
            return x
        x += timedelta(days=1)
    return x


# ================================================================
#  三、窗口定位（实际优先，估算兜底）
# ================================================================

def resolve_window(year: int) -> dict:
    """定位该年春节窗口：节前最后交易日 / 节后第一个交易日。

    精度：`actual`（日历缓存已含该年 A 股休市数据）> `estimate`（硬编码 + 经验偏移 ±2 天）。

    返回 {year, festival, last_td, first_td, holiday_start, holiday_end, precision, source}
    """
    fest = _festival(year)
    if not fest:
        return {"year": year, "festival": None, "last_td": None, "first_td": None,
                "holiday_start": None, "holiday_end": None,
                "precision": None, "source": None, "confidence": None}

    holidays = _all_holidays()

    def _mk(hs: date, he: date, source: str) -> dict:
        return {"year": year, "festival": _iso(fest),
                "holiday_start": _iso(hs), "holiday_end": _iso(he),
                "last_td": _iso(_prev_td(hs, holidays)),
                "first_td": _iso(_next_td(he, holidays)),
                "precision": "actual", "source": source,
                "confidence": "精确（按交易所公告的休市区间定位）"}

    # ① 项目权威表 `flash.rules.HOLIDAYS`（按年维护，`is_trading_day` 同源）
    rng = _rules_holiday_range(year)
    if rng and rng[0] <= fest <= rng[1]:
        return _mk(rng[0], rng[1], "flash.rules.HOLIDAYS")

    # ② 金十日历缓存里的实际 A 股休市区间
    cal_days = _calendar_holidays()
    gap = _longest_gap({h for h in cal_days if h[:4] == str(year)})
    if gap and gap[0] <= fest <= gap[1]:
        return _mk(gap[0], gap[1], "flash_calendar")

    # ③ 兜底：硬编码春节 + **假期区间推导**（★ 调休逐年公告 => 单日误差可达 ±3 天，
    #    只作远期预警；临近必须以交易所休市公告为准）。
    #    ★ 口径与 ①② **完全一致**（都是「假期起止 → 前后最近交易日」），
    #      否则会出现"first_td 落在假期区间内"的自相矛盾（实测踩到：曾用固定偏移
    #      `fest+6`，而 holiday_end 也取 `fest+6` ⇒ 节后首日 = 假期末日 ⇒
    #      `action=buy` 永远不出现，因为那天被判成 holiday）。
    est_start = fest - timedelta(days=1)          # 除夕（假期首日）
    est_end = fest + timedelta(days=6)            # 初六（假期末日）
    return {"year": year, "festival": _iso(fest),
            "holiday_start": _iso(est_start), "holiday_end": _iso(est_end),
            "last_td": _iso(_prev_td(est_start, holidays)),
            "first_td": _iso(_next_td(est_end, holidays)),
            "precision": "estimate", "source": "hardcoded",
            "confidence": "预估（调休逐年公告，实测单日误差可达 ±3 天）"}


def _td_span(a: date, b: date, holidays: set) -> int:
    """(a, b] 之间的交易日数（含 b、不含 a）。b < a 时返回负数。"""
    if b == a:
        return 0
    sign = 1
    if b < a:
        a, b, sign = b, a, -1
    n, x = 0, a + timedelta(days=1)
    while x <= b:
        if _is_td(x, holidays):
            n += 1
        x += timedelta(days=1)
    return n * sign


# ================================================================
#  四、主入口
# ================================================================

def window(today: date = None) -> dict:
    """当前是否处于春节窗口，以及处于哪一段。

    phase: pre（节前预警）｜ holiday（休市中）｜ post（节后跟踪）｜ idle
    """
    if today is None:
        try:
            from app.flash.rules import beijing_now
            today = beijing_now().date()
        except Exception:
            today = date.today()

    holidays = _all_holidays()
    out = {"available": False, "phase": "idle", "today": _iso(today),
           "year": None, "win": None, "days_to_last_td": None,
           "post_index": None, "hold_days_left": None}

    # 逐候选年份匹配（跨年窗口：12 月也要能看到次年春节）
    for y in (today.year, today.year + 1):
        win = resolve_window(y)
        if not win.get("festival"):
            continue
        fest = _d(win["festival"])
        last_td = _d(win["last_td"])
        first_td = _d(win["first_td"])
        hs, he = _d(win["holiday_start"]), _d(win["holiday_end"])

        # 节前预警窗（以「节前最后交易日」为锚，往前 PRE_ALERT_DAYS 自然日）
        if last_td - timedelta(days=PRE_ALERT_DAYS) <= today <= last_td:
            out.update({"available": True, "phase": "pre", "year": y, "win": win,
                        "days_to_last_td": _td_span(today, last_td, holidays)})
            return out
        # 休市中
        if hs <= today <= he:
            out.update({"available": True, "phase": "holiday", "year": y, "win": win})
            return out
        # 节后跟踪窗
        if first_td <= today <= first_td + timedelta(days=POST_WATCH_DAYS):
            idx = _td_span(first_td, today, holidays) + 1     # 节后第 N 个交易日
            out.update({"available": True, "phase": "post", "year": y, "win": win,
                        "post_index": idx,
                        "hold_days_left": max(0, HOLD_DAYS - idx)})
            return out
    return out


def calendar_exception(regime: str = None, today: date = None) -> dict:
    """决策卡字段：春节「日历例外」（**只读、无副作用、失败静默降级**）。

    返回结构稳定（前端依赖），关键字段：
      active / phase / title / note / targets / position_hint / evidence / caveat
      · `overrides`：本次例外放宽的市况（仅当 regime 为 defensive/neutral_bearish 时给出）
    """
    out = {"available": False, "active": False, "phase": "idle",
           "year": None, "festival": None, "last_td": None, "first_td": None,
           "precision": None, "action": "none", "title": "", "note": "",
           "targets": [], "position_hint": None, "hold_days": HOLD_DAYS,
           "overrides": None, "evidence": None, "caveat": None, "regime": regime}
    try:
        w = window(today)
        if not w.get("available"):
            return {**out, "available": True, "note": "当前不在春节窗口内"}
        win = w["win"]
        out.update({"available": True, "active": True, "phase": w["phase"],
                    "year": w["year"], "festival": win.get("festival"),
                    "last_td": win.get("last_td"), "first_td": win.get("first_td"),
                    "precision": win.get("precision")})

        # 只在"防御/偏空"市况下才谈"例外"——市况本来允许时无需例外（避免自相矛盾）
        reg = (regime or "").strip()
        is_blocked = reg in ("defensive", "neutral_bearish")
        if w["phase"] == "pre":
            out["action"] = "watch"
            out["title"] = f"春节窗口临近（{w['year']} 春节 {win['festival']}）"
            out["note"] = (f"距节前最后交易日约 {w['days_to_last_td']} 个交易日。"
                           f"历史（2005~2026，22 次）节后 T+1 收盘买入持 "
                           f"{HOLD_DAYS} 日，中证500 平均 +1.75%（扣 0.10% 成本 +1.65%，"
                           f"P=0.0144，17/20 为正）=> 提前准备资金，节后开盘再决策。")
        elif w["phase"] == "holiday":
            out["action"] = "hold"
            out["title"] = "春节休市中"
            out["note"] = (f"休市期间无法交易。节后第一个交易日为 {win['first_td']}；"
                           f"历史最优口径为节后 T+1 收盘买入（避开首日跳空，跳空仅 +0.18%）。")
        else:  # post
            idx = w.get("post_index") or 1
            left = w.get("hold_days_left")
            out["action"] = "buy" if idx == 1 else ("hold" if (left or 0) > 0 else "exit")
            out["title"] = f"春节后第 {idx} 个交易日"
            if idx == 1:
                out["note"] = (f"今天是节后第 1 个交易日，收盘即历史最优介入点（P2-a）："
                               f"持有 {HOLD_DAYS} 个交易日。历史中证500 +1.75%、"
                               f"中证1000 +2.56%（扣成本后 +1.65%/+2.46%）。")
            elif (left or 0) > 0:
                out["note"] = (f"已过 {idx} 个交易日，按 {HOLD_DAYS} 日口径还剩 {left} 个"
                               f"交易日到期。新增买入已偏离最优入场点（历史 edge 集中在 T+1~T+3）。")
            else:
                out["note"] = (f"已过 {idx} 个交易日，超出 {HOLD_DAYS} 日持有口径 => "
                               f"按纪律了结，不追。")

        # ★ 精度披露（必须）：预估日期要让用户看得到不确定性 ——
        #   春节调休区间由国务院**逐年公告**，实测单日误差可达 ±3 天（2005 节前差 +3、
        #   2017 节后差 +3、2024 节后差 +3）。若不披露，用户会按错日子操作。
        if out.get("precision") == "estimate":
            out["note"] += ("  ※ 该日期为预估（调休逐年公告，实测单日误差可达 ±3 天）；"
                            "临近请以交易所休市公告为准。")

        if out["action"] == "buy":
            out["targets"] = [dict(t) for t in TARGETS]
            out["position_hint"] = {
                "pct": DEFAULT_PCT,
                "label": "日历例外·轻仓",
                "note": (f"防御档原上限 ≤10%；本例外临时放宽至 {DEFAULT_PCT}%（单一宽基 ETF），"
                         f"持有 {HOLD_DAYS} 个交易日后了结"),
            }
        if is_blocked and out["action"] in ("buy", "watch"):
            out["overrides"] = reg

        out["evidence"] = {
            "sample_years": "2005~2026（22 次春节，天然独立）",
            "main_test": "up_ratio T+3 +19.13pp（P=0.0001，17/22），Bonferroni α=0.0025",
            "index": {"中证500": "T+3 +2.35%（P=0.0011，16/20）",
                      "中证1000": "T+3 +2.76%（P=0.0030，11/12）",
                      "沪深300": "T+3 +0.68%（P=0.17，不显著 => 不给大盘标的）"},
            "entry": "节后 T+1 收盘买入持 3 日：中证500 +1.75%、中证1000 +2.56%（扣 0.10% 双边）",
            "robust": "中证500 2016~2026 11/11、近 10 年 10/10、近 5 年 5/5 为正",
            "falsify": "国庆 −0.27%（P=0.72）、五一 +0.72%（P=0.22）=> 非「长假效应」",
            "worst": "2007 −6.91%（「2·27 大跌」当天）",
            "scripts": "scripts/spring_festival_{effect,returns,falsify,entry,robust}.py",
        }
        out["caveat"] = ("★ 本例外不代表市况转好，只是「日期固定的日历效应」；"
                         "无法与「2 月春季躁动」完全分离；沪深300 无效；"
                         "中证1000 仅 12 年样本；请按 HOLD_DAYS 到期机械了结。")
        return out
    except Exception as e:
        print(f"[spring_festival] exception build failed: {e}")     # ASCII（铁律⑥）
        return {**out, "available": False, "note": "读取失败（不影响其他决策）"}


def rules() -> dict:
    """参数下发（供前端镜像展示，避免两处写死阈值 —— 与 `trade_gate.rules()` 同款）。"""
    return {
        "pre_alert_days": PRE_ALERT_DAYS,
        "post_watch_days": POST_WATCH_DAYS,
        "hold_days": HOLD_DAYS,
        "default_pct": DEFAULT_PCT,
        "targets": [dict(t) for t in TARGETS],
        "festival_table_years": [min(SPRING_FESTIVAL), max(SPRING_FESTIVAL)],
        "entry_rule": "节后第 1 个交易日收盘买入，持有 %d 个交易日" % HOLD_DAYS,
    }
