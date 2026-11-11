# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】竞价面板（auction panel）—— 竞价看板的三块增强数据（2026-11-08 用户需求）
================================================================================
  ① **昨日日报联动**：指数竞价开盘 vs 昨日关键位与"预期区间"
     （口径与昨日日报同一事实源：`key_level_watch` 支撑位 + `scenario_watch` 推演锚点
       —— 锚点"跌破下看 X / 收复 Y 算修复"自然构成一个 [下看, 修复] 预期区间；
       ⚠️ 区间只有上证有（锚点体系只定义了上证），其他指数只有关键位，不硬造区间）
  ② **板块联动**：`industry_map`（code→行业，月级重建）× 全市场竞价缺口 → 行业聚合
     （高开/低开板块榜 + 每板块领涨/领跌成员 = "板块与个股的联动关系"）
  ③ **LLM 解读**：面板 → 自然语言（强弱信号/资金情绪/风险/关注要点），
     按日缓存 + 数据指纹（sig），**只读 GET 出缓存、POST 才真烧**（与 obsAI/flash_brief 同款）。

★ 边界（诚实声明，写进模块头）：
  · 与 `market_emotion` 的 auction 块**同源同口径**（stocks = `tencent._cache`，
    gap = open/prev_close；`gap_date`/`gap_is_today` 语义一致 —— 非竞价窗口读到的
    是最近已完成交易日的开盘数据，前端已有"非当日口径"提示，此处复用同一判断）。
  · **竞价量比**（今竞价额 vs 昨竞价额）：9-25 评估时的缺口④，现在 `auction_total_amount_wan`
    已落库 ⇒ 可算；仅在 gap_is_today 且昨日有值时给出，否则 null（缺失 ≠ 0）。
  · LLM **只做性质判断**，不给买卖建议/仓位/目标价（与 daily_report L1 同一红线）；
    无 LLM 配置或调用失败 ⇒ 降级返回 ok=False，前端显示规则判读（auction.verdict 仍在）。
  · 零新增外部请求：指数实时价用 `key_level_watch._live_index_quotes()`（15s 进程缓存），
    其余全部来自既有缓存与库表。
================================================================================
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db
from app import key_level_watch as _klw
from app import scenario_watch as _scw
from app import industry_map as _imap

_BJ = timezone(timedelta(hours=8))

_LLM_TABLE = "auction_llm"
_LLM_READY = False
_LLM_VERSION = "2026-11-08.1"

_MIN_SECTOR_N = 5          # 行业成员少于该数不进板块榜（防小样本噪声）
_SECTOR_TOP = 8            # 高开/低开板块榜各取几个
_LEADER_N = 2              # 每板块展示几只领涨/领跌成员


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else None          # 过滤 NaN
    except (TypeError, ValueError):
        return None


def _bj_now() -> datetime:
    return datetime.now(_BJ)


# ══════════════════════════════════════════════════════════════════════════
# 取数
# ══════════════════════════════════════════════════════════════════════════
def _prev_day(day: str) -> Optional[str]:
    """gap_date 的前一交易日（用 market_emotion_daily 的日期序列做交易日历代理）。"""
    try:
        r = db.fetch_one("SELECT MAX(date) AS d FROM market_emotion_daily WHERE date < %s",
                         (str(day)[:10],))
        return str((r or {}).get("d") or "")[:10] or None
    except Exception:
        return None


def _stock_gaps() -> tuple:
    """全市场竞价缺口（与 market_emotion 的 auction 块同口径）。返回 (gaps, gap_date, has_live)。"""
    from app import tencent
    stocks = tencent._cache.get("stocks") or {}
    gap_date = tencent._price_date_now()
    gaps = []
    for code, s in (stocks or {}).items():
        pc, op = _f(s.get("prev_close")), _f(s.get("open"))
        if not pc or not op or pc <= 0 or op <= 0:
            continue
        amt = _f(s.get("amount_wan")) or 0.0
        gaps.append({"code": code, "name": s.get("name") or code,
                     "gap_pct": round((op / pc - 1) * 100, 2),
                     "amount_wan": round(amt)})
    return gaps, gap_date, bool(stocks)


def _index_open_px(code: str, gap_date: str, gap_is_today: bool,
                   live: Dict) -> tuple:
    """指数开盘价三级来源（★ 2026-11-08 修休市日空显示）：

      ① live（仅当日竞价/盘中口径，15s 进程缓存）→ open_src='live'
      ② `backtest_prices` 日批 K 线里 gap_date 当日 bar 的 open（**纯库读零外呼**，
         休市/盘后也能看到"那一天已完成的开局"，与个股用缓存 open 的口径对齐）
         → open_src='kline'
      ③ 都没有 → (None, None)（缺失 ≠ 0，前端显示 —）
    """
    if gap_is_today:
        lq = live.get(code) or {}
        lpx = _f(lq.get("price"))
        if lpx:
            return lpx, "live"
    try:
        from app.contradictions.scanner import _db_index_kline
        for b in (_db_index_kline(code, 6) or []):
            if str(b.get("date") or "")[:10] == str(gap_date)[:10]:
                op = _f(b.get("open"))
                if op:
                    return op, "kline"
    except Exception:  # noqa: except_pass  取不到开盘价就返回 (None,None) 让调用方换源，已有兜底
        pass
    return None, None


def _index_rows(prev_day: Optional[str], gap_is_today: bool,
                gap_date: str) -> List[Dict]:
    """指数行：昨日关键位（key_level_watch）+ 开盘价（live / K 线定稿）。"""
    try:
        kl = _klw.load(day=prev_day)
    except Exception as e:
        kl = {"rows": [], "note": str(e)[:80]}
    live = {}
    if gap_is_today:
        try:
            live = _klw._live_index_quotes() or {}
        except Exception:
            live = {}
    out = []
    for r in (kl.get("rows") or []):
        code = str(r.get("code") or "")
        prev_close = _f(r.get("close"))
        op_px, op_src = _index_open_px(code, gap_date, gap_is_today, live)
        gap_pct = round((op_px / prev_close - 1) * 100, 2) \
            if (op_px and prev_close) else None
        # ★★ 2026-11-08 防护（实测踩到）：key_level_watch 里中证500/中证全指的 close
        #   存的是错误量级（8.34/16.18，真实应为千点级）——既有数据 bug，缺口会算出
        #   +77983% 这种荒谬值。缺口超出 ±25%（超北交所 30cm 上限）一律判 suspect
        #   ⇒ gap 置 None（缺失 ≠ 荒谬值），不假装可信。
        # ★★ 2026-11-12（用户："中证全指 15.64、15.39前低，明显有问题"）：
        #   上面这条只防"缺口荒谬"，**防不住"昨收本身荒谬但算不出缺口"**：
        #   中证全指的 `open_price` 缺失 ⇒ `gap_pct=None` ⇒ 原判据恒 False
        #   ⇒ 界面上照常显示 `昨收 15.64 / 支撑 15.39`，**一点提示都没有**。
        #   ⇒ 补"点位量级自检"：主流指数没有低于 **100 点**的
        #     （最低的科创50/北证50 也在 1000 上下，中证500 是 6700 级）。
        #     阈值写死 100 是保守下界；将来若有 <100 的新指数，改这里并加进
        #     `tencent.INDEX_MAP` 即可（两者必须同步）。
        bad_prev = bool(prev_close and prev_close < 100)
        bad_support = bool(_f(r.get("support")) and _f(r.get("support")) < 100)
        suspect = (bad_prev or bad_support
                   or bool(gap_pct is not None and abs(gap_pct) > 25))
        if suspect:
            gap_pct = None
        out.append({
            "code": code, "name": r.get("name") or code,
            "prev_close": prev_close,
            "open_price": op_px,
            "open_src": op_src,
            "gap_pct": gap_pct,
            "suspect": suspect,
            "support": _f(r.get("support")),
            "support_type": r.get("support_type") or "",
            "status": r.get("status") or "",
            "broke": bool(r.get("broke")),
            "detail": r.get("detail") or "",
            "kl_date": kl.get("date"),
        })
    return out


def _scenario_band(prev_day: Optional[str]) -> Optional[Dict]:
    """昨日推演锚点 → 预期区间 [下看, 修复]（仅上证有锚点体系；缺失不硬造）。

    ★ 2026-11-08 实测校准：anchor.downside 是**字符串**（"3500/3400"——两档下看）、
      fix 是描述串（"收复 3674–3681 且连续 2 日…"）⇒ 区间端点 = downside 首段数值
      + `SC_SH_FIX` 常量（与推演同源），原始串原样透出供展示。
    """
    if not prev_day:
        return None
    try:
        scw = _scw.load(day=prev_day)
        a = (scw or {}).get("anchor") or {}
        import re as _re
        ds = str(a.get("downside") or "")
        m = _re.match(r"\s*([\d.]+)", ds)
        lo = float(m.group(1)) if m else None
        hi = float(getattr(_scw, "SC_SH_FIX", 0) or 0)
        return {
            "level": _f(a.get("level")),
            "state": a.get("state") or "",
            "close": _f(a.get("close")),
            "downside_raw": ds, "fix_raw": str(a.get("fix") or ""),
            "band": [lo, hi] if (lo and hi and lo < hi) else None,
            "data_as_of": (scw or {}).get("data_as_of") or prev_day,
        }
    except Exception as e:
        return {"error": str(e)[:80]}


def _sector_rows(gaps: List[Dict], market_avg: Optional[float] = None) -> Dict:
    """板块联动：industry_map × 缺口 → 行业聚合（高开/低开榜 + 领涨/领跌成员）。"""
    try:
        imap = _imap.get_industry_map() or {}
    except Exception:
        imap = {}
    agg: Dict[str, Dict] = {}
    for g in gaps:
        ind = imap.get(str(g["code"]))
        if not ind:
            continue
        a = agg.setdefault(ind, {"industry": ind, "n": 0, "up": 0, "down": 0,
                                 "sum_gap": 0.0, "amount_wan": 0, "members": []})
        a["n"] += 1
        a["sum_gap"] += g["gap_pct"]
        if g["gap_pct"] > 0:
            a["up"] += 1
        elif g["gap_pct"] < 0:
            a["down"] += 1
        a["amount_wan"] += g["amount_wan"]
        if len(a["members"]) < 60:            # 领涨/领跌成员从成员池里挑（截断防爆内存）
            a["members"].append({"name": g["name"], "code": g["code"],
                                 "gap_pct": g["gap_pct"]})
    up_list, down_list = [], []
    for a in agg.values():
        if a["n"] < _MIN_SECTOR_N:
            continue                          # 小样本不进榜（防"3 只全高开=板块强势"噪声）
        a.pop("members_keep", None)
        a["avg_gap"] = round(a["sum_gap"] / a["n"], 2)
        a["up_ratio"] = round(a["up"] / a["n"] * 100, 1)
        a["amount_wan"] = round(a["amount_wan"])
        # ★★ 2026-11-08（用户实测反馈"普跌日高开板块全是负缺口，语义误导"）：
        #   加**超额缺口**（行业均值 − 全市场均值）——普跌日"谁更抗跌"（避风港）
        #   与普涨日"谁更强"是同一个口径，排序也改按超额 ⇒ 两种市况下都可读。
        a["excess"] = (round(a["avg_gap"] - market_avg, 2)
                       if market_avg is not None else None)
        mem = sorted(a["members"], key=lambda x: -x["gap_pct"])
        a["leaders"] = mem[:_LEADER_N]        # 领涨成员
        a["laggers"] = list(reversed(mem[-_LEADER_N:])) if len(mem) > _LEADER_N else []
        a.pop("members"), a.pop("sum_gap")
        up_list.append(a)
        down_list.append(a)
    # 排序口径 = 超额缺口（缺市场均值时退回绝对 avg_gap）
    def _ex(x):
        return x["excess"] if x.get("excess") is not None else x["avg_gap"]
    up_list.sort(key=lambda x: -_ex(x))
    # 低开榜准入：down 家数 > up（"真低开行业"——防 avg_gap 为正混入，合成测试实测踩到）
    down_list = [a for a in down_list if a["down"] > a["up"]]
    down_list.sort(key=lambda x: _ex(x))
    return {"up": up_list[:_SECTOR_TOP], "down": down_list[:_SECTOR_TOP],
            "n_industries": len(agg),
            "note": (f"排序=超额缺口（行业均值−全市场均值{f'{market_avg:+.2f}%' if market_avg is not None else ''}）；"
                     f"成员≥{_MIN_SECTOR_N} 只的行业才进榜；低开榜要求低开家数过半；"
                     f"映射=industry_map（月级重建，新行业名有滞后）")}


def _emotion_summary(gaps: List[Dict], gap_date: str, gap_is_today: bool) -> Dict:
    up_open = sum(1 for g in gaps if g["gap_pct"] > 0)
    down_open = sum(1 for g in gaps if g["gap_pct"] < 0)
    avg_gap = round(sum(g["gap_pct"] for g in gaps) / len(gaps), 2) if gaps else None
    amount_wan = sum(g["amount_wan"] for g in gaps)
    prev_amt = None
    pd_ = _prev_day(gap_date)
    if pd_:
        try:
            r = db.fetch_one("SELECT auction_total_amount_wan FROM market_emotion_daily "
                             "WHERE date=%s", (pd_,))
            prev_amt = _f((r or {}).get("auction_total_amount_wan"))
        except Exception:
            prev_amt = None
    ratio = None
    if gap_is_today and prev_amt and amount_wan and prev_amt > 0:
        ratio = round(amount_wan / prev_amt, 2)
    return {"up_open": up_open, "down_open": down_open, "avg_gap_all": avg_gap,
            "total_amount_wan": round(amount_wan) if amount_wan else None,
            "prev_amount_wan": round(prev_amt) if prev_amt else None,
            "amount_ratio": ratio,
            "prev_day": pd_,
            "amount_note": ("amount 为截至 as_of 的累计值（9:15-9:25 期间即竞价额）；"
                            "量比=今竞价额/昨竞价额，仅当日口径且昨日有值时给出")}


# ══════════════════════════════════════════════════════════════════════════
# 面板组装
# ══════════════════════════════════════════════════════════════════════════
def _plan_section(gap_map: Dict[str, Dict]) -> Dict:
    """计划偏离对照（A3，docs/plans/20261108_看盘序信息传递盘点）：**纯事实 join**。

    · 候选 = `ranking_history` 最新日 Top10（决策卡"做什么"的同源，一次小读）
    · 观察池 = `gate_snapshot_history` 最新 payload 中 ready≥2 的票（买入闸门就绪底座）
    · join 竞价缺口 ⇒ "我计划里的票今天竞价如何"；
      **只陈述，不建议**（R2：竞价数据无检验结论；参考价偏离的解读留给规则判读卡）。
    """
    out: Dict = {"candidates": [], "watch": [], "note": ""}
    try:
        r = db.fetch_one("SELECT MAX(rank_date) AS d FROM ranking_history")
        d = (r or {}).get("d")
        if d:
            out["candidates_date"] = str(d)[:10]
            for x in (db.fetch(
                    "SELECT rank_pos, code, name, total_score, signal "
                    "FROM ranking_history WHERE rank_date=%s "
                    "ORDER BY rank_pos LIMIT 10", (d,)) or []):
                c = str(x.get("code") or "")
                g = gap_map.get(c)
                out["candidates"].append({**x, "gap_pct": (g or {}).get("gap_pct")})
    except Exception as e:
        out["note"] += "候选读取失败；"
    try:
        row = db.fetch_one("SELECT date, payload FROM gate_snapshot_history "
                           "ORDER BY date DESC LIMIT 1")
        pay = json.loads((row or {}).get("payload") or "{}")
        out["watch_date"] = str((row or {}).get("date") or "")[:10]
        cnt = 0
        for x in (pay.get("candidates") or []):
            code = str(x.get("code") or "")
            ready = x.get("ready")
            if not code or ready is None:
                continue
            try:
                if int(ready) < 2:
                    continue
            except (TypeError, ValueError):
                continue
            g = gap_map.get(code)
            out["watch"].append({"code": code, "name": x.get("name") or code,
                                 "ready": ready,
                                 "gap_pct": (g or {}).get("gap_pct")})
            cnt += 1
            if cnt >= 20:
                break
    except Exception as e:
        out["note"] += "观察池读取失败；"
    out["note"] = (out["note"] + "纯事实对照（候选=盘前评分 Top10 / 观察=闸门 ready≥2；"
                   "缺口=竞价开盘/昨收）；不构成任何操作建议").strip("； ")
    return out


def build_panel() -> Dict:
    gaps, gap_date, has_live = _stock_gaps()
    gap_is_today = gap_date == _bj_now().strftime("%Y-%m-%d")
    prev_day = _prev_day(gap_date)
    # ★ 顺序：emotion 先算（avg_gap_all 是板块超额的基准）→ 再聚合板块
    emotion = _emotion_summary(gaps, gap_date, gap_is_today) if has_live else {}
    sectors = (_sector_rows(gaps, market_avg=emotion.get("avg_gap_all"))
               if has_live else
               {"up": [], "down": [], "n_industries": 0, "note": "无行情"})
    indexes = _index_rows(prev_day, gap_is_today, gap_date) if has_live else []
    band = _scenario_band(prev_day)
    panel = {
        "ok": has_live,
        "gap_date": gap_date,
        "gap_is_today": gap_is_today,
        "as_of": _bj_now().strftime("%Y-%m-%d %H:%M"),
        "prev_day": prev_day,
        "indexes": indexes,
        "scenario": band,
        "sectors": sectors,
        "emotion": emotion,
        "llm": _load_llm(gap_date),
        "note": ("口径与竞价看板主卡一致：非竞价窗口读的是最近已完成交易日的开盘数据；"
                 "预期区间=昨日推演锚点（下看~修复，仅上证）；板块=industry_map 聚合。"),
    }
    # ★ 2026-11-08（看盘序 A3）：计划偏离对照（纯事实 join，零决策链）
    gap_map = {g["code"]: g for g in gaps}
    panel["plan"] = _plan_section(gap_map) if has_live else {
        "candidates": [], "watch": [], "note": "无行情"}
    # ★ 2026-11-08（看盘序 A1b）：昨日日报的"明日关键观察指标"接入竞价面板——
    #   日报收盘后产出（A1a 落库），次日竞价正是它的消费时刻；缺失如实标注（不硬造）。
    try:
        from app.daily_report import load_report_json
        _tw = load_report_json(day=prev_day)
        if _tw and _tw.get("payload"):
            panel["tomorrow_watch"] = {
                "date": _tw["date"],
                "items": (_tw["payload"].get("tomorrow_watch") or [])[:5],
            }
        else:
            panel["tomorrow_watch"] = {
                "date": prev_day, "items": [],
                "empty_reason": "该日日报无结构化产出（LLM 兜底或恢复前）",
            }
    except Exception as e:
        panel["tomorrow_watch"] = {"date": prev_day, "items": [],
                                   "empty_reason": str(e)[:80]}
    panel["llm_sig_current"] = _sig(panel)
    return panel


# ══════════════════════════════════════════════════════════════════════════
# LLM 解读（按日缓存 + 数据指纹；GET 只读缓存，POST 才真烧）
# ══════════════════════════════════════════════════════════════════════════
_LLM_SYSTEM = (
    "你是 A 股竞价阶段（9:15-9:25）的观察助手。只做**性质判断与关注点提示**："
    "绝不给买卖建议、仓位、目标价、个股推荐（项目红线）。"
    "输入是竞价面板 JSON：指数开盘 vs 昨日关键位与预期区间、行业板块聚合、"
    "全市场高开低开结构与竞价量比。规则："
    "① 数字必须引用面板原值，禁止编造；字段为 null 即如实说'数据不足'；"
    "② 区间位置判断：开盘高于区间上沿=强于预期，区间内=符合预期，低于下沿=弱于预期；"
    "③ 输出必须是合法 JSON（不要 markdown 围栏）："
    '{"conclusion":"一句话总结(≤50字)","strength":"强弱信号(2-3句,引用具体数字)",'
    '"sentiment":"资金情绪(2-3句,结合量比与高开低开结构)",'
    '"risks":["风险1","风险2"],"watch_points":["关注点1","关注点2","关注点3"]}')


def _sig(panel: Dict) -> str:
    core = {
        "d": panel.get("gap_date"),
        "e": [panel.get("emotion", {}).get(k) for k in
              ("up_open", "down_open", "avg_gap_all", "total_amount_wan", "amount_ratio")],
        "i": [[x.get("code"), x.get("gap_pct"), x.get("status")]
              for x in (panel.get("indexes") or [])],
        "s": [[x.get("industry"), x.get("up_ratio"), x.get("avg_gap")]
              for x in (panel.get("sectors", {}).get("up") or [])[:5]],
    }
    return hashlib.md5(json.dumps(core, sort_keys=True, ensure_ascii=False,
                                  default=str).encode("utf-8")).hexdigest()[:12]


def _ensure_llm_table() -> None:
    global _LLM_READY
    if _LLM_READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_LLM_TABLE} (
            date TEXT PRIMARY KEY,
            sig TEXT,
            payload TEXT,
            model TEXT,
            llm_version TEXT,
            created_at TEXT
        )
    """)
    _LLM_READY = True


def _load_llm(gap_date: str) -> Optional[Dict]:
    try:
        _ensure_llm_table()
        r = db.fetch_one(f"SELECT * FROM {_LLM_TABLE} WHERE date=%s", (str(gap_date)[:10],))
        if not r:
            return None
        return {"date": r.get("date"), "sig": r.get("sig"),
                "payload": json.loads(r.get("payload") or "{}"),
                "created_at": r.get("created_at"),
                "llm_version": r.get("llm_version")}
    except Exception:
        return None


def _panel_prompt_core(panel: Dict) -> Dict:
    """喂给 LLM 的精简面板（全量 sectors 的 members 已聚合，不塞明细）。"""
    return {
        "gap_date": panel.get("gap_date"),
        "gap_is_today": panel.get("gap_is_today"),
        "prev_day": panel.get("prev_day"),
        "emotion": panel.get("emotion"),
        "scenario_band": (panel.get("scenario") or {}).get("band"),
        "scenario_anchor": {k: (panel.get("scenario") or {}).get(k)
                            for k in ("level", "state", "close")},
        "indexes": [{k: x.get(k) for k in
                     ("name", "prev_close", "open_price", "open_src", "gap_pct",
                      "support", "status", "broke")} for x in panel.get("indexes") or []],
        "sectors_up": (panel.get("sectors", {}).get("up") or [])[:5],
        "sectors_down": (panel.get("sectors", {}).get("down") or [])[:5],
    }


def run_llm(force: bool = False) -> Dict:
    """生成/读取竞价 LLM 解读。**只做性质判断**；失败降级，不影响面板。"""
    panel = build_panel()
    if not panel.get("ok"):
        return {"ok": False, "error": "无行情数据（面板不可用）"}
    gap_date = panel["gap_date"]
    sig = panel["llm_sig_current"]
    cached = panel.get("llm")
    if cached and not force and cached.get("sig") == sig and cached.get("payload"):
        return {"ok": True, "cached": True, **cached["payload"], "sig": sig}

    try:
        from app.flash import llm
        payload = llm._call_json(_LLM_SYSTEM,
                                 json.dumps(_panel_prompt_core(panel),
                                            ensure_ascii=False, default=str),
                                 temperature=0.2)
    except Exception as e:
        return {"ok": False, "error": f"LLM 调用失败: {str(e)[:100]}"}
    if not payload or not isinstance(payload, dict) or not payload.get("conclusion"):
        return {"ok": False, "error": "LLM 返回不可用（空/缺 conclusion）——可重试或看规则判读"}

    now = _bj_now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        _ensure_llm_table()
        model = ""
        try:
            from app.flash import llm as _l
            model = str(getattr(_l, "DEFAULT_MODEL", "") or "")
        except Exception:  # noqa: except_pass  只用于库里记 model 名，取不到留空即可，不影响判定
            pass
        db.upsert(_LLM_TABLE, {
            "date": str(gap_date)[:10], "sig": sig,
            "payload": json.dumps(payload, ensure_ascii=False),
            "model": model, "llm_version": _LLM_VERSION, "created_at": now,
        }, conflict_columns=["date"])
    except Exception as e:
        print(f"[auction_panel] LLM 记账失败（不影响返回）: {str(e)[:80]}")
    return {"ok": True, "cached": False, "sig": sig, **payload}
