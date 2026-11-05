# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】指数关键位观察器（key level watch）—— **纯观察器，不产生买卖信号**
================================================================================
背景（2026-10-23，用户 10-22 复盘）：
  "3674.82/3681.28 双底今天被击穿（最低 3649.86，年内首次收在 3700 下方）……
   这意味着政策底 1.0 试探失败。"
  ⇒ 把"前低/整数位/年内低点有没有失守、离支撑多远"变成每天可核对的表。

口径（指数日 K，腾讯；失败返回 insufficient，不猜）：
  · 前低 = 最近 60 个交易日（不含今日）最低收盘；
  · 整数位 = floor(收盘/100)×100；
  · 年内低点 = 最近 250 个交易日（不含今日）最低收盘；
  · 失守 = **今日收盘跌破、且昨日收盘还在其上**（首次跌破，避免连续多日重复计数）；
  · 支撑 = 候选位中"低于现价且最高"的那个；离支撑距离 = 收盘/支撑 − 1。

★ 预登记声明：不改分数、不进闸门、不改 regime、不碰模拟盘。
================================================================================
"""
import math
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

from app.database import db

_TABLE = "key_level_watch"
_READY = False

INDICES = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("000300", "沪深300"),
    ("000905", "中证500"),
    ("000985", "中证全指"),
]

# ★ 2026-10-23（用户推演）：结构性关键位（前双底/颈线），非整数关口。
#   格式 env `KEY_LEVELS_EXTRA`：`代码:位1,位2;代码:位1`（默认上证 3674/3681/3750）。
def _extra_levels() -> Dict[str, List[float]]:
    # ★ 2026-10-23：加入当日低点 **3574**（用户："把 3574 写进触发器"）；
    #   3674/3681 = 前双底，3750 = 双底颈线。
    raw = (os.environ.get("KEY_LEVELS_EXTRA")
           or "000001:3574,3674,3681,3750").strip()
    out: Dict[str, List[float]] = {}
    for seg in raw.split(";"):
        seg = seg.strip()
        if ":" not in seg:
            continue
        code, lv = seg.split(":", 1)
        vals = []
        for x in lv.split(","):
            try:
                vals.append(float(x.strip()))
            except (TypeError, ValueError):
                pass
        if vals:
            out[code.strip()] = vals
    return out


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS key_level_watch (
            date TEXT, code TEXT, name TEXT,
            close REAL, chg_pct REAL,
            support REAL, support_type TEXT, dist_pct REAL,
            status TEXT, broke INTEGER, first_year_low INTEGER,
            detail TEXT, created_at TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    _READY = True


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _analyze(bars: List[dict]) -> Optional[Dict]:
    closes = [_num(b.get("close")) for b in bars]
    closes = [x for x in closes if x]
    if len(closes) < 65:
        return None
    c, prev = closes[-1], closes[-2]
    prev60 = min(closes[-61:-1])
    year_low = min(closes[-250:-1]) if len(closes) >= 250 else prev60
    grid = 100.0
    round_level = math.floor(c / grid) * grid
    broke_prev = c < prev60 <= prev
    broke_round = c < round_level <= prev
    first_year_low = c < year_low
    cands = [(x, t) for x, t in ((prev60, "前低"), (round_level, "整数位"),
                                 (year_low, "年内低点")) if x < c]
    if cands:
        support, stype = max(cands, key=lambda x: x[0])
    else:
        support, stype = min((prev60, "前低"), (round_level, "整数位"),
                             (year_low, "年内低点"), key=lambda x: x[0])
    dist = (c / support - 1) * 100 if support else None
    if first_year_low:
        status = "失守年内低点"
    elif broke_prev:
        status = "失守前低"
    elif broke_round:
        status = "失守整数位"
    elif dist is not None and dist <= 1.5:
        status = "逼近支撑"
    else:
        status = "正常"
    detail = (f"{stype} {support:.1f}（{status}）；"
              f"前低 {prev60:.1f} / 整数位 {round_level:.0f} / 年内低点 {year_low:.1f}")
    return {"close": round(c, 2),
            "chg_pct": round((c / prev - 1) * 100, 2),
            "support": round(support, 2), "support_type": stype,
            "dist_pct": round(dist, 2) if dist is not None else None,
            "status": status,
            "broke": bool(broke_prev or broke_round or first_year_low),
            "first_year_low": bool(first_year_low),
            "detail": detail}


def compute(day: str = None) -> Dict:
    ensure_table()
    # ★ 2026-10-23：改用带东财兜底的加载器（腾讯对 399006/399986 常空，
    #   会导致关键位表缺行 → 记分卡"上证位置"退化为数据不足）。
    from app.contradictions.scanner import _load_index_kline
    rows, errors, datas = [], [], []
    for code, name in INDICES:
        try:
            bars = _load_index_kline(code, count=300) or []
        except Exception as e:
            bars = []
            errors.append(f"{name}: {str(e)[:50]}")
        if not bars:
            errors.append(f"{name}: 无K线")
            continue
        d = str(bars[-1].get("date") or "")[:10]
        a = _analyze(bars)
        if not a:
            errors.append(f"{name}: K线不足")
            continue
        datas.append(d)
        rows.append({"code": code, "name": name, "kline_date": d, **a})
    if not rows:
        return {"ok": False, "reason": "指数K线全部不可用", "errors": errors}

    day = str(day or max(datas))[:10]
    # ★★ 2026-10-28（P0：数据日期欺骗）：原逻辑 `day = max(datas)` 后把**所有** index
    #   一并写成 `day` 的行 —— 若某指数只到 10-26（实时源停更），它的 10-26 收盘就会被
    #   标成 10-27 的行，下游（推演证伪/观察器）据此当"当日收盘"用。
    #   实测：10-27 那天只写进 2 个指数、000001 直接缺行（rows=2 且停在 10-23）。
    #   改：逐行校验，只保留**日期等于目标日**的指数；不一致的记进 errors（宁缺勿错日期）。
    kept, dropped = [], []
    for r in rows:
        if r["kline_date"] == day:
            kept.append(r)
        else:
            dropped.append(f"{r['name']}({r['kline_date']})")
    if dropped:
        errors.append("日期不符已跳过：" + "、".join(dropped))
    rows = kept
    if not rows:
        return {"ok": False, "reason": f"无指数有 {day} 的收盘", "errors": errors}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        db.execute("DELETE FROM key_level_watch WHERE date = %s", (day,))
        for r in rows:
            db.upsert(_TABLE, {
                "date": day, "code": r["code"], "name": r["name"],
                "close": r["close"], "chg_pct": r["chg_pct"],
                "support": r["support"], "support_type": r["support_type"],
                "dist_pct": r["dist_pct"], "status": r["status"],
                "broke": 1 if r["broke"] else 0,
                "first_year_low": 1 if r["first_year_low"] else 0,
                "detail": r["detail"], "created_at": now,
            }, conflict_columns=["date", "code"])
    except Exception as e:
        print(f"[keylevel] 落库失败: {str(e)[:100]}")
        return {"ok": False, "reason": f"落库失败: {str(e)[:80]}", "rows": rows}

    return {"ok": True, "date": day, "rows": rows, "errors": errors,
            "broke_n": sum(1 for r in rows if r["broke"]),
            "year_low_n": sum(1 for r in rows if r["first_year_low"])}


# ── 实时现价（2026-11-05 轻量实时化）─────────────────────────────────────────
_LIVE_TTL = 15.0
_live_cache: Dict = {"ts": 0.0, "map": {}}


def _live_index_quotes() -> Dict[str, Dict]:
    """实时指数报价 `{code: {price, chg_pct, time}}`（15 秒进程缓存）。

    ★★ 2026-11-05（用户："把关键位这条做成轻量实时"）：
      支撑位（前低60/整数位/年内低点）**一天只变一次**（收盘后才更新）；
      `broke` 是"首次失守"的**状态量**，盘中翻转会污染它（见 `load` 的 ⚠️）。
      盘中真正在变的只有一件事：**现价离支撑多远**。
      ⇒ 所以这里**只取指数现价**，零 K 线请求、零重算 ——
        与 `compute()` 的"遍历 300 根 K 线整体重算"完全不是一回事。

    ⚠️ 15 秒进程缓存：一次刷新里 6 个指数只该打一轮；用户手动刷新也不会连打。
    ⚠️ 失败返回 `{}`，调用方**原样返回收盘值**（宁可显示昨天的，也不显示空白）。
    """
    now = time.time()
    if _live_cache["ts"] and now - _live_cache["ts"] < _LIVE_TTL:
        return _live_cache["map"]
    out: Dict[str, Dict] = {}
    try:
        from app.tencent import get_index
    except Exception as e:
        print(f"[keylevel] 实时指数模块不可用（回退收盘值）: {str(e)[:80]}")
        _live_cache.update({"ts": now, "map": out})
        return out
    # ★★ 2026-11-05（实测 6 只**串行 10.1 秒**）：`get_index` 每次都是真实 HTTP（无缓存层）。
    #   并发 6 个 ⇒ 降到单个请求的耗时（~1~2s）。前端 120s 轮询本就够，
    #   但首屏/手动刷新不该让人干等 10 秒。
    from concurrent.futures import ThreadPoolExecutor

    def _one(item):
        _c = item[0]
        try:
            q = get_index(_c) or {}
            _p = q.get("price")
            if _p:
                return _c, {"price": float(_p), "chg_pct": q.get("change_pct"),
                            "time": str(q.get("time") or "")}
        except Exception:
            pass              # 单指数失败不影响其余（与 compute() 的 fail-open 同口径）
        return _c, None

    try:
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(INDICES)))) as ex:
            for _c, _v in ex.map(_one, INDICES):
                if _v:
                    out[_c] = _v
    except Exception as e:
        print(f"[keylevel] 实时指数并发抓取失败（回退收盘值）: {str(e)[:80]}")
    _live_cache.update({"ts": now, "map": out})
    return out


def load(day: str = None) -> Dict:
    """读取某日的指数关键位结果。

    ★★ 2026-10-29（用户："观察器的拥挤与关键位盘中不显示"）：与 `crowding_watch.load`
      同源问题 —— 前端按今天查，而 `compute()` 只在日批（收盘后）跑 ⇒ 盘中今天没有行
      ⇒ 页面只有 `—`。修法同样是**回退到最近一次收盘**并标注实际日期。

    ⚠️ 为什么不盘中现算：关键位判断依赖"前低(60日)/年内低点"的完整性，
      盘中用未定稿的当日 bar 会让 `broke`（是否失守）随分钟反复翻转，
      而 `broke` 是**只在首次跌破时才记一次**的状态量 —— 盘中现算会污染这个状态。
    """
    ensure_table()
    requested = str(day)[:10] if day else ""
    fallback = False
    try:
        if day:
            d = str(day)[:10]
            rows = db.fetch("SELECT * FROM key_level_watch WHERE date=%s "
                            "ORDER BY code", (d,))
            if not rows:
                # ⚠️ 同 crowding_watch：必须限制 `date <= 请求日`，
                #    否则查历史无数据日会回退到更新的日期（把后来的行情当成当时的）。
                r = db.fetch_one("SELECT MAX(date) AS d FROM key_level_watch WHERE date <= %s",
                                 (d,))
                latest = str((r or {}).get("d") or "")[:10]
                if latest and latest != d:
                    rows = db.fetch("SELECT * FROM key_level_watch WHERE date=%s "
                                    "ORDER BY code", (latest,))
                    if rows:
                        fallback, d = True, latest
        else:
            r = db.fetch_one("SELECT MAX(date) AS d FROM key_level_watch")
            d = str((r or {}).get("d") or "")[:10]
            rows = db.fetch("SELECT * FROM key_level_watch WHERE date=%s "
                            "ORDER BY code", (d,)) if d else []
    except Exception as e:
        return {"date": day, "rows": [], "note": f"读取失败: {str(e)[:80]}"}
    extra_map = _extra_levels()
    # ★★ 2026-11-05（轻量实时化）：**只在"看最近交易日"时**用实时价覆盖。
    #   回放历史日必须原样返回当日结论 —— 否则就是"用今天的价回看昨天"
    #   （本项目反复禁过的"写入时刻冒充数据时刻"）。
    #   ⚠️ 判据用 `requested` 而不查 `MAX(date)`：多一次远程查询要多 1~2 秒
    #      （实测 Supabase 单次往返 1~2s），而这个判断根本不需要看库里有没有行。
    try:
        from app.flash.rules import latest_completed_trading_day
        _today = str(latest_completed_trading_day())[:10]
    except Exception:
        _today = ""
    _use_live = (not requested) or (bool(_today) and requested >= _today)
    live_map = _live_index_quotes() if _use_live else {}

    out = []
    for x in rows:
        code = x.get("code")
        close = _num(x.get("close"))
        chg = _num(x.get("chg_pct"))
        support = _num(x.get("support"))
        _lq = live_map.get(code) or {}
        _lpx = _num(_lq.get("price"))
        if _lpx:                               # 有实时价 ⇒ 现价/涨跌用实时
            close = _lpx
            if _lq.get("chg_pct") is not None:
                chg = _num(_lq.get("chg_pct"))
        # ★★ 2026-11-05 护栏：`compute()` 的 K 线源存在串标的的既有 bug ——
        #   实测 2026-11-04 落库：`000905 中证500 close=8.33/support=8.04`、
        #   `000985 中证全指 close=15.95/support=14.66`，而真实点位是 6740 / 5211。
        #   用这种 support 去算实时距离会得到 **83742%** 这种荒谬值（本次实测踩到）。
        #   ⇒ 只接受"支撑位与现价同一量级"（0.1x~10x）；否则**沿用落库的 dist_pct**
        #     并标 `support_suspect`，让别人一眼知道是支撑位有问题而不是行情有问题。
        _sup_ok = bool(support and support > 0)
        _dist_live = None
        if _lpx and _sup_ok:
            _ratio = _lpx / support
            if 0.1 <= _ratio <= 10:
                _dist_live = round((_ratio - 1) * 100, 2)
        prev = (close / (1 + chg / 100)) if close and chg is not None else None
        extra = []
        for L in extra_map.get(code or "", []):
            extra.append({
                "level": L,
                "dist_pct": round((close / L - 1) * 100, 2) if close else None,
                "broke": bool(close and close < L),
                "reclaimed": bool(prev and close >= L > prev),
            })
        out.append({
            "code": code, "name": x.get("name"),
            "close": close, "chg_pct": chg,
            "support": support, "support_type": x.get("support_type"),
            # 距离用实时价重算（支撑位一天只变一次，不重算）；
            # 支撑位量级可疑 或 无实时值 ⇒ 沿用落库的收盘口径
            "dist_pct": (_dist_live if _dist_live is not None
                         else _num(x.get("dist_pct"))),
            "status": x.get("status"),
            # ⚠️ `broke` 仍是**日批的收盘确认值**，盘中绝不改写（它是状态量，
            #    只在"首次失守"时置位；盘中反复翻转会把它变成噪音）
            "broke": bool(x.get("broke")),
            "first_year_low": bool(x.get("first_year_low")),
            # 盘中是否**正在**跌破 —— 纯展示，与 `broke` 的"首次失守"语义分开
            # ⚠️ 有实时值时返回 True/False（False 也要显式给，前端才知道"已判断"），
            #    无实时值才是 None（未知）
            "live_broke": (bool(_dist_live < 0) if _dist_live is not None else None),
            # 支撑位与现价差一个量级以上 ⇒ 落库数据本身有问题（见上方护栏注释）
            "support_suspect": bool(_lpx and _sup_ok and _dist_live is None),
            "live": bool(_lpx),
            "live_as_of": str(_lq.get("time") or ""),
            "detail": x.get("detail"),
            "extra": extra,
        })
    note = ("观察器：关键位失守只说明结构走坏，不构成买卖信号；"
            "失守后需看能否快速收回。")
    live_any = any(r.get("live") for r in out)
    if live_any:
        note = (f"现价为实时（支撑位仍为 {d} 收盘口径；"
                "`broke` 只在收盘确认时置位）。") + note
    if fallback:
        # 同 crowding_watch：不写"尚未生成"，周末/长假永远不会有该日数据。
        note = f"该日（{requested}）无收盘数据，此处显示 {d} 收盘结果。" + note
    return {"date": d, "rows": out, "requested_date": requested,
            "fallback": fallback, "live": live_any,
            "live_as_of": next((r.get("live_as_of") for r in out if r.get("live")), ""),
            "note": note}
