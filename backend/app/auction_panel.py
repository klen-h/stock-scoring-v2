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


def _index_rows(prev_day: Optional[str], gap_is_today: bool) -> List[Dict]:
    """指数行：昨日关键位（key_level_watch）+ 竞价实时价（仅当日口径）。"""
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
        lq = live.get(code) or {}
        lpx = _f(lq.get("price"))
        gap_pct = round((lpx / prev_close - 1) * 100, 2) \
            if (lpx and prev_close) else None
        out.append({
            "code": code, "name": r.get("name") or code,
            "prev_close": prev_close,
            "live_price": lpx,
            "gap_pct": gap_pct,
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


def _sector_rows(gaps: List[Dict]) -> Dict:
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
        mem = sorted(a["members"], key=lambda x: -x["gap_pct"])
        a["leaders"] = mem[:_LEADER_N]        # 领涨成员
        a["laggers"] = list(reversed(mem[-_LEADER_N:])) if len(mem) > _LEADER_N else []
        a.pop("members"), a.pop("sum_gap")
        up_list.append(a)
        down_list.append(a)
    up_list.sort(key=lambda x: (-x["up_ratio"], -x["avg_gap"]))
    # ★ 低开榜准入：down 家数 > up 才算"真低开行业"（防止 avg_gap 为正、
    #   仅因 up_ratio 排序靠后而混进低开榜——2026-11-08 合成测试实测踩到）
    down_list = [a for a in down_list if a["down"] > a["up"]]
    down_list.sort(key=lambda x: (x["avg_gap"], x["up_ratio"]))
    return {"up": up_list[:_SECTOR_TOP], "down": down_list[:_SECTOR_TOP],
            "n_industries": len(agg),
            "note": f"聚合口径=竞价缺口（开盘/昨收），成员≥{_MIN_SECTOR_N} 只的行业才进榜；"
                    f"低开榜要求低开家数过半；映射=industry_map（月级重建，新行业名有滞后）"}


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
def build_panel() -> Dict:
    gaps, gap_date, has_live = _stock_gaps()
    gap_is_today = gap_date == _bj_now().strftime("%Y-%m-%d")
    prev_day = _prev_day(gap_date)
    indexes = _index_rows(prev_day, gap_is_today) if has_live else []
    band = _scenario_band(prev_day)
    sectors = _sector_rows(gaps) if has_live else {"up": [], "down": [], "n_industries": 0, "note": "无行情"}
    emotion = _emotion_summary(gaps, gap_date, gap_is_today) if has_live else {}
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
                     ("name", "prev_close", "live_price", "gap_pct",
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
        except Exception:
            pass
        db.upsert(_LLM_TABLE, {
            "date": str(gap_date)[:10], "sig": sig,
            "payload": json.dumps(payload, ensure_ascii=False),
            "model": model, "llm_version": _LLM_VERSION, "created_at": now,
        }, conflict_columns=["date"])
    except Exception as e:
        print(f"[auction_panel] LLM 记账失败（不影响返回）: {str(e)[:80]}")
    return {"ok": True, "cached": False, "sig": sig, **payload}
