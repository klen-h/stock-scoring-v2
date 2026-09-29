"""
================================================================================
【文件作用】盘中实时涨停/跌停/炸板统计（zzshare rt_k 落地，2026-09-29）
================================================================================
背景（为什么需要它）：
  现有涨停口径有三处已知局限，都写在各处注释里，本模块正是为补掉它们而生：
  1. `routers/market.overview` 的涨跌停家数用 **`change_pct >= 9.9` 近似**
     （其注释自陈"科创板/创业板 20% 用 9.9 是近似"）⇒ 20cm 票涨 10% 被误判涨停。
  2. 工作台「涨停梯队」读 `zz_daily_snapshots`（**日批**，收盘后才有）⇒ 盘中看到的是
     上一交易日快照；`_limit_stats` 的炸板率来自 `backtest_prices`（**仅 ~839 只覆盖**）。
  3. 竞价看板注释自陈"仅 839 只股票有日线数据，可能少于真实涨停家数"。

  本模块用 `rt_k`（全市场实时快照，2026-09-29 V2 验证：3 批共 5569 行 / 14s，
  `fields='all'` 含 `high_limit`/`low_limit`）做**精确判定**：
  `close == high_limit` 即涨停（不依赖涨跌幅近似），**覆盖率 100%**。

口径（★ 与日批权威口径的关系）：
  · 本模块 = **盘中动态口径**（此刻快照），收盘前炸板可能回封、涨停可能打开
    ⇒ 只做"盘中温度计"，**收盘后的权威家数仍以日批 `uplimit_hot.ban_info` 为准**
    （沿用 `routers/market._dedup_uplimit_stocks` 已确立的纪律）。
  · 排除停牌（vol<=0）与新股首日（pre_close<=0；V3 实测该日 high_limit 不可靠）。
  · 一字板 = `high == low` 且封住（★ 不是"开盘即涨停"——T 字板开盘价也是涨停价，
    但盘中打开过、随时会炸，不能当一字；此判据与 `_limit_stats` 完全一致）。
  · 判据常量（`_SEAL_TOL`/`_BREAK_TOL`/`_BIG_LOSS_PCT`）**复用 `routers/market`**，
    保证盘中与日批的炸板率是**同一个定义**（见 `compute` docstring）。

工程约束：
  · **唯数据源单点**：走 `zzshare_client.get_api()`（接口变更只改一处）；
  · **fail-open**：任一批失败只记日志、用已拿到的部分，绝不抛给调用方；
  · **60s 进程缓存**：token 限 20 次/分，3 批/轮 ⇒ 60s 一轮安全（不撞 429）；
  · 日志 ASCII（铁律⑥：本地 Windows GBK 控制台）。
================================================================================
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# 全市场分 3 批（验证实测：沪 2320 / 深 2902 / 北 347，合计 5569；14s）
_BATCHES = ("60*.SH,68*.SH", "00*.SZ,30*.SZ", "8*.BJ,4*.BJ,92*.BJ")

_CACHE = {"ts": 0.0, "val": None}
_LOCK = threading.Lock()
TTL = 60                      # 秒：一轮 3 次请求 ⇒ ≤3 次/分（token 上限 20 次/分）


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _f(v) -> float:
    """安全转 float（None/''/'-' → 0.0）——与 routers/market._num 同思路。"""
    try:
        if v is None or v == "" or v == "-":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fetch_all() -> List[dict]:
    """3 批 rt_k（fields='all'）→ list[dict]。单批失败 fail-open（保留已拿到的）。"""
    from app.zzshare_client import get_api
    api = get_api()
    rows: List[dict] = []
    for b in _BATCHES:
        try:
            df = api.rt_k(ts_code=b, fields="all")
            if df is not None and hasattr(df, "to_dict"):
                rows.extend(df.to_dict("records"))
        except Exception as e:
            print(f"[rt_uplimit] batch failed {b}: {str(e)[:80]}")   # ASCII
    return rows


def fetch_all() -> List[dict]:
    """全市场实时快照（3 批 rt_k / `fields=all`）—— **唯一抓取实现**。

    供 `compute()` 与 `promotion` 共用：若各抓一遍会翻倍 token 消耗
    （3 批 × 2 处 × 120s 循环 ⇒ 撞 token 上限 20 次/分）。
    """
    return _fetch_all()


def classify(rows: List[dict]) -> Dict:
    """**逐票分类（判据的唯一实现）** —— `compute()` 与 `promotion` 都走这里。

    ★★ 为什么必须抽出：`compute()` 出统计（家数/炸板率）、`promotion` 出晋级率
      （昨日涨停股今日封没封），两者都依赖"这只票此刻算不算涨停"。若各写一遍，
      "涨停"在系统里就有了两个定义 —— 正是 `compute()` docstring 申明的纪律所要避免的。

    返回 `{"recs": [rec...], "trading": n, "limit_down": n}`：
      · `recs` 与 `rows` **同序**（仅剔除停牌 vol<=0 / 新股首日 pre_close<=0），
        不按代码去重 —— 避免改变 `compute()` 的计数语义（北交所等可能有重复码）。
      · 每 rec：`code/name/bucket/sealed/broken/oneword/limit_down/big_loss/
        close/pre_close/high_limit`。
    """
    from app.routers.market import _bucket_of, _BREAK_TOL, _SEAL_TOL, _BIG_LOSS_PCT
    recs: List[dict] = []
    trading = 0
    limit_down = 0
    for r in rows:
        close = _f(r.get("close"))
        pre = _f(r.get("pre_close"))
        vol = _f(r.get("vol"))
        hi_lim = _f(r.get("high_limit"))
        lo_lim = _f(r.get("low_limit"))
        high = _f(r.get("high"))
        low = _f(r.get("low"))
        # 排除停牌（无成交）与新股首日（pre_close<=0；V3 实测该日 high_limit 不可靠）
        if vol <= 0 or close <= 0 or pre <= 0:
            continue
        trading += 1
        rec = {
            "code": str(r.get("ts_code") or r.get("code") or "").split(".")[0],
            "name": str(r.get("name") or ""),
            "bucket": _bucket_of(str(r.get("ts_code") or ""), str(r.get("name") or "")),
            "sealed": False, "broken": False, "oneword": False,
            "limit_down": False, "big_loss": False,
            "close": close, "pre_close": pre, "high_limit": hi_lim,
        }
        if lo_lim > 0 and close <= lo_lim:
            limit_down += 1
            rec["limit_down"] = True
        if hi_lim > 0 and high >= hi_lim * (1 - _SEAL_TOL):     # 曾触及涨停价
            rec["sealed"] = close >= hi_lim * (1 - _BREAK_TOL)
            rec["oneword"] = rec["sealed"] and abs(high - low) < 1e-9
            rec["broken"] = not rec["sealed"]
            if rec["broken"]:
                rec["big_loss"] = (close / pre - 1) * 100 < _BIG_LOSS_PCT
        recs.append(rec)
    return {"recs": recs, "trading": trading, "limit_down": limit_down}


def compute(rows: List[dict], cls: Dict = None) -> Dict:
    """从全市场实时快照算涨停/跌停/炸板（**精确口径**，与 `_limit_stats` 严格同源）。

    ★★ 口径纪律（为什么必须逐字对齐 `routers/market._limit_stats`）：同一个"炸板率"
      在整个系统里只能有一个定义，否则日批（历史口径）与盘中（实时口径）会给出
      两个数、无法对照。因此这里**复用同一组常量与判据**：
        · `_SEAL_TOL=0.001` 曾触板容差（最高价触及即可）
        · `_BREAK_TOL=0.002` 封住判定缓冲（板幅 −0.2%，防一分钱误差误判回封）
        · 一字板 = `high == low` 且封住（★ 不是"开盘即涨停"——T 字板开盘也涨停但会炸）
        · 炸板率分母 = 曾触板 − 一字（一字不可能炸，留在分母会系统性压低炸板率）
      差别只在两处（且都是本模块的目的）：① 判据用数据源给的 `high_limit`（精确涨停价，
      含 ST5/双创20/北交30 制度差异与分位四舍五入），不反算；② 覆盖率 = 全市场。
    """
    # ★ 2026-09-29：判据已抽到 `classify()`（**唯一实现**）—— `promotion`（晋级率）
    #   复用同一份逐票结果，保证"涨停"在整个系统里只有一个定义。
    #   `cls` 由外部传入时复用（`snapshot()` 一次分类供 compute + promotion 共用）。
    cls = cls or classify(rows)
    total = len(rows)
    trading = cls["trading"]
    limit_down = cls["limit_down"]
    tot = {"touched": 0, "sealed": 0, "oneword": 0, "broken": 0, "big_loss": 0}
    buckets: Dict[str, Dict] = {}
    for rec in cls["recs"]:
        if not (rec["sealed"] or rec["broken"]):    # 未触及涨停价 ⇒ 不进涨停统计
            continue
        b = buckets.setdefault(rec["bucket"], {"touched": 0, "sealed": 0, "oneword": 0,
                                               "broken": 0, "big_loss": 0})
        b["touched"] += 1
        tot["touched"] += 1
        if rec["sealed"]:
            b["sealed"] += 1
            tot["sealed"] += 1
            if rec["oneword"]:
                b["oneword"] += 1
                tot["oneword"] += 1
        else:
            # 盘中曾摸板、此刻未封死 ⇒ 炸板（★ 动态：收盘前可能回封）
            b["broken"] += 1
            tot["broken"] += 1
            if rec["big_loss"]:
                b["big_loss"] += 1
                tot["big_loss"] += 1
    den = max(0, tot["touched"] - tot["oneword"])
    for b in buckets.values():
        bden = max(0, b["touched"] - b["oneword"])
        b["break_rate"] = round(b["broken"] / bden * 100, 1) if bden else None
    return {
        "total": total,            # 快照行数（应 ≈5569）
        "trading": trading,        # 排除停牌/新股后的有效样本
        "limit_up": tot["sealed"],     # 涨停家数（此刻封住，精确口径）
        "limit_down": limit_down,
        "touched": tot["touched"],     # 曾触及涨停
        "broken": tot["broken"],
        "broken_rate": round(tot["broken"] / den * 100, 1) if den else None,
        "big_loss_rate": (round(tot["big_loss"] / den * 100, 1) if den else None),
        "yizi": tot["oneword"],        # 一字板（high==low 且封住）
        "buckets": buckets,            # 按板幅分桶（主板/20cm/30cm/ST5）
        "note": ("盘中动态口径（此刻快照，与日批炸板率同判据/同缓冲）："
                 "炸板可能回封、涨停可能打开 ⇒ 收盘后权威家数以日批 ban_info 为准"),
    }


def _safe_promotion(cls: Dict, data_date: str = None) -> Dict:
    """`promotion.build_promotion` 的失败静默包装（辅助块绝不拖垮主统计）。

    ⚠️ 必须传 `data_date`：盘前/休市时行情与昨日名单**同日** ⇒ 需由 `promotion` 判
       "不可评估"，否则会给出恒 100% 的假晋级率（见 `promotion.build_promotion`）。
    """
    try:
        from app import promotion
        return promotion.build_promotion(cls, data_date=data_date)
    except Exception as e:
        print(f"[rt_uplimit] promotion failed: {str(e)[:80]}")        # ASCII（铁律⑥）
        return {"available": False, "reason": "计算失败"}


def snapshot(force: bool = False) -> Dict:
    """60s 缓存包装（供接口与后台循环共用）。fail-open：异常返回 available=False。"""
    now = time.time()
    if not force:
        cached = _CACHE.get("val")
        if cached is not None and now - _CACHE["ts"] < TTL:
            return cached
    with _LOCK:               # 防并发重复拉取（接口 + 后台循环可同时到达）
        cached = _CACHE.get("val")
        if not force and cached is not None and time.time() - _CACHE["ts"] < TTL:
            return cached
        try:
            rows = fetch_all()
        except Exception as e:
            print(f"[rt_uplimit] fetch failed: {str(e)[:80]}")       # ASCII
            return cached or {"available": False, "reason": "行情源不可用"}
        if not rows:
            return cached or {"available": False, "reason": "行情源返回空"}
        # ★ 2026-09-29：区分「抓取时刻」与「数据所属交易日」—— 盘前/休市时 rt_k 返回的是
        #   **上一交易日收盘快照**，若只给 `updated_at`（抓取时刻）会被读成"实时"。
        #   `_price_date()` 是项目既有的同口径判定（交易日过 9:15 ⇒ 今天，否则最近已完成日）。
        try:
            from app.routers.market import _price_date
            data_date = _price_date()
        except Exception:
            data_date = _bj_now().strftime("%Y-%m-%d")
        val = {"available": True,
               "updated_at": _bj_now().strftime("%Y-%m-%d %H:%M:%S"),
               "data_date": data_date,          # 数据所属交易日（盘前 = 上一交易日）
               "is_intraday": data_date == _bj_now().strftime("%Y-%m-%d")}
        # ★ 2026-09-29：**一次分类、两处消费** —— `compute()`（今日统计）与 `promotion()`
        #   （晋级率：昨日涨停股今日封没封）必须同源，且**不能各抓一遍行情**
        #   （3 批 rt_k × 2 处 × 120s 循环 ⇒ 翻倍 token，撞 20 次/分上限）。
        cls = classify(rows)
        val.update(compute(rows, cls))
        # 晋级率块（失败静默：无昨日名单/快照缺失 ⇒ available=False，不影响主统计）
        val["promotion"] = _safe_promotion(cls, data_date)
        if not val["is_intraday"]:
            # 盘前/休市：数据是上一交易日收盘快照 ⇒ 明确改写口径，避免误读为"实时"
            val["note"] = (f"盘前/休市：以下为 {data_date} **收盘定稿**口径"
                           f"（与日批权威家数同源）；开盘后自动转为盘中动态口径")
        _CACHE.update(ts=time.time(), val=val)
        return val


def refresh() -> Dict:
    """强制刷新（后台循环调用）。"""
    return snapshot(force=True)
