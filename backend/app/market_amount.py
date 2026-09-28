"""
================================================================================
【文件作用】两市成交额「环比 + 缩量/放量定性」（U 型曲线法，2026-09-29）
================================================================================
背景（为什么需要它）：
  工作台看盘序的"两市成交额"只有**绝对值** ⇒ 用户无法回答"今天放量还是缩量"
  （看盘第一问）。而 A 股成交量是 **U 型分布**（开盘/尾盘重、午间轻）⇒
  **线性外推严重失真**（10:00 累计占 26.1%，线性外推说"全天 = 当前×1.5"，
  真实倍数 1/0.261 ≈ 3.83 倍）。
  正确做法：`预估全天 = 当前累计 / 曲线(当前时刻)`。

★★ 可靠性边界（实测，**必须据此标注，不可越界吹**）：
  曲线由 `scripts/build_amount_curve.py` 分层抽样建立（12 只 × 12 天），**留出验证**（n=72）：
    · 10:00 中位误差 **16.2%**（P90 51.6%）⇒ **上午不可用于定性**（本质限制：
      不同交易日的早盘热度差异大，"今日形状 = 历史平均"的假设在早盘最不成立）
    · 14:00 中位误差 **4.96%**（判据预注册 ≤6% ⇒ 通过）
    · 14:30 3.02% / 14:55 0.74%
  ⇒ 本模块在 **14:00 前只报"实时累计"（客观值），14:00 后才给"预估全天 + 定性"**
    （`reliable=False` 时前端不得显示缩量/放量结论）。

数据源：
  · 当日分时：腾讯 `minute/query`（**沪 sh000001 + 深 sz399001** 的累计成交额，
    共 2 次请求 ⇒ 全市场口径；**只有当日、无历史**）★ 实测字段：`"0931 价 累计量 累计额"`
  · 历史日常额：本地落库表 `market_amount_daily`（`save_daily()` 每日收盘写一行）；
    **首次**（表为空）回退 `market_tail_snapshot.amount`（14:31 累计）÷ 曲线(14:31) 反推，
    并标 `base_source="tail_estimate"` 让人知道基准是估算的。

用法：
  from app import market_amount
  market_amount.estimate()      # 实时：累计/预估全天/环比/定性
  market_amount.save_daily()    # 收盘落库（scheduler 挂）
================================================================================
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CURVE_PATH = os.path.join(ROOT, "data", "amount_curve.json")

_TX_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
_MARKETS = ("sh000001", "sz399001")     # 沪 + 深 = "两市"（北交所不计入"两市"口径）
_TTL = 60                                # 实时缓存（腾讯轻量；30s 轮询下够用）
_RELIABLE_AFTER = "1400"                 # ★ 曲线法可靠的时刻下限（见文件头"可靠性边界"）
_BAND = 10.0                             # 缩量/放量阈值(%)：经验初值，见 _verdict 注释

_cache = {"ts": 0.0, "val": None}
_curve_cache = {"mtime": 0.0, "val": None}
_lock = threading.Lock()


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def load_curve() -> Optional[dict]:
    """读曲线 JSON（按 mtime 缓存）。缺失返回 None（调用方须优雅降级）。"""
    try:
        mt = os.path.getmtime(CURVE_PATH)
    except OSError:
        return None
    if _curve_cache["val"] is not None and _curve_cache["mtime"] == mt:
        return _curve_cache["val"]
    try:
        with open(CURVE_PATH, encoding="utf-8") as f:
            v = json.load(f)
        _curve_cache.update(mtime=mt, val=v)
        return v
    except Exception as e:
        print(f"[amount] 曲线读取失败: {str(e)[:80]}")        # ASCII（铁律⑥）
        return None


def _curve_share(points: Dict[str, float], hhmm: str) -> Optional[float]:
    """某时刻的累计占比（**向前取最近的分位点**，不插值——曲线点是离散锚）。"""
    keys = sorted(points.keys())
    best = None
    for k in keys:
        if k <= hhmm:
            best = k
        else:
            break
    return points.get(best) if best else None


def _fetch_intraday() -> Dict:
    """腾讯分时（沪深累计成交额）→ {points: {HHMM: 累计元}, cum: 最新累计元, at: HHMM}。"""
    import requests
    s = requests.Session()
    s.headers.update({"user-agent": "Mozilla/5.0", "referer": "https://gu.qq.com/"})
    agg: Dict[str, float] = {}
    for mk in _MARKETS:
        r = s.get(_TX_URL, params={"code": mk}, timeout=10)
        rows = (((r.json() or {}).get("data") or {}).get(mk) or {}) \
            .get("data", {}).get("data") or []
        for line in rows:
            parts = str(line).split()
            if len(parts) < 4:
                continue
            hhmm, amt = parts[0], parts[3]
            try:
                agg[hhmm] = agg.get(hhmm, 0.0) + float(amt)
            except (TypeError, ValueError):
                continue
    if not agg:
        return {}
    at = sorted(agg.keys())[-1]
    return {"points": agg, "cum": agg[at], "at": at}


def _base_amount() -> tuple:
    """昨日（基准）全天成交额 → (元, 来源)。**只认落库表**（`save_daily` 每日收盘写入）。

    ★★ 2026-09-29 口径仲裁（为什么砍掉了原有的两条回退路径）：
      · `market_tail_snapshot.amount`（9/28 14:31 = 8962.6 亿）：用曲线反推全天得 1.04 万亿，
        而**两个权威来源**都指向 1.67~1.70 万亿（zzshare 全市场日线 9/24 = 16,691 亿；
        腾讯分时 9/28 = 17,028 亿）⇒ 该字段口径存疑，**不可作基准**（已另行记录待查）。
      · 指数日线 `amount`（上证=沪市/深证=深市）：实测 `get_kline` 对指数返回 `amount=None`
        ⇒ 拿不到。
      ⇒ 唯一可靠路径 = **本模块自建落库**（腾讯分时当日序列 → 收盘写一行）：
        首日无基准时诚实返回 0（不做环比），次日即有精确基准。
    """
    from app.database import db
    try:
        row = db.fetch_one(
            "SELECT date, amount_yi FROM market_amount_daily "
            "ORDER BY date DESC LIMIT 1")
        if row and row.get("amount_yi"):
            return float(row["amount_yi"]) * 1e8, f"db:{str(row.get('date'))[:10]}"
    except Exception:
        pass
    return 0.0, "none"


def _verdict(ratio_pct: Optional[float]) -> Optional[str]:
    """环比 → 定性。★ 阈值 ±10% 是**经验初值**（未回测；A 股两市日成交额波动本身
    较大）⇒ 前端须与"预估误差（14:00 中位 5%）"一并展示，不可当作精确判据。"""
    if ratio_pct is None:
        return None
    if ratio_pct <= -_BAND:
        return "缩量"
    if ratio_pct >= _BAND:
        return "放量"
    return "温和"


def estimate(force: bool = False) -> Dict:
    """实时：当前累计 / 预估全天 / 环比昨日 / 定性。fail-open（失败返回 available=False）。"""
    now = time.time()
    if not force and _cache["val"] is not None and now - _cache["ts"] < _TTL:
        return _cache["val"]
    with _lock:
        if not force and _cache["val"] is not None and time.time() - _cache["ts"] < _TTL:
            return _cache["val"]
        out = {"available": False, "reason": ""}
        try:
            d = _fetch_intraday()
        except Exception as e:
            print(f"[amount] 分时抓取失败: {str(e)[:80]}")     # ASCII（铁律⑥）
            return _cache["val"] or {"available": False, "reason": "分时源不可用"}
        if not d:
            return _cache["val"] or {"available": False, "reason": "分时源返回空"}
        cur = load_curve() or {}
        pts = cur.get("points") or {}
        share = _curve_share(pts, d["at"])
        base, base_src = _base_amount()
        cum_yi = round(d["cum"] / 1e8, 0)
        today = _bj_now().strftime("%Y-%m-%d")
        # ★★ 数据所属交易日（对齐项目 `_price_date()` 口径）：
        #   交易日且已过 9:30 ⇒ **今日**（盘中动态 / 盘后即今日收盘定稿，腾讯分时给的就是今天）；
        #   否则（盘前/休市）⇒ **最近已完成交易日**（腾讯分时给的是上一交易日全天序列）。
        #   ⚠️ 只用 `_is_trading_hours()` 判会把"盘后"错标成"上一交易日"——盘后曲线占比
        #      ≈1.0 会让"预估全天"恰好等于累计（结果对），但**日期标注会骗人**。
        try:
            from app.flash.rules import is_trading_day, latest_completed_trading_day
            _now = _bj_now()
            data_date = (today if (is_trading_day(_now) and (_now.hour, _now.minute) >= (9, 30))
                         else str(latest_completed_trading_day())[:10])
        except Exception:
            data_date = today
        try:
            from app.tencent import _is_trading_hours
            intraday = bool(_is_trading_hours())
        except Exception:
            intraday = False
        out = {
            "available": True,
            "date": today,
            "data_date": data_date,                # 数据所属交易日（盘前=上一交易日）
            "is_intraday": intraday,
            "as_of": d["at"],                      # 数据时刻（HHMM）
            "cum_yi": cum_yi,                      # 实时累计（客观值，任何时刻都对）
            "cum_share_pct": (round(share * 100, 1) if share else None),
            "reliable": bool(share and intraday and d["at"] >= _RELIABLE_AFTER),
            "curve_built_at": cur.get("built_at"),
        }
        if share and share > 0:
            est = d["cum"] / share
            out["full_day_est_yi"] = round(est / 1e8, 0)
            if base > 0:
                ratio = (est / base - 1) * 100
                out["base_yi"] = round(base / 1e8, 0)
                out["base_source"] = base_src
                out["ratio_pct"] = round(ratio, 1)
                out["verdict"] = _verdict(ratio)
                # ★ 何时可以展示"环比+定性"：盘中须过可靠时刻；非盘中（定稿）本来就是
                #   实测全天额 ⇒ 直接可比（此时 `share≈1`，`full_day_est` 即实际值）。
                out["show_ratio"] = bool(out["reliable"] or not intraday)
        out["note"] = (
            f"预估法：曲线法（`data/amount_curve.json`，分层抽样留出验证：14:00 中位误差 5%、"
            f"10:00 达 16%）。**{_RELIABLE_AFTER[:2]}:{_RELIABLE_AFTER[2:]} 前不做缩量/放量定性**"
            f"（早盘形状假设不成立）。当前时刻 {d['at'][:2]}:{d['at'][2:]}"
            f"{'（已可靠）' if out['reliable'] else '（未到可靠区间，仅看累计）'}。"
            f"基准来源 {base_src}；缩量/放量阈值为经验初值 ±{_BAND:.0f}%。")
        if not intraday:
            # ★ 必须放在**最后**：此前提前 return 被删（盘后也要算环比），若在中间赋值
            #   会被上面的 note 覆盖 ⇒ 丢失"这其实是定稿而非实时"的关键标注。
            out["note"] = (f"非交易时段 ⇒ 当前为 **{data_date} 收盘定稿**"
                           + ("（今日已收盘）" if data_date == today else "（上一交易日）")
                           + "。" + out["note"])
        _cache.update(ts=time.time(), val=out)
        return out


def save_daily(date: Optional[str] = None) -> Dict:
    """收盘落库当日全市场成交额（+ 分时序列），供次日做**精确**同时刻对比。

    ★ 为什么必须落库：腾讯分时**只有当日** ⇒ 不落库 = 明天永远只能靠曲线"估算"昨日。
      落库后，未来可用**真实分时序列**替代曲线（精度更高），是本模块的长期正确路径。
    """
    from app.database import db
    now = _bj_now()
    if date is None:
        # ★ 安全保护：腾讯分时**不带日期**（盘前返回的是上一交易日序列）⇒ 若在盘前落库，
        #   会把昨天的数据写到今天名下（静默错位）。故只允许**收盘定稿窗口**内自动落库。
        if not (now.hour == 15 and now.minute >= 55) and now.hour not in (16, 17):
            return {"ok": False, "error": "非收盘落库窗口（15:55-17:59）；补录请显式传 date"}
        date = now.strftime("%Y-%m-%d")
    day = date
    try:
        d = _fetch_intraday()
        if not d:
            return {"ok": False, "error": "分时源无数据"}
        db.execute(
            "CREATE TABLE IF NOT EXISTS market_amount_daily ("
            "date TEXT PRIMARY KEY, amount_yi TEXT, cum_share_pct TEXT,"
            "series_json TEXT, created_at TEXT)")
        pts = {k: round(v / 1e8, 1) for k, v in d["points"].items()}
        # ★ 用 `db.upsert`（项目双库封装）而非手写 ON CONFLICT（SQLite 老版本不支持）
        db.upsert("market_amount_daily", {
            "date": day,
            "amount_yi": str(round(d["cum"] / 1e8, 0)),
            "cum_share_pct": "",
            "series_json": json.dumps(pts, ensure_ascii=False),
            "created_at": _bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        }, conflict_columns=["date"])
        return {"ok": True, "date": day, "amount_yi": round(d["cum"] / 1e8, 0),
                "points_n": len(pts), "at": d["at"]}
    except Exception as e:
        print(f"[amount] 落库失败: {str(e)[:100]}")             # ASCII（铁律⑥）
        return {"ok": False, "error": str(e)[:100]}
