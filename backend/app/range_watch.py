# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】震荡区间识别（Range Watch）—— 给每个价格动作加上"箱体坐标"
================================================================================
背景（2026-11-05，用户："底部震荡常匪夷所思、散户易被洗下车"）：
  排查后发现**结构性缺陷** —— 系统所有"卖出触发"都是**幅度**判据：
      `exit_advice` 9 档：−8% / 跌破支撑 / 回撤 8% / 浮盈 30% …
      `rules.yaml`：stop_loss_hit / hold_3d_review(固定3日) / loss_over_7pct …
  **没有一条"环境"判据**（这个价位本身是否正当在震荡区间的正常波动）。
  在区间下沿，−8% 可能只是日常试探；但在规则眼里它和"趋势破位"长得一模一样。

  更具体：`mainforce/phases.py` 虽有 `shakeout`（洗盘）阶段，但判定要求
  `ret_20 > 8 and px > ma60` —— 那是**上涨途中的洗盘**。实测 2026-11-04 全市场：
      decline 1903 (66%) / sideways 596 / accumulation 198 / markup 138
      / distribution 12 / **shakeout 只有 6 只（0.2%）**
  底部区域 `ret_20` 为负、`px` 在 `ma60` 下 ⇒ 该规则永不触发。
  且 `stock_facts_wide` 只有 `dist_from_60d_high`（距高点多远），
  **没有 `range_low/range_high/pos_in_range`** —— 系统不知道"现在在箱体哪一格"。

★ 本模块只做**功能 1：区间识别**（用户 2026-11-05 明确"先只做功能 1"）。
  后续功能（洗盘打分 / 持仓耐心度 / 异动区分）都读这里的字段，不重复算。

★ 红线：**不改评分、不进闸门、不碰模拟盘**（与拥挤赛道/关键位/跨市场异动同级）。
  它只是给"该不该拿"提供第 5 个坐标，判断权仍在人。

--------------------------------------------------------------------------------
一、区间怎么定义（三个关键取舍，改前必读）
--------------------------------------------------------------------------------
  ① **区间上下沿用"前 59 日"，不含当日**。
     否则当日永远在自己算出的区间内，`pos_in_range` 恒 ≤1，
     "今天突破了"这件事在数据上永远不成立。
  ② **`range_days` = 距最近一次创新高/新低的天数**（取较晚的那个）。
     含义：区间从"最后一次打破平衡"那天开始，此后价格都在 [lo, hi] 内来回。
     不用"连续 N 日收盘在区间内"——那种口径遇到一次假突破就归零，震荡期反复触发。
  ③ **振幅窗口要卡区间**（15%~35%）。太窄（<15%）是死水没有交易价值；
     太宽（>35%）已经是趋势不是震荡。不卡这个口径，会把"暴跌中的中继"
     也标成区间，反而害人。

二、状态口径
    突破   close > hi × 1.005     （放量确认留给功能 4，这里只报位置）
    破位   close < lo × 0.995
    上沿   pos ≥ 0.75
    下沿   pos ≤ 0.25
    中部   其余

三、存储纪律（数据库已 398MB/500MB，2026-10-29 实测）
  全市场 2864 行/天 ⇒ **只留最近 `KEEP_DAYS=5` 天**，每天先删旧再写。
  5 天 ≈ 1.4MB，可忽略；回放/对比用 5 天足够（区间判定的价值在"当下"）。

四、性能
  K 线走 `crowding_watch._pack()`（本地数据包，零外呼）。逐只 `get_klines` 与
  `crowding_watch.compute` 同款；实测见 `compute()` 返回的 `elapsed_ms`。
================================================================================
"""
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.database import db

_TABLE = "range_watch"
_READY = False

# ── 阈值（集中在此，便于回测回调；改动必须同步 docstring）──
MIN_AMP = 0.15          # 60 日振幅下限：低于此视为死水
MAX_AMP = 0.35          # 上限：高于此已是趋势，不是震荡
MIN_RANGE_DAYS = 15     # 区间已持续的最小交易日数（洗盘需要时间）
RANGE_WIN = 60          # 用最近 60 根定区间上下沿
# ★ 必须比 RANGE_WIN 长：`range_days` 要往前回溯"价格何时离开过这个箱体"，
#   若只看 60 根，窗口内的收盘必然都在 [lo,hi] 内 ⇒ 恒等于 59，毫无信息。
MIN_BARS = 120
KEEP_DAYS = 5           # 落库只留 5 天（存储纪律）

# ⚠️ 破位要比突破**敏感**（2026-11-05 实测修正）：
#   初版 `BREAK_DN=0.995` 导致「收 2.03 / 区间下沿 2.04」被算成**下沿**而不是破位
#   （2.03 / 2.04 = 0.9951 > 0.995），于是全市场 909 只挤在"下沿"里、
#   其中大量其实已经跌穿区间 —— 状态分布完全失真。
#   止损方向宁快不宁慢：跌破前 59 日最低价 0.1% 就算破位；
#   突破则需 0.5% 确认（假突破太常见，见 `range_watch` 后续功能 4）。
BREAK_UP = 1.005        # 收盘 > hi × 1.005 ⇒ 突破
BREAK_DN = 0.999        # 收盘 < lo × 0.999 ⇒ 破位
EDGE_POS = 0.25         # pos ≤ 0.25 下沿 / ≥ 0.75 上沿

STATE_CN = {"突破": "突破", "破位": "破位", "下沿": "下沿", "上沿": "上沿", "中部": "中部"}


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT, code TEXT, name TEXT, industry TEXT,
            close REAL, range_low REAL, range_high REAL,
            amp60 REAL, amp20 REAL, range_days INTEGER,
            pos_in_range REAL, state TEXT, in_range INTEGER,
            vol_ratio REAL, created_at TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    _READY = True


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num(b, k) -> Optional[float]:
    return _f(b.get(k)) if isinstance(b, dict) else None


def _amplitude(bars: List[dict], n: int) -> Optional[float]:
    """最近 n 根的振幅 (max_high - min_low) / min_low。"""
    sub = bars[-n:]
    hs = [h for h in (_num(b, "high") for b in sub) if h]
    ls = [lo for lo in (_num(b, "low") for b in sub) if lo]
    if len(hs) < n * 0.9 or len(ls) < n * 0.9:
        return None
    lo = min(ls)
    if lo <= 0:
        return None
    return (max(hs) - lo) / lo


def analyze_bars(bars: List[dict]) -> Optional[Dict]:
    """单只票的区间指标。不足 60 根或数据异常返回 None。

    ★ 抽成独立纯函数：便于用构造数据单测（不依赖数据包/数据库）。
    """
    if not bars or len(bars) < MIN_BARS:
        return None
    win = bars[-RANGE_WIN:]
    # ① 上下沿用**前 59 日**（不含当日），否则当日永远在自己区间内
    hist = win[:-1]
    hs = [h for h in (_num(b, "high") for b in hist) if h]
    ls = [lo for lo in (_num(b, "low") for b in hist) if lo]
    if len(hs) < RANGE_WIN - 5 or len(ls) < RANGE_WIN - 5:
        return None
    hi, lo = max(hs), min(ls)
    if lo <= 0 or hi <= lo:
        return None
    close = _num(win[-1], "close")
    if not close or close <= 0:
        return None

    amp60 = (hi - lo) / lo
    pos = (close - lo) / (hi - lo)

    # ② range_days：从最近往前数，**收盘价连续待在 [lo, hi] 内的天数**。
    #    ⚠️ 回溯窗口必须 > RANGE_WIN，否则恒等于窗口长度（见 MIN_BARS 注释）。
    #    ⚠️ 用收盘价而非 high/low：盘中刺破后收回，恰恰是洗盘的定义，
    #       拿最高价去判会把每次下影线都算成"离开区间"。
    range_days = 0
    for b in reversed(bars[-MIN_BARS:-1]):     # 不含当日（当日单独判状态）
        c = _num(b, "close")
        if c is None or c > hi or c < lo:
            break
        range_days += 1

    if close > hi * BREAK_UP:
        state = "突破"
    elif close < lo * BREAK_DN:
        state = "破位"
    elif pos <= EDGE_POS:
        state = "下沿"
    elif pos >= 1 - EDGE_POS:
        state = "上沿"
    else:
        state = "中部"

    amp20 = _amplitude(win, 20)
    in_range = (MIN_AMP <= amp60 <= MAX_AMP
                and range_days >= MIN_RANGE_DAYS
                and state in ("下沿", "中部", "上沿"))

    # 量能：近 5 日均量 / 60 日均量（功能 2 洗盘打分要用，这里先备着）
    vols = [v for v in (_num(b, "volume") for b in win) if v and v > 0]
    vol_ratio = None
    if len(vols) >= 55:
        v5 = sum(vols[-5:]) / 5
        v60 = sum(vols) / len(vols)
        if v60 > 0:
            vol_ratio = round(v5 / v60, 3)

    return {
        "close": round(close, 3),
        "range_low": round(lo, 3),
        "range_high": round(hi, 3),
        "amp60": round(amp60, 4),
        "amp20": round(amp20, 4) if amp20 is not None else None,
        "range_days": int(range_days),
        "pos_in_range": round(pos, 4),
        "state": state,
        "in_range": 1 if in_range else 0,
        "vol_ratio": vol_ratio,
    }


def _bulk_klines(ps, codes: List[str]) -> Dict[str, List[dict]]:
    """批量取最近 120 根 K 线。

    ★★ 性能关键（2026-11-05 实测驱动）：`crowding_watch._PackView.get_klines`
      **每只都新开一个 sqlite 连接**，2864 只直接跑到超时（本地实测 >2 分钟无输出）。
      ⇒ 这里按 300 只一批、复用同一连接批量查；`pack_source`（非本地包）没有
        `path` 属性，仍逐只（那边有进程缓存，且只在服务器路径下走）。
    """
    path = getattr(ps, "path", None)
    if not path:
        return {c: (ps.get_klines(c) or []) for c in codes}

    import sqlite3
    out: Dict[str, List[dict]] = {}
    con = sqlite3.connect(path)
    try:
        for i in range(0, len(codes), 300):
            chunk = [str(c) for c in codes[i:i + 300]]
            ph = ",".join(["?"] * len(chunk))
            # 只用窗口函数取每只最后 120 根，不依赖日期字符串格式
            # （包里 date 的格式未约定，用 `date >= '2026-05-01'` 比大小会静默失效）
            sql = (
                f"SELECT code, date, high, low, close, volume FROM ("
                f"  SELECT code, date, high, low, close, volume, "
                f"         ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn"
                f"  FROM klines WHERE code IN ({ph})"
                f") WHERE rn <= {MIN_BARS} ORDER BY code, date"
            )
            try:
                for r in con.execute(sql, tuple(chunk)):
                    out.setdefault(str(r[0]), []).append({
                        "date": r[1], "high": r[2], "low": r[3],
                        "close": r[4], "volume": r[5]})
            except Exception as e:
                print(f"[range] bulk query failed: {str(e)[:80]}")
                break
    finally:
        con.close()
    return out


def compute(day: str = None, codes: List[str] = None, limit: int = 0) -> Dict:
    """全市场算一遍并落库（日批调用）。

    `codes` / `limit` 只用于本地调试与冒烟（正常日批不传 = 全市场）。
    """
    ensure_table()
    t0 = time.time()
    from app.crowding_watch import _pack
    from app.industry_map import get_industry_map

    try:
        ps = _pack()
        allc = [c for c in (ps.get_codes() or [])
                if str(c).isdigit() and len(str(c)) == 6]
    except Exception as e:
        return {"ok": False, "reason": f"数据包不可用: {str(e)[:80]}"}
    codes = [str(c) for c in codes] if codes else allc
    if limit:
        codes = codes[:int(limit)]
    imap = get_industry_map() or {}
    names: Dict[str, str] = {}

    # ★ 批量取 K 线（见 `_bulk_klines`）；逐只 while 会超时
    klmap = _bulk_klines(ps, codes)
    rows, data_day, skipped = [], "", 0
    for code in codes:
        bars = klmap.get(str(code)) or []
        if not bars:
            skipped += 1
            continue
        if not data_day:
            data_day = str(bars[-1].get("date") or "")[:10]
        m = analyze_bars(bars)
        if not m:
            skipped += 1
            continue
        rows.append({
            "code": str(code), "name": names.get(code, ""),
            "industry": imap.get(str(code), ""), **m,
        })

    if not rows:
        return {"ok": False, "reason": "无有效K线"}

    # 名称：优先用 stock_facts_wide 当日（已有 name 列，避免额外请求）
    try:
        nm = {str(r["code"]): r["name"] for r in db.fetch(
            "SELECT code, name FROM stock_facts_wide "
            "WHERE date = (SELECT MAX(date) FROM stock_facts_wide)")}
    except Exception:
        nm = {}
    for r in rows:
        r["name"] = nm.get(r["code"], "")

    d = str(day or data_day or datetime.now().strftime("%Y-%m-%d"))[:10]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        db.execute(f"DELETE FROM {_TABLE} WHERE date = %s", (d,))
        # ★★ 批量插入（2026-11-05 实测驱动，**必须有**）：
        #   逐行 `db.execute(INSERT)` 每次都是一次到 Supabase 的往返 ——
        #   600 行实测 **715 秒**（1.2 秒/行），全市场 2864 行会跑到半小时以上。
        #   改 150 行一批后同一批数据 <3 秒。写法与 `stock_facts.build` 一致。
        cols = ("date", "code", "name", "industry", "close", "range_low", "range_high",
                "amp60", "amp20", "range_days", "pos_in_range", "state", "in_range",
                "vol_ratio", "created_at")
        marks = "(" + ",".join(["%s"] * len(cols)) + ")"
        for i in range(0, len(rows), 150):
            chunk = rows[i:i + 150]
            db.execute(
                f"INSERT INTO {_TABLE} ({','.join(cols)}) VALUES "
                + ",".join([marks] * len(chunk)),
                tuple(v for r in chunk for v in (
                    d, r["code"], r["name"], r["industry"], r["close"], r["range_low"],
                    r["range_high"], r["amp60"], r["amp20"], r["range_days"],
                    r["pos_in_range"], r["state"], r["in_range"], r["vol_ratio"], now)))
        # 存储纪律：只留最近 KEEP_DAYS 个**数据日期**
        # ★★ 2026-11-05 实测踩坑：原来用"当前日期 − 5 天"当截止 ——
        #   而数据包的 K 线日期可能滞后（本地实测只到 2026-10-22），
        #   `date < 2026-10-31` 会把**刚写进去的数据立刻删光**
        #   （现象：compute 报 594 行，查库只剩 31 行旧数据）。
        #   ⇒ 改成按"库里有哪几个数据日期"取最近 KEEP_DAYS 个，与自然日无关。
        try:
            keep = [str(r["date"]) for r in db.fetch(
                f"SELECT DISTINCT date FROM {_TABLE} ORDER BY date DESC LIMIT %s",
                (KEEP_DAYS,)) or []]
            if keep:
                ph = ",".join(["%s"] * len(keep))
                db.execute(f"DELETE FROM {_TABLE} WHERE date NOT IN ({ph})", tuple(keep))
        except Exception as e:
            print(f"[range] retention failed: {str(e)[:80]}")
    except Exception as e:
        print(f"[range] save failed: {str(e)[:100]}")
        return {"ok": False, "reason": f"落库失败: {str(e)[:80]}", "rows": len(rows)}

    in_r = sum(1 for r in rows if r["in_range"])
    low_edge = sum(1 for r in rows if r["state"] == "下沿")
    return {
        "ok": True, "date": d, "n": len(rows), "skipped": skipped,
        "in_range": in_r, "low_edge": low_edge,
        "state_counts": {s: sum(1 for r in rows if r["state"] == s)
                         for s in STATE_CN},
        "elapsed_ms": int((time.time() - t0) * 1000),
    }


def _holdings_codes() -> List[str]:
    """我的持仓代码（`user_portfolio`）——前端最关心"我手上这些在不在区间里"。"""
    try:
        from app.portfolio_scope import portfolio_where
        w, p = portfolio_where()
        rows = db.fetch(f"SELECT code FROM user_portfolio {w}", p)
        return [str(r["code"]) for r in (rows or []) if r.get("code")]
    except Exception:
        return []


def load(day: str = None, state: str = None, limit: int = 300) -> Dict:
    """读库。`day` 缺省取最新；`state` 可筛选（下沿/上沿/破位/突破）。"""
    ensure_table()
    try:
        if day:
            d = str(day)[:10]
        else:
            r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE}")
            d = str((r or {}).get("d") or "")[:10]
        if not d:
            return {"date": "", "rows": [], "note": "暂无数据（等日批首次运行）"}
        if state:
            rows = db.fetch(
                f"SELECT * FROM {_TABLE} WHERE date=%s AND state=%s "
                f"ORDER BY amp60 DESC LIMIT %s", (d, state, int(limit)))
        else:
            # 默认按"离区间边缘最近"排：下沿最需要关注
            rows = db.fetch(
                f"SELECT * FROM {_TABLE} WHERE date=%s "
                f"ORDER BY in_range DESC, pos_in_range ASC LIMIT %s", (d, int(limit)))
    except Exception as e:
        return {"date": day, "rows": [], "note": f"读取失败: {str(e)[:80]}"}

    items = []
    for x in rows or []:
        items.append({
            "code": str(x.get("code") or ""), "name": x.get("name") or "",
            "industry": x.get("industry") or "",
            "close": _f(x.get("close")),
            "range_low": _f(x.get("range_low")),
            "range_high": _f(x.get("range_high")),
            "amp60": _f(x.get("amp60")),
            "amp20": _f(x.get("amp20")),
            "range_days": x.get("range_days"),
            "pos": _f(x.get("pos_in_range")),
            "state": x.get("state") or "",
            "in_range": bool(x.get("in_range")),
            "vol_ratio": _f(x.get("vol_ratio")),
        })

    # 统计 + 我的持仓切片（一次查询全搞定，前端不必再过滤）
    try:
        agg = db.fetch(
            f"SELECT state, COUNT(*) AS n, SUM(in_range) AS ir FROM {_TABLE} "
            f"WHERE date=%s GROUP BY state", (d,))
    except Exception:
        agg = []
    counts = {str(a.get("state")): int(a.get("n") or 0) for a in agg or []}
    total_in = sum(int(a.get("ir") or 0) for a in agg or [])

    holds = set(_holdings_codes())
    mine = [it for it in items if it["code"] in holds]

    return {
        "date": d,
        "total": sum(counts.values()),
        "in_range": total_in,
        "state_counts": counts,
        "rows": items,
        "mine": mine,
        "note": ("区间口径：上下沿取前 59 日高低点；`range_days`=距最近一次创新高/新低；"
                 "振幅需在 15%~35% 且持续 ≥15 日才算区间。纯观察，不改评分/闸门。"),
    }


def for_codes(codes: List[str], day: str = None) -> Dict[str, Dict]:
    """批量取指定股票的区间指标（给 coach / 持仓页复用，不再算一遍）。"""
    ensure_table()
    codes = [str(c) for c in (codes or []) if c]
    if not codes:
        return {}
    if not day:
        r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE}")
        day = str((r or {}).get("d") or "")[:10]
    if not day:
        return {}
    ph = ",".join(["%s"] * len(codes))
    try:
        rows = db.fetch(
            f"SELECT * FROM {_TABLE} WHERE date=%s AND code IN ({ph})",
            tuple([day] + codes))
    except Exception:
        return {}
    return {str(r["code"]): {
        "state": r.get("state"), "pos": _f(r.get("pos_in_range")),
        "low": _f(r.get("range_low")), "high": _f(r.get("range_high")),
        "range_days": r.get("range_days"), "in_range": bool(r.get("in_range")),
        "vol_ratio": _f(r.get("vol_ratio")),
    } for r in rows or []}
