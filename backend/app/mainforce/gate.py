# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】战法信号主力过滤闸门（PLAN_NEXT_PHASE P0-2 第一路的落地）
================================================================================

验证依据（scripts/strategy_mainforce_filter_test.py，547 信号历史重放，2026-09-05）：

  | 过滤器            | 保留 | 胜率%  | 均收益% | 盈亏比 |
  |---                |---   |---     |---      |---     |
  | A 全部信号（基线） | 501  | 48.1   | 0.07    | 1.05   |
  | D 剔高位           | 407  | 50.4   | 0.402   | 1.32   |
  | G 剔高位+拉升段    | 364  | 50.3   | 0.44    | 1.36   |

分战法（G）：均线回踩 47.1%→76.9%、单阳不破 51.1%→54.5%、龙回头均收益
-0.13%→+0.27%。高位（筹码区间顶部）与拉升段（放量追高）的战法信号
是主要亏损源——形态相似，但主力阶段不对。

口径（G）：信号股 price_pos > 0.75（筹码区间顶部）或 phase=markup（拉升段）
  → 不放行。信号落库保留全量（数据不失真），仅在【企微推送】与【模拟盘入池】
  两个可执行路径生效。

开关：STRATEGY_MAINFORCE_GATE=on（默认）/ off。
================================================================================
"""

import time

from app.mainforce.chips import chip_metrics, chip_series
from app.mainforce.phases import detect_phase

HIGH_POS_THRESHOLD = 0.75   # 筹码区间顶部（与验证口径一致）
_fs_cache = {"ts": 0.0, "map": None}

# ★★ 2026-10-21（egress 实测 1.465GB/天）：本函数一次调用 = 
#   ① `load_flow_map()` **整表** 8.7MB（原来没传窗口）；
#   ② `_load_prices_map()` **全历史** ~40MB（1018 只 × 750 根）。
#   而 `recommendation._recompute_whitelist`（6h 缓存）、`performance._replay_track`
#   （1h 缓存）与各个研究脚本都会调它 —— pg_stat_statements 实测两天内
#   **84 次整表读 / 76 次全历史 IN 查询**，正是这次 1.4GB 的来源。
#   修法（最终形态）：
#     ① 日线走 `research_cache.ohlc_for`（本机 SQLite / 数据包优先），
#        带**新鲜度守卫**（缓存末日 < 最新信号日 ⇒ 回源，宁可多读也不静默放行）；
#     ② 资金流走 `research_cache.flow_map`（本机 24h；闸门只把 flow5 写进输出，
#        `ok` 由 price_pos / phase 决定 ⇒ 陈旧 1 天不改变拦截结论）；
#     ③ 结果做 **30 分钟进程缓存**（白名单 6h / 绩效 1h 两层缓存之间会重复调用）。
#   ⚠️ 日线**不能**加 start 缩短（chip 网格由 bars 的 min/max 决定，会改变 price_pos，
#      实测 n 136→149）。
#   ⚠️ 也不要退化成 `load_flow_map(days=60)`：窗口读每次都要跑 `_stale_flow_codes`
#      的 `GROUP BY code`，且**不读写**整表磁盘缓存 —— 实测一次 **444 秒**。
_GATE_CACHE = {"ts": 0.0, "key": None, "val": None}
_GATE_TTL = 1800.0
_pack_warned = {"done": False}


def _gate_cache_key(signals: list) -> str:
    """signals 的稳定指纹（排序后 md5）—— 同一批信号无论顺序如何都命中同一缓存。"""
    import hashlib
    raw = "|".join(sorted("%s,%s" % (s.get("code"), s.get("date"))
                          for s in signals))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _norm_date(x, as_str: bool):
    """把日期统一成 signals 使用的类型（str 或 date）。

    ★ 为什么需要：本地缓存/数据包里的日期是 TEXT，Supabase 的是 DATE。
      闸门里的 `chip_series(dates_out=...)` 用 `dates_out` 去命中 bar 的日期，
      类型不一致会**静默命中不到** ⇒ 状态缺失 ⇒ 调用方按 ok=True 放行（偏松）。
    """
    if as_str:
        return str(x)[:10]
    import datetime
    if isinstance(x, str):
        try:
            return datetime.date.fromisoformat(x[:10])
        except ValueError:
            return x
    if isinstance(x, datetime.datetime):
        return x.date()
    return x


def _top_up_bars(cached: dict, latest: str) -> tuple:
    """把末根 < latest 的代码**按日期增量**补几根，并写回本机缓存。

    ★★ 2026-10-22：原来的"缓存过旧 ⇒ 整批回源"是新的流量黑洞 ——
      只要本机缓存比今天晚一天（盘后信号日 = 今天），**每个新进程**都会
      退化成 366k 行全历史读（pg_stat_statements：57 次/4h）。
      现在只补 `date >= 各自末根`：常态是 1018 只 × 1 根 ≈ 1k 行，
      写回后下一个进程直接命中。
    返回 (cached, added_rows)。
    """
    from app.database import db
    from app import research_cache

    def _d(x):
        return str(x)[:10]

    tail = [c for c, b in cached.items() if b and _d(b[-1].get("date")) < latest]
    if not tail:
        return cached, 0
    cutoff = min(_d(cached[c][-1].get("date")) for c in tail)
    rows = db.fetch(
        "SELECT code, date, open, high, low, close, volume FROM backtest_prices "
        "WHERE code = ANY(%s) AND date >= %s ORDER BY code, date",
        (tail, cutoff)) or []
    added = 0
    for r in rows:
        c = r["code"]
        arr = cached.setdefault(c, [])
        byd = {_d(x.get("date")): x for x in arr}
        key = _d(r["date"])
        if key in byd:
            continue
        byd[key] = {"date": r["date"], "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"], "volume": r["volume"]}
        cached[c] = [byd[k] for k in sorted(byd)]
        added += 1
    if added:
        try:
            research_cache.upsert_bars({c: cached[c] for c in tail})
        except Exception as e:
            print("[gate] ohlc cache writeback failed (%s)" % type(e).__name__)
    return cached, added


def _load_gate_bars(by_code: dict) -> dict:
    """闸门用日线：**本机缓存/数据包优先**，缺失才回源（2026-10-21/22 egress）。

    背景：原来直接 `_load_prices_map(codes)`（全历史 750 根 × 1018 只 ≈ 40MB/次），
    而 `_load_prices_map` 的进程缓存被 5 万根字节上限清空 ⇒ **每次调用都重新过网**。
    `research_cache.ohlc_for` 以本机 SQLite 为准（脚本跨进程复用；Render 从 pack
    建一次），只有本机没有的代码才回源。
    ⚠️ 新鲜度守卫：缓存末日 < 最新信号日时必须补 —— 否则今天的信号会被
      `d <= bars[-1]` 滤掉 → 闸门静默放行（比多读几 MB 严重得多）。
      但**只增量补**（`_top_up_bars`），不做整批回源。
    """
    codes = set(by_code)
    first = next((d for ds in by_code.values() for d in ds), None)
    as_str = isinstance(first, str)
    latest = max(str(d)[:10] for ds in by_code.values() for d in ds)
    try:
        from app import research_cache
        cached = research_cache.ohlc_for(codes)
        if cached:
            cmax = max((str(b[-1].get("date"))[:10]
                        for b in cached.values() if b), default="")
            if cmax < latest:
                cached, added = _top_up_bars(cached, latest)
                cmax = max((str(b[-1].get("date"))[:10]
                            for b in cached.values() if b), default="")
                print("[gate] ohlc topped up %d rows (cache %s, signal %s)"
                      % (added, cmax, latest))           # ASCII（铁律⑥）
            if cmax >= latest:
                return {c: [dict(b, date=_norm_date(b.get("date"), as_str))
                            for b in bars]
                        for c, bars in cached.items()}
            print("[gate] local ohlc still stale (%s < %s) -> DB fallback"
                  % (cmax, latest))                      # ASCII（铁律⑥）
    except Exception as e:
        print("[gate] local ohlc unavailable (%s) -> DB fallback"
              % type(e).__name__)                        # ASCII（铁律⑥）
    from app.backtest.strategies import _load_prices_map
    return _retry_read(lambda: _load_prices_map(codes), "回测价格批量")


def _load_gate_flow() -> dict:
    """闸门用资金流：**本机缓存优先**（24h），缺失才回源。

    ★ 为什么不用 `load_flow_map(days=60)` 直接读：
      ① 窗口读每次都要先跑 `_stale_flow_codes`（`GROUP BY code` 全表聚合）；
      ② 实测本机一次 60 日窗口读 **444 秒**（Supabase pooler 慢，不是行数问题），
         而且窗口读**不读写**整表磁盘缓存（见 flow.load_flow_map 注释）；
      ③ 闸门的 `flow5` **只用于输出展示**，`ok` 由 price_pos / phase 决定
         （见下方 `blocked` 构造）⇒ 24h 缓存陈旧 1 天也不改变拦截结论。
    ⇒ 回源用**整表读**（有本机 SQLite 兜底：一次之后 24h 内零流量）。
    """
    try:
        from app import research_cache
        m = research_cache.flow_map()          # 本机 SQLite，24h
        if m:
            return m
    except Exception as e:
        print("[gate] local flow cache unavailable (%s) -> DB"
              % type(e).__name__)               # ASCII（铁律⑥）
    from app.mainforce.flow import load_flow_map
    return _retry_read(load_flow_map, "mainflow_history（整表 + 本机磁盘缓存）")


def _mode_on() -> bool:
    import os
    return (os.environ.get("STRATEGY_MAINFORCE_GATE") or "on").strip().lower() == "on"


def _float_map() -> dict:
    if _fs_cache["map"] is None or time.time() - _fs_cache["ts"] > 6 * 3600:
        try:
            from app.mainforce.flow import get_float_shares_from_snapshot
            _fs_cache["map"] = get_float_shares_from_snapshot()
            _fs_cache["ts"] = time.time()
        except Exception:
            _fs_cache["map"] = {}
    return _fs_cache["map"]


def _load_bars(code: str) -> list:
    """K线优先级：评分 K 线缓存（当日就绪）→ backtest_prices。"""
    try:
        from app.scoring.kline_cache import get_cached_klines
        bars = get_cached_klines(code)
        if bars and len(bars) >= 130:
            return bars
    except Exception:
        pass
    try:
        from app.backtest.data import load_prices
        bars = load_prices(code, start="2025-01-01")
        if bars and len(bars) >= 130:
            return bars
    except Exception:
        pass
    return []


def strategy_gate(code: str, name: str = "") -> dict:
    """
    战法信号主力过滤。返回 {ok, reason, phase, price_pos, winner_ratio, flow5}。
    ok=False：高位（price_pos>0.75）或拉升段——形态信号与主力阶段背离。
    """
    empty = {"ok": True, "reason": "", "phase": None, "price_pos": None,
             "winner_ratio": None, "flow5": None}
    if not _mode_on():
        return {**empty, "reason": "gate off"}
    bars = _load_bars(code)
    if not bars:
        return {**empty, "reason": "no bars"}          # 无数据不拦截（保守放行）
    chip = chip_metrics(bars, float_shares=_float_map().get(code))
    phase = detect_phase(bars)
    if not chip or not phase:
        return {**empty, "reason": "no chip/phase"}

    flow5 = None
    try:
        from app.mainforce.flow import load_flow
        rows = load_flow(code)[-5:]
        if rows:
            flow5 = round(sum(float(r.get("main_pct") or 0) for r in rows), 2)
    except Exception:
        pass

    blocked = []
    if chip["price_pos"] > HIGH_POS_THRESHOLD:
        blocked.append(f"高位(price_pos {chip['price_pos']:.2f})")
    if phase["phase"] == "markup":
        blocked.append(f"拉升段(ret20 {phase['ret_20']:+.0f}%,量比 {phase['vol_ratio']})")

    return {
        "ok": not blocked,
        "reason": "；".join(blocked),
        "phase": phase["phase"],
        "price_pos": chip["price_pos"],
        "winner_ratio": chip["winner_ratio"],
        "flow5": flow5,
    }


def _retry_read(fn, what: str, tries: int = 4):
    """读库重试（Supabase pooler 空闲断连 SSL，重试即恢复）。

    ★ 2026-09-20：`gate_states_for_signals` 原先**没有任何重试** ⇒ 首次读
      `mainflow_history`（8.7MB 整表、每进程一次）撞上 pooler 断连时，异常直接冒到
      调用方（`recommendation._recompute_whitelist` 捕到后打印「门控状态读取失败
      （降级放行）」）⇒ **整轮白名单重算在"无主力闸门"口径下完成**（实测连续两次复现）。
      而主力闸门是当前唯一被验证有效的提纯手段（胜率 48.1→50.3%、均收益 +0.07→+0.44%、
      盈亏比 1.05→1.36）⇒ 它静默失效 = **绩效口径整体失真**，且失真方向偏悲观。
      与 `scripts/*.py` 里的 `q()` 同款：重试 + `_reset_pg_conn()` + 递增退避。
      （已确认 `load_flow_map` 失败**不会**缓存空值 —— 缓存只在自己成功之后写 ⇒ 重试有效。）
    """
    import time
    last = None
    for i in range(max(1, tries)):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"[mainforce.gate] {what} 读取失败（第 {i + 1}/{tries} 次）: {e}")
            try:
                from app.database import db
                db._reset_pg_conn()
            except Exception:
                pass
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
    raise last


def gate_states_for_signals(signals: list) -> dict:
    """
    批量还原信号日当日的主力过滤状态（无前视，供白名单重算/回测复用）。
    signals: [{code, date, ...}]；返回 {(code, date): gate_dict}（同 strategy_gate 结构）。
    ★ 30 分钟进程缓存（见文件头 `_GATE_CACHE` 说明）：同一批 signals 只算一次。
    """
    if not signals:
        return {}
    ck = _gate_cache_key(signals)
    now = time.time()
    if _GATE_CACHE["key"] == ck and now - _GATE_CACHE["ts"] < _GATE_TTL:
        return dict(_GATE_CACHE["val"])

    by_code = {}
    for s in signals:
        by_code.setdefault(s["code"], []).append(s["date"])
    # ★ 2026-10-21：本机资金流缓存优先（原来整表 8.7MB/次，见 `_load_gate_flow`）
    flow_map = _load_gate_flow()
    fs_map = _retry_read(_float_map, "流通股本 map")
    # ★ 一次性告警：DATA_SOURCE 不是 pack 时，下面这步会走 Supabase 全历史
    #   （~40MB/次）。研究脚本/本地默认 db ⇒ 每次跑都白付。日志里能看到。
    if not _pack_warned["done"]:
        _pack_warned["done"] = True
        try:
            from app import pack_source
            if not pack_source.enabled():
                print("[gate] WARN DATA_SOURCE != pack -> cache miss may read "
                      "full history from Supabase (~40MB)")   # ASCII（铁律⑥）
        except Exception:
            pass
    out = {}
    # ★ 批量加载（单 IN 查询 + 6h 进程缓存，与战法回放路径共享）——
    #   逐股 load_prices 是 253 次独立查询，曾把绩效接口拖到 30s 超时
    # ★ 2026-09-13：**不要**给这里加 start 缩短历史！chip_series 的价格网格
    #   由传入 bars 的 min/max 决定（chips.py L84-89），缩短历史会改变网格 →
    #   price_pos 跨过 0.75 阈值 → 闸门判断漂移（实测 n 136→149）。
    # ★★ 2026-10-21：改走 `_load_gate_bars` —— 本机缓存/数据包优先（见其说明），
    #   过旧才回源。这一条是当天 1.4GB 的主因（76 次全历史 IN 查询）。
    bars_map = _load_gate_bars(by_code)
    for code, dates in by_code.items():
        bars = bars_map.get(code)
        if not bars or len(bars) < 130:
            continue
        valid = [d for d in dates if bars[69]["date"] <= d <= bars[-1]["date"]]
        if not valid:
            continue
        chips = chip_series(bars, float_shares=fs_map.get(code), dates_out=valid)
        phases = _phase_series_safe(bars, valid)
        flow_rows = flow_map.get(code) or []
        flow_idx = {r["date"]: k for k, r in enumerate(flow_rows)}
        for d in valid:
            cm, pm = chips.get(d), phases.get(d)
            if not cm or not pm:
                continue
            flow5 = None
            if d in flow_idx:
                k = flow_idx[d]
                flow5 = round(sum(float(r.get("main_pct") or 0)
                                  for r in flow_rows[max(0, k - 4):k + 1]), 2)
            blocked = []
            if cm["price_pos"] > HIGH_POS_THRESHOLD:
                blocked.append(f"高位(price_pos {cm['price_pos']:.2f})")
            if pm["phase"] == "markup":
                blocked.append("拉升段")
            out[(code, d)] = {
                "ok": not blocked, "reason": "；".join(blocked),
                "phase": pm["phase"], "price_pos": cm["price_pos"],
                "winner_ratio": cm["winner_ratio"], "flow5": flow5,
            }
    _GATE_CACHE.update(ts=time.time(), key=ck, val=out)
    return dict(out)


def _phase_series_safe(bars: list, dates_out: list) -> dict:
    try:
        from app.mainforce.phases import phase_series
        return phase_series(bars, dates_out=dates_out)
    except Exception:
        return {}
