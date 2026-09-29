"""
================================================================================
【文件作用】盘中「晋级率」——昨日涨停股今日的承接力（2026-09-29）
================================================================================

【为什么做】现有情绪看板只回答"**今天**有多少涨停/炸板"，回答不了"**昨日的涨停
  今天还活着吗**"——后者才是情绪周期最灵敏的读数：
    · 晋级率高 = 接力资金愿意接 → 情绪**延续**；
    · 昨日涨停股炸板 = 接力资金撤了 → 情绪**退潮**（比全市场炸板率更早、更准，
      因为分母是"昨日涨停股"而非全市场，筛掉了噪声）。
  用户框架里"退潮禁接力"的判据就是它。

【数据源（★ 零新增外部请求、零新增管线）】
  · 昨日名单 = `zz_daily_snapshots`（日批快照，**早已在库**）的
    `payload.review_uplimit_reason[].stocks[].code` 去重。
    ★★ 2026-09-29 实测覆盖率（vs 权威家数 `uplimit_hot.ban_info`）：
      09-18 79/78、09-21 105/104、09-22 65/64、09-23 51/51、09-24 53/52、09-28 33/33
      ⇒ **6 天全部 100%~102%**，即它就是**昨日全量涨停名单**（原以为只有题材归因口径）。
  · 今日判定 = `realtime_uplimit.classify()`（rt_k 全市场精确 `close == high_limit`）。
    ★ **不自己抓行情**：本模块是**纯函数**，输入由 `realtime_uplimit.snapshot()` 传入
      （同一个 60s 缓存、同一次 3 批 rt_k）—— 若各抓一遍会翻倍 token 消耗。

【口径（★ 三条必须与展示一起披露）】
  ① **分母 = 昨日名单中有今日数据的只数**（`promoted + broken + no_touch`）；
     昨日涨停但今日**停牌/无数据**的票**单列 `missing`、不进分母**——与
     `coach.audit.execution_consistency`"未响应不进分母"同一纪律（缺失 ≠ 0）。
  ② **快照是盘中抓的**：`zz_daily_snapshots.created_at` 实测 09-28=14:52、09-24=14:15
     （非收盘后）⇒ 昨日名单 ≈ 该时刻的名单，**收盘前最后几分钟的炸板/回封会偏差**
     （它与 `ban_info` 同源 ⇒ 自洽，但与"收盘定稿"不完全等价）。
     ⚠️ 分母必须用**名单数**而非 `ban_info` 家数（两者差 0~2 只）——分子分母同源。
  ③ 今日判定与盘中炸板率**同判据**（`_SEAL_TOL` / `_BREAK_TOL` 复用 `routers/market`）
     ⇒ 盘中是**动态**读数：炸板可能回封、涨停可能打开。

【定位】展示层（盘中温度计）。**收盘后的权威家数仍以日批 `ban_info` 为准**；
  **不进决策链**（与 `realtime_uplimit`、E2 v0 同一口径）。
================================================================================
"""

import json
import threading
from typing import Dict, List, Optional

from app.database import db

# 昨日名单进程缓存：一天只变一次 ⇒ 按快照日期缓存（不必按时间 TTL）
_UNIV_CACHE: Dict = {"key": None, "val": None}
_LOCK = threading.Lock()


def daily_universe(refresh: bool = False, before: Optional[str] = None) -> Dict:
    """**全量涨停名单** + 权威家数。

    `before`（YYYY-MM-DD）：只要**严格早于**该日的最新快照 —— 生产路径必须传当前行情
      的 `data_date`（见 `build_promotion` 的说明）：
        · 盘中 T：data_date=T ⇒ 取 T-1 ⇒ "昨日涨停股今天还活着吗" ✓ 本义；
        · 盘后 T：data_date=T ⇒ 取 T-1 ⇒ "昨日涨停股今日收盘表现" ✓（收盘定稿）；
        · 盘前 T+1：data_date=T ⇒ 取 T-1 ⇒ 与盘后同解 ✓（本就无新信息）。
      ⚠️ 不能取"最新快照"：日批在**盘中 14:52** 就写入当天快照 ⇒ 14:52 后"最新"变成当天
        ⇒ 与行情**同日自比** ⇒ 晋级率恒 100%（假信号），且尾盘 8 分钟无读数。
    返回 `{available, date, created_at, codes: [6位码], name_map, count, ban_total,
    coverage_pct, note}`；失败静默（`available=False`）。
    """
    try:
        if before:
            row = db.fetch_one(
                "SELECT date, payload, created_at FROM zz_daily_snapshots "
                "WHERE date < %s ORDER BY date DESC LIMIT 1", (str(before)[:10],))
        else:
            row = db.fetch_one(
                "SELECT date, payload, created_at FROM zz_daily_snapshots "
                "ORDER BY date DESC LIMIT 1")
    except Exception as e:
        print(f"[promotion] universe read failed: {str(e)[:80]}")     # ASCII（铁律⑥）
        return {"available": False, "reason": "读取快照失败"}
    if not row:
        return {"available": False, "reason": "无日批快照（zz_daily_snapshots 为空）"}

    key = str(row.get("date"))
    with _LOCK:
        if not refresh and _UNIV_CACHE["key"] == key and _UNIV_CACHE["val"]:
            return _UNIV_CACHE["val"]

    out = {"available": False, "date": key, "created_at": str(row.get("created_at") or ""),
           "codes": [], "name_map": {}, "count": 0, "ban_total": 0,
           "coverage_pct": None, "note": None}
    try:
        p = json.loads(row.get("payload") or "{}")
    except Exception as e:
        out["reason"] = f"payload 解析失败: {str(e)[:60]}"
        return out

    # 权威家数（同源对照，用于覆盖率自检）
    hot = p.get("uplimit_hot") or {}
    ban = (hot.get("ban_info") or {}) if isinstance(hot, dict) else {}
    try:
        out["ban_total"] = sum(int(v.get("count") or 0) for v in ban.values())
    except Exception:
        out["ban_total"] = 0

    codes, names = [], {}
    for grp in (p.get("review_uplimit_reason") or []):
        if not isinstance(grp, dict):
            continue
        for s in (grp.get("stocks") or []):
            if not isinstance(s, dict):
                continue
            c = str(s.get("code") or s.get("stock_code") or "").split(".")[0]
            if not c or c in names:
                continue
            names[c] = str(s.get("name") or "")
            codes.append(c)
    out["codes"] = codes
    out["name_map"] = names
    out["count"] = len(codes)
    out["available"] = bool(codes)
    out["coverage_pct"] = (round(len(codes) / out["ban_total"] * 100, 1)
                           if out["ban_total"] else None)
    out["note"] = ("昨日名单来自日批快照的题材归因分组（实测覆盖率 100%~102%，"
                   "即全量）；⚠️ 快照为**盘中**抓取（非收盘后）⇒ 收盘前几分钟的"
                   "炸板/回封会偏差")
    with _LOCK:
        _UNIV_CACHE.update(key=key, val=out)
    return out


def build_promotion(cls: Dict, universe: Optional[Dict] = None,
                    data_date: Optional[str] = None) -> Dict:
    """晋级率块：昨日名单 × 今日逐票分类 → 晋级 / 炸板 / 未触板（含按板幅分组）。

    `cls` = `realtime_uplimit.classify(rows)` 的结果（`{"recs": [...], ...}`）。
    「晋级」= 今日 `sealed`；「炸板」= 今日 `broken`（曾触板未封）；其余为「未触板」。
    """
    # ★ 名单必须**严格早于**行情所属交易日（`before=data_date`），否则会同日自比
    #   （日批在盘中 14:52 就写入当天快照 —— 见 `daily_universe` 注释）。
    univ = universe if universe is not None else daily_universe(before=data_date)
    if not univ.get("available"):
        return {"available": False, "reason": univ.get("reason") or "无昨日名单"}

    # ★★ 防「**同日自比**」假信号（2026-09-29 实现时发现的必查陷阱）：
    #   盘前/休市时 rt_k 返回的是**上一交易日收盘快照**（`is_intraday=false`），
    #   而"昨日名单"也来自上一交易日 ⇒ 两者**同一天** ⇒ 昨日涨停股在"今日"快照里
    #   必然全部 `sealed` ⇒ **晋级率恒为 100%**。那不是信号，是口径重合。
    #   ⇒ 直接判不可评估（宁可不给数，也不给一个恒 100% 的假读数）。
    if data_date and univ.get("date") \
            and str(data_date)[:10] == str(univ["date"])[:10]:
        return {"available": False,
                "reason": f"盘前/休市：行情与名单同为 {univ['date']}"
                          f"（同日自比无前瞻意义，开盘后自动生效）"}

    by_code: Dict[str, dict] = {}
    for rec in (cls.get("recs") or []):
        c = rec.get("code")
        if c:
            by_code[c] = rec

    promoted, broken, no_touch, missing = [], [], [], []
    groups: Dict[str, Dict[str, int]] = {}
    for c in univ["codes"]:
        rec = by_code.get(c)
        if rec is None:                       # ① 今日无数据（停牌/未上市）⇒ 单列，不进分母
            missing.append(c)
            continue
        g = groups.setdefault(rec.get("bucket") or "其他",
                              {"n": 0, "promoted": 0, "broken": 0})
        g["n"] += 1
        if rec.get("sealed"):
            promoted.append(c)
            g["promoted"] += 1
        elif rec.get("broken"):
            broken.append(c)
            g["broken"] += 1
        else:
            no_touch.append(c)

    den = len(promoted) + len(broken) + len(no_touch)
    for g in groups.values():
        g["rate"] = round(g["promoted"] / g["n"] * 100, 1) if g["n"] else None

    def _names(cs, k=8):
        return [{"code": c, "name": univ["name_map"].get(c) or ""} for c in cs[:k]]

    return {
        "available": True,
        "universe_date": univ.get("date"),
        "universe_created_at": univ.get("created_at"),
        "universe_count": univ.get("count"),
        "ban_total": univ.get("ban_total"),
        "coverage_pct": univ.get("coverage_pct"),
        "den": den,                      # 分母 = 昨日涨停且有今日数据的只数
        "promoted": len(promoted),
        "broken": len(broken),
        "no_touch": len(no_touch),
        "missing": len(missing),         # 停牌/无数据 ⇒ 单列，不进分母
        "promote_rate": round(len(promoted) / den * 100, 1) if den else None,
        "promote_break_rate": round(len(broken) / den * 100, 1) if den else None,
        "groups": groups,                # 按板幅（主板/20cm/30cm/ST5）
        "samples": {"promoted": _names(promoted), "broken": _names(broken)},
        "verdict": _verdict(den, len(promoted), len(broken)),
        "note": ("晋级 = 昨日涨停股今日**封住**；炸板 = 昨日涨停股今日曾触板未封；"
                 "未触板 = 昨日涨停股今日未摸板。分母 = 昨日涨停且有今日数据的只数"
                 "（停牌/无数据单列，不进分母）；盘中动态口径，炸板可能回封 ⇒ "
                 "收盘后以日批 ban_info 为准。不进决策链。"),
    }


def _verdict(den: int, promoted: int, broken: int) -> str:
    """规则判读（不用 LLM）——沿用用户框架「退潮禁接力」的判据链。"""
    if not den:
        return "不可评估（昨日涨停股今日均无数据）"
    r = promoted / den * 100
    br = broken / den * 100
    if r >= 50:
        return f"承接强（晋级 {r:.0f}%）⇒ 接力资金仍在，情绪延续"
    if r < 25 and br >= 10:
        return f"退潮信号（晋级 {r:.0f}%、炸板 {br:.0f}%）⇒ 按纪律禁接力"
    if r < 25:
        return f"承接弱（晋级 {r:.0f}%）⇒ 谨慎接力"
    return f"承接中性（晋级 {r:.0f}%、炸板 {br:.0f}%）"


def snapshot(cls: Optional[Dict] = None, data_date: Optional[str] = None) -> Dict:
    """供 `realtime_uplimit.snapshot()` 调用；`cls` 缺省时自行抓取（仅调试用）。

    ⚠️ 生产路径**必须**传 `cls`（复用同一次 rt_k 抓取）—— 不传会新增 3 次外部请求。
    ⚠️ `data_date` 必须传（**同日自比防护**需要它，见 `build_promotion`）。
    """
    try:
        if cls is None:
            from app import realtime_uplimit
            cls = realtime_uplimit.classify(realtime_uplimit.fetch_all())
        return build_promotion(cls, data_date=data_date)
    except Exception as e:
        print(f"[promotion] build failed: {str(e)[:80]}")            # ASCII（铁律⑥）
        return {"available": False, "reason": "计算失败"}
