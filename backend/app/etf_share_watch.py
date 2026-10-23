# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】宽基 ETF 份额异动 —— **国家队行为的 T+1 证据**（观察器，不产生买卖信号）
================================================================================
背景（2026-10-19，用户："接 ETF 份额异动（行为级证据，比公告更早）"）：
  汇金的真实买盘大头在**宽基 ETF 申购**，它先于公告、且公告经常不说金额
  ⇒ 份额是"最诚实的痕迹"。本模块每天算一次，供 `policy_bottom_watch` 第①条作**证据**。

★★ 判据（env 可调；改判据请在文档留痕）
  · 单日份额增幅 ≥ `ETF_SHARE_CHG_PCT`（默认 **8%**）⇒ 记为「疑似国家队申购」
  · **连续 2 个交易日**都放大（`ETF_CONFIRM_DAYS`）⇒ 升级为「确认」
  · 确认后 = 第①条的**自动勾选依据**（人工仍可覆盖，见 policy_bottom_watch）

★★ 数据源（**避开东财限速**，全部官方）
  · 主源：**上交所** `query.sse.com.cn` 的 ETF 规模/份额披露接口
      sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L
      参数 `STAT_DATE=YYYY-MM-DD`（**可查历史**，非交易日返回 0 行）
      一次 `pageHelp.pageSize=5000` 取回**全部 924 只**（实测 total=924，一页够）
      字段：`SEC_CODE / SEC_NAME / TOT_VOL`（总份额，**万份**；比值口径与单位无关）
  · 深市（159915 等）：`szse.cn/api/report/ShowReport/data?CATALOGID=1945` 的
      `PAGESIZE` **无效、固定 10 条/页 ⇒ 758 只 = 76 页**，每日 76 请求成本过高
      ⇒ **本版不纳入深市**（不是漏了）。要接的话正解是找深交所"基金份额"专用报表，
      或按周缓存一次全表。

★★ 红线
  1. 纯观察器：不改分数、不进闸门、不碰模拟盘准入。
  2. 份额增幅是**证据**不是信号：可能来自做市/套利/新发，不能直接推"汇金买了"。
  3. 接口失败 ⇒ `ok=0`，**不得**当成"没有异动"（缺失 ≠ 不满足）。
================================================================================
"""

import json
import os
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.database import db

# ── 阈值 ──
ETF_SHARE_CHG_PCT = float(os.environ.get("ETF_SHARE_CHG_PCT") or 8.0)
ETF_CONFIRM_DAYS = int(os.environ.get("ETF_CONFIRM_DAYS") or 2)
# ★ 2026-10-23：连续天数原来只算 0/1/2（今天+昨天），用户问"怎么都是 1 天" ——
#   现在最多回溯 5 个交易日，展示真实连续天数（确认阈值仍是 ETF_CONFIRM_DAYS=2）。
ETF_STREAK_MAX = int(os.environ.get("ETF_STREAK_MAX") or 5)

# 重点宽基（沪市；汇金申购的主战场）
WATCH: Dict[str, str] = {
    "510050": "华夏上证50ETF",
    "510300": "华泰柏瑞沪深300ETF",
    "510330": "华夏沪深300ETF",
    "510500": "南方中证500ETF",
    "588000": "华夏科创50ETF",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
SSE_URL = ("http://query.sse.com.cn/commonQuery.do?sqlId="
           "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&isPagination=true"
           "&pageHelp.pageSize=5000&pageHelp.pageNo=1&pageHelp.beginPage=1"
           "&pageHelp.cacheSize=1&pageHelp.endPage=1&STAT_DATE=")
TIMEOUT = 25

_TABLE_READY = False


# ────────────────────────────────────────────────────────────────
#  取数
# ────────────────────────────────────────────────────────────────
def _sse_map(date: str) -> Dict[str, Tuple[str, float]]:
    """某一交易日的 {代码: (名称, 总份额/万份)}；非交易日/未更新 ⇒ {}（由调用方判空）。"""
    req = urllib.request.Request(SSE_URL + date, headers={
        "User-Agent": UA, "Referer": "http://www.sse.com.cn/"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    rows = ((d.get("pageHelp") or {}).get("data")) or []
    out = {}
    for x in rows:
        code = str(x.get("SEC_CODE") or "").strip()
        try:
            vol = float(str(x.get("TOT_VOL") or 0))
        except (TypeError, ValueError):
            continue
        if code and vol > 0:
            out[code] = (str(x.get("SEC_NAME") or ""), vol)
    return out


def _prev_trading_day(day: str) -> str:
    """上一个交易日（跳过周末/法定节假日）。"""
    d = datetime.strptime(day, "%Y-%m-%d")
    try:
        from app.flash.rules import is_trading_day
    except Exception:
        is_trading_day = lambda x: x.weekday() < 5        # noqa: E731
    for _ in range(15):
        d -= timedelta(days=1)
        if is_trading_day(d):
            return d.strftime("%Y-%m-%d")
    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


# ────────────────────────────────────────────────────────────────
#  建表 / 落库 / 读取
# ────────────────────────────────────────────────────────────────
def ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS etf_share_watch (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            vol REAL,
            prev_vol REAL,
            chg_pct REAL,
            suspicious INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0,
            created_at TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    _TABLE_READY = True


def save(data: Dict) -> bool:
    ensure_table()
    day = str(data.get("date") or "")[:10]
    rows = data.get("rows") or []
    ts = datetime.now().isoformat()
    ok = True
    for r in rows:
        try:
            db.upsert("etf_share_watch", {
                "date": day, "code": r["code"], "name": r.get("name") or "",
                "vol": r.get("vol"), "prev_vol": r.get("prev_vol"),
                "chg_pct": r.get("chg_pct"), "suspicious": 1 if r.get("suspicious") else 0,
                "streak": int(r.get("streak") or 0), "created_at": ts,
            }, conflict_columns=["date", "code"])
        except Exception as e:
            print(f"[etf] save failed {r.get('code')}: {str(e)[:80]}")   # ASCII（铁律⑥）
            ok = False
    return ok


def load(day: str) -> Optional[Dict]:
    ensure_table()
    day = str(day or "")[:10]
    if not day:
        return None
    try:
        rows = db.fetch("SELECT * FROM etf_share_watch WHERE date = %s ORDER BY chg_pct DESC",
                        (day,)) or []
    except Exception:
        return None
    data_date = day
    if not rows:
        # ★ 2026-10-23：上交所份额是 **T+1** 披露，当日日批常常还取不到
        #   ⇒ 原来直接返回 None，清单显示"尚未运行（日批上线后自动填充）"，
        #   看起来像没跑。改成**沿用最近一份**并标注 data_date，绝不冒充当日。
        try:
            r = db.fetch_one("SELECT MAX(date) AS d FROM etf_share_watch "
                             "WHERE date <= %s", (day,))
            data_date = str((r or {}).get("d") or "")[:10]
            if data_date and data_date != day:
                rows = db.fetch("SELECT * FROM etf_share_watch WHERE date = %s "
                                "ORDER BY chg_pct DESC", (data_date,)) or []
        except Exception:
            rows = []
    if not rows:
        return None
    out = [dict(r) for r in rows]
    # ★ `ok=True` 必须带上：`summarize()` 用 `not d.get("ok")` 判"不可用"，
    #   漏了就会把"读到正常历史"误报成"检查不可用"（实测踩到）。
    return {"ok": True, "date": day, "data_date": data_date,
            "fallback": bool(data_date and data_date != day),
            "rows": out, "count": len(out),
            "suspicious": [r for r in out if r.get("suspicious")],
            "confirmed": any(int(r.get("streak") or 0) >= ETF_CONFIRM_DAYS for r in out),
            "threshold": ETF_SHARE_CHG_PCT, "confirm_days": ETF_CONFIRM_DAYS,
            "created_at": str(out[0].get("created_at") or "")}


# ────────────────────────────────────────────────────────────────
#  扫描
# ────────────────────────────────────────────────────────────────
def scan(day: str) -> Dict:
    """算某交易日的份额异动（含"是否连续 2 日"）。失败**不外抛**。"""
    day = str(day or "")[:10]
    try:
        cur = _sse_map(day)
    except Exception as e:
        return {"ok": False, "date": day, "rows": [], "error": f"上交所接口失败: {type(e).__name__}: {str(e)[:60]}"}
    if not cur:
        return {"ok": False, "date": day, "rows": [],
                "error": f"{day} 上交所无份额数据（非交易日或尚未更新）"}

    d1 = _prev_trading_day(day)
    d2 = _prev_trading_day(d1)
    try:
        prev = _sse_map(d1)
        prev2 = _sse_map(d2)
    except Exception as e:
        return {"ok": False, "date": day, "rows": [],
                "error": f"前一交易日接口失败: {type(e).__name__}: {str(e)[:60]}"}

    # 逐日份额表缓存：连续天数回溯时按需取（最多再多 3 次请求）
    _maps = {day: cur, d1: prev, d2: prev2}

    def _vol_of(dd: str, code: str):
        if dd not in _maps:
            try:
                _maps[dd] = _sse_map(dd)
            except Exception:
                _maps[dd] = {}
        return (_maps[dd].get(code) or (None, None))[1]
    if not prev:
        return {"ok": False, "date": day, "rows": [], "error": f"{d1} 无份额数据，无法算增幅"}

    rows: List[Dict] = []
    for code, name in WATCH.items():
        if code not in cur:
            continue
        vol = cur[code][1]
        pv = (prev.get(code) or (None, None))[1]
        if not pv:
            continue
        chg = (vol / pv - 1) * 100
        sus = chg >= ETF_SHARE_CHG_PCT
        # 连续天数：昨天也放大 ⇒ 2；只有今天 ⇒ 1
        pv2 = (prev2.get(code) or (None, None))[1]
        chg_prev = ((pv / pv2 - 1) * 100) if pv2 else None
        sus_prev = bool(chg_prev is not None and chg_prev >= ETF_SHARE_CHG_PCT)
        # ★ 连续天数：从今天往前逐日回溯（最多 ETF_STREAK_MAX 天），
        #   而不是只判"今天+昨天"⇒ 连续 3 天时能显示 3，而不是永远 1/2。
        streak = 0
        if sus:
            streak = 1
            cur_vol, dd = vol, day
            for _ in range(max(0, ETF_STREAK_MAX - 1)):
                dd = _prev_trading_day(dd)
                pv_i = _vol_of(dd, code)
                if not pv_i:
                    break
                if (cur_vol / pv_i - 1) * 100 >= ETF_SHARE_CHG_PCT:
                    streak += 1
                    cur_vol = pv_i
                else:
                    break
        rows.append({
            "code": code, "name": cur[code][0] or name,
            "vol": round(vol, 2), "prev_vol": round(pv, 2),
            "chg_pct": round(chg, 2), "prev_chg_pct": (round(chg_prev, 2) if chg_prev is not None else None),
            "suspicious": sus, "streak": streak,
        })

    rows.sort(key=lambda r: -r["chg_pct"])
    confirmed = any(r["streak"] >= ETF_CONFIRM_DAYS for r in rows)
    return {"ok": True, "date": day, "prev_date": d1, "rows": rows,
            "suspicious": [r for r in rows if r["suspicious"]],
            "confirmed": confirmed,
            "threshold": ETF_SHARE_CHG_PCT, "confirm_days": ETF_CONFIRM_DAYS,
            "error": ""}


def scan_and_store(day: str, push: bool = True) -> Dict:
    """日批入口：算 → 落库 → **只在"首次确认"时**推企微（同日重跑不重复推）。"""
    prev = load(day) or {}
    was_confirmed = bool(prev.get("confirmed"))
    data = scan(day)
    if data.get("ok"):
        data["stored"] = save(data)
    else:
        data["stored"] = False
        data["confirmed"] = False

    pushed = False
    if push and data.get("confirmed") and not was_confirmed:
        try:
            from app.flash import wechat
            lines = [f"**宽基 ETF 份额连续放大（{data['date']}）**",
                     f"门槛：单日 ≥{ETF_SHARE_CHG_PCT:g}% 且连续 {ETF_CONFIRM_DAYS} 日"
                     "（疑似国家队申购，**证据，非信号**）"]
            for r in [x for x in data["rows"] if x["streak"] >= ETF_CONFIRM_DAYS]:
                lines.append(f"- {r['code']} {r['name']}：{r['chg_pct']:+.2f}%"
                             f"（前一日 {r.get('prev_chg_pct')}%）")
            wechat.push_markdown_batched("ETF 份额连续放大（市场底清单①证据）",
                                         "\n".join(lines), force=True, category="alert")
            pushed = True
        except Exception as e:
            print(f"[etf] push failed: {str(e)[:100]}")
    data["pushed"] = pushed
    return data


def backfill(days: int = 5, end: str = None) -> Dict:
    """补最近 N 个交易日的份额快照（跳过已有日期；**不推送**）。

    ★ 2026-10-23（用户："没有历史数据补全吗"）：上交所支持 `STAT_DATE` 查历史，
      而日批只在"当天有数据"时落库 ⇒ T+1 未披露的日子永久缺行，连续天数也就
      永远从 1 起算。这里按交易日往回补齐（默认 5 天 ≈ 15 次请求，仅首次真正发请求）。
    """
    if not end:
        try:
            from app.flash.rules import latest_completed_trading_day
            end = latest_completed_trading_day()
        except Exception:
            end = datetime.now().strftime("%Y-%m-%d")
    filled, skipped, failed = [], [], []
    d = str(end)[:10]
    for i in range(max(1, days)):
        if i:
            d = _prev_trading_day(d)
        if load(d):
            skipped.append(d)
            continue
        r = scan(d)
        if r.get("ok"):
            save(r)
            filled.append(d)
        else:
            failed.append(d)
    print(f"[etf] backfill {end} 前 {days} 日: 补 {len(filled)} / 已有 {len(skipped)}"
          f" / 失败 {len(failed)}（{filled[:3]}）")
    return {"filled": filled, "skipped": skipped, "failed": failed, "end": end}


def summarize(data: Dict = None) -> str:
    """一行文案（清单/日报/接口共用）。"""
    d = data or {}
    if not d:
        return "份额异动：尚无记录（日批未跑过）"
    if not d.get("ok"):
        return f"份额异动：检查不可用（{d.get('error') or '取数失败'}）**不等于没有异动**"
    sus = d.get("suspicious") or []
    asof = (f"（当日尚未披露，沿用 {d['data_date']}）"
            if d.get("fallback") and d.get("data_date") else "")
    if d.get("confirmed"):
        names = "、".join(f"{r['code']}+{r['chg_pct']:.1f}%" for r in sus[:3])
        return (f"🔔 份额异动**连续 {ETF_CONFIRM_DAYS} 日确认**：{names}"
                f"（疑似国家队申购）{asof}")
    if sus:
        n = max(int(r.get("streak") or 0) for r in sus)
        names = "、".join(f"{r['code']}+{r['chg_pct']:.1f}%" for r in sus[:3])
        return (f"份额异动：{names}（≥{ETF_SHARE_CHG_PCT:g}%，已连续 {n} 日，"
                f"需 {ETF_CONFIRM_DAYS} 日确认）{asof}")
    top = (d.get("rows") or [{}])[0]
    return (f"份额异动：无（{d.get('data_date') or d.get('date')} 最大增幅 "
            f"{(top.get('chg_pct') if top else 0) or 0:+.2f}%，"
            f"门槛 {ETF_SHARE_CHG_PCT:g}%）{asof}")
