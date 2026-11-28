# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】题材轮动 —— 区间排名 + 轮动信号 + 可验证性检验（2026-11-28）
================================================================================
背景（用户 2026-11-28）："zzshare 有个题材轮动的功能，项目需要加入这个大功能"。

★ 数据源实测（2026-11-28，改动前必读）
  `plates_rank_days_new(plate_type, date2, n_days, prev_days, limit)` 返回：
    {plate_code, plate_name, sum_rate, sum_leader_money, sum_score, days, is_new}
  · `n_days`   = **累计天数**（1=当日涨幅，5=5日累计，20=20日累计）
  · `is_new`   = **相对 prev_days 天前是否新进榜**（轮动标记）
  · 回溯能力：2023-06-30 / 2024-06-28 / 2025-06-30 / 2026-11-27 **都能返回 100 条**
    （比行业板块的 77~82 条更完整，说明题材池的历史覆盖更好）
  · `plate_type`：17=题材 / 15=概念 / 14=行业
  ⇒ **历史可回补 ⇒ 轮动信号可以立即验证，不必等三个月积累。**

★ 为什么"轮动"不等于"排名"
  排名回答"谁涨得多"，轮动回答"**钱从哪流到哪**"。真正的轮动信号是**变化量**：
    ① `is_new`                  —— 新进榜（源端直接给）
    ② `rank` 的日变化           —— 跃升 / 掉队（本模块自己算）
    ③ `sum_rate(5日) - sum_rate(20日)/4` —— **加速度**（近期是否在加速）
    ④ `sum_leader_money`        —— 累计龙头资金（钱是不是真的进来了）
  只展示排名、不看变化，等于把"轮动"做成了"涨幅榜"。

★ 本模块**必须回答的问题**（不回答就等于讲故事）
  `is_new=True` 的题材，次日/3 日后的超额收益是否显著高于全体均值？
  ⇒ `verify()` 用回补的历史做事件研究；**结果会写进返回值的 `valid` 字段**。
  参考 11-28 产业链的教训：拿不出证据的东西不许包装成信号。

★ 红线
  · 只陈述事实（谁进榜、谁掉队、资金多少），**不给买卖建议**
  · 不进评分、不进闸门、不碰模拟盘
================================================================================
"""
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.database import db

_TABLE = "topic_rotation"
_PT = {"topic": 17, "concept": 15, "industry": 14}
_PT_CN = {17: "题材", 15: "概念", 14: "行业"}

_READY = False


def ensure_table() -> None:
    """建表（幂等）。主键含 `n_days`：同一天要存多档累计口径（1/5/20 日）。"""
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT NOT NULL,
            plate_type INTEGER NOT NULL,
            plate_code TEXT NOT NULL,
            plate_name TEXT,
            n_days INTEGER NOT NULL,
            rank INTEGER,
            sum_rate REAL,
            sum_score REAL,
            sum_leader_money REAL,
            is_new INTEGER,
            created_at TEXT,
            PRIMARY KEY (date, plate_type, plate_code, n_days)
        )
    """)
    _READY = True


# ================================================================
#  一、取数 + 落库
# ================================================================
def fetch(day: str, n_days: int = 5, plate_type: int = 17,
          limit: int = 100, prev_days: int = 3) -> List[Dict]:
    """拉某日の板块区间排名（原样返回，失败返回 []）。"""
    from app.zzshare_client import get_api
    try:
        rows = get_api().plates_rank_days_new(
            plate_type=plate_type, date2=day, n_days=n_days,
            prev_days=prev_days, limit=limit) or []
    except Exception as e:
        print(f"[topic_rot] fetch failed {day} n={n_days}: {str(e)[:80]}")
        return []
    out = []
    for i, r in enumerate(rows, 1):
        code = str(r.get("plate_code") or "").strip()
        if not code:
            continue
        out.append({
            "plate_code": code,
            "plate_name": str(r.get("plate_name") or ""),
            "rank": i,
            "sum_rate": _f(r.get("sum_rate")),
            "sum_score": _f(r.get("sum_score")),
            "sum_leader_money": _f(r.get("sum_leader_money")),
            "is_new": 1 if r.get("is_new") else 0,
        })
    return out


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def save(day: str, n_days_list: Tuple[int, ...] = (1, 5, 20),
         plate_type: int = 17, limit: int = 100) -> Dict:
    """落库某日的多档累计口径（幂等 upsert）。返回 `{ok, day, written, kinds}`。

    ⚠️ 必须同时存 `n_days=1` —— 它是"当日涨幅"，`verify()` 算次日收益全靠它。
       只存 5 日累计的话，事后无法还原单日收益，验证就做不了。
    """
    ensure_table()
    from app.flash.rules import beijing_now as _bj
    now = _bj().strftime("%Y-%m-%d %H:%M:%S")
    written = 0
    kinds = {}
    for nd in n_days_list:
        rows = fetch(day, n_days=nd, plate_type=plate_type, limit=limit)
        if not rows:
            kinds[str(nd)] = 0
            continue
        batch = [{**r, "date": day, "plate_type": plate_type,
                  "n_days": nd, "created_at": now} for r in rows]
        try:
            n = db.upsert_many(_TABLE, batch,
                               conflict_columns=["date", "plate_type",
                                                 "plate_code", "n_days"],
                               page_size=200)
            written += int(n or 0)
        except Exception as e:
            print(f"[topic_rot] write failed {day} n={nd}: {str(e)[:80]}")
            kinds[str(nd)] = 0
            continue
        kinds[str(nd)] = len(rows)
    return {"ok": bool(written), "day": day, "written": written, "kinds": kinds}


def latest_day(plate_type: int = 17, n_days: int = 5) -> Optional[str]:
    ensure_table()
    try:
        r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE} "
                         f"WHERE plate_type=%s AND n_days=%s",
                         (plate_type, n_days)) or {}
        return str(r.get("d"))[:10] if r.get("d") else None
    except Exception:
        return None


def backfill(start: str, end: str = None, n_days_list: Tuple[int, ...] = (1, 5, 20),
             plate_type: int = 17, sleep_sec: float = 0.6,
             skip_existing: bool = True, max_days: int = 0) -> Dict:
    """逐日回补题材轮动历史（幂等、可中断、默认跳过已有日期）。

    成本：每天 `len(n_days_list)` 次请求。默认 3 档 × 0.6s ≈ 2 秒/天。
    回补 2 年（约 500 个交易日）≈ 17 分钟。
    """
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from app.flash.rules import is_trading_day, latest_completed_trading_day

    t0 = time.time()
    s = str(start or "")[:10]
    e = str(end or "").strip()[:10] or str(latest_completed_trading_day())[:10]
    try:
        d0 = _dt.strptime(s, "%Y-%m-%d")
        d1 = _dt.strptime(e, "%Y-%m-%d")
    except (TypeError, ValueError):
        return {"ok": False, "error": "start/end 需为 YYYY-MM-DD"}
    if d0 > d1:
        return {"ok": False, "error": "start 晚于 end"}

    # ★ 2026-11-28：断点续传按 **(日期, 累计档)** 判断，不是只看日期 ——
    #   否则"先补了 1/5 档、后来想补 20 档"会被全部跳过（实测踩到）。
    have = set()
    if skip_existing:
        try:
            ensure_table()
            rows = db.fetch(f"SELECT DISTINCT date, n_days FROM {_TABLE} "
                            f"WHERE plate_type=%s", (plate_type,)) or []
            for r in rows:
                if r.get("date") is not None and r.get("n_days") is not None:
                    have.add((str(r.get("date"))[:10], int(r.get("n_days"))))
        except Exception as ex:
            print(f"[topic_rot] 已存在日期读取失败: {str(ex)[:60]}")

    todo = []
    cur = d0
    while cur <= d1:
        day = cur.strftime("%Y-%m-%d")
        try:
            ok = bool(is_trading_day(cur))
        except Exception:
            ok = cur.weekday() < 5
        if ok and not all((day, nd) in have for nd in n_days_list):
            todo.append(day)
        cur += _td(days=1)
    if max_days and len(todo) > max_days:
        todo = todo[:max_days]

    res = {"ok": True, "start": s, "end": e, "n_days": len(todo),
           "done": 0, "written": 0, "empty": [], "failed": [], "cost_sec": 0.0}
    if not todo:
        res["cost_sec"] = round(time.time() - t0, 1)
        return res

    for i, day in enumerate(todo, 1):
        try:
            r = save(day, n_days_list=n_days_list, plate_type=plate_type)
        except Exception as ex:
            res["failed"].append(f"{day}: {str(ex)[:60]}")
            r = None
        if r is None:
            continue
        if r.get("written"):
            res["written"] += int(r["written"])
            res["done"] += 1
        else:
            res["empty"].append(day)
        if i % 20 == 0 or i == len(todo):
            print(f"[topic_rot] 回补 {i}/{len(todo)} | 已写 {res['written']} 行 "
                  f"| 空 {len(res['empty'])} | 失败 {len(res['failed'])}")
        time.sleep(max(0.0, sleep_sec))
    res["cost_sec"] = round(time.time() - t0, 1)
    return res


# ================================================================
#  二、轮动视图（今日 vs 前一日：谁进、谁退、谁加速）
# ================================================================
def _rows(day: str, n_days: int, plate_type: int) -> Dict[str, Dict]:
    ensure_table()
    try:
        rows = db.fetch(f"SELECT * FROM {_TABLE} WHERE date=%s AND plate_type=%s "
                        f"AND n_days=%s", (day, plate_type, n_days)) or []
    except Exception:
        return {}
    return {str(r.get("plate_code")): dict(r) for r in rows}


def _prev_day(day: str, plate_type: int, n_days: int) -> Optional[str]:
    ensure_table()
    try:
        r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE} "
                         f"WHERE plate_type=%s AND n_days=%s AND date<%s",
                         (plate_type, n_days, day)) or {}
        return str(r.get("d"))[:10] if r.get("d") else None
    except Exception:
        return None


def view(day: str = None, n_days: int = 5, plate_type: int = 17,
         top: int = 15) -> Dict:
    """轮动视图：**变化量**而非排名本身。

    返回 `{ok, date, prev, top, new_in, surge, drop, accel, note}`
      top    当期前 N（含累计涨幅/强度/资金）
      new_in 源端标记新进榜的（`is_new=1`）
      surge  排名跃升最多（对比前一交易日）
      drop   排名下滑最多（资金撤离迹象）
      accel  5日 与 20日 的差距（越大 = 近期越在加速；负 = 前期涨、现在歇）
    """
    ensure_table()
    d = str(day or "")[:10] or (latest_day(plate_type, n_days) or "")
    if not d:
        return {"ok": False, "error": "无数据（先跑 backfill 或等日批落库）"}
    cur = _rows(d, n_days, plate_type)
    if not cur:
        return {"ok": False, "error": f"{d} 无 {_PT_CN.get(plate_type)}数据"}
    prev = _prev_day(d, plate_type, n_days)
    pv = _rows(prev, n_days, plate_type) if prev else {}
    # 加速度要用 20 日口径
    long_map = _rows(d, 20, plate_type) if n_days != 20 else {}

    def _item(r: Dict, extra: Dict = None) -> Dict:
        o = {"code": r.get("plate_code"), "name": r.get("plate_name"),
             "rank": r.get("rank"), "sum_rate": r.get("sum_rate"),
             "sum_score": r.get("sum_score"),
             "money_yi": (round((_f(r.get("sum_leader_money")) or 0) / 1e8, 2)
                          if r.get("sum_leader_money") is not None else None),
             "is_new": bool(r.get("is_new"))}
        if extra:
            o.update(extra)
        return o

    items = sorted(cur.values(), key=lambda x: (x.get("rank") or 9999))
    top_rows = [_item(r) for r in items[:top]]
    new_in = [_item(r) for r in items if r.get("is_new")]
    surge, drop = [], []
    if pv:
        for code, r in cur.items():
            p = pv.get(code)
            if not p:
                continue
            dr = (p.get("rank") or 0) - (r.get("rank") or 0)   # 正 = 名次上升
            if dr >= 3:
                surge.append(_item(r, {"rank_change": dr,
                                       "prev_rank": p.get("rank")}))
            elif dr <= -3:
                drop.append(_item(r, {"rank_change": dr,
                                      "prev_rank": p.get("rank")}))
        surge.sort(key=lambda x: -x["rank_change"])
        drop.sort(key=lambda x: x["rank_change"])
    accel = []
    for code, r in cur.items():
        lg = long_map.get(code)
        if not lg:
            continue
        short = _f(r.get("sum_rate"))
        long_v = _f(lg.get("sum_rate"))
        if short is None or long_v is None:
            continue
        # 5 日与 20 日的日均差：>0 表示近 5 日比近 20 日更猛
        a = short - long_v / 4.0
        accel.append(_item(r, {"accel": round(a, 2),
                               "sum_rate_20d": long_v}))
    accel.sort(key=lambda x: -(x.get("accel") or 0))
    return {
        "ok": True, "date": d, "prev_date": prev,
        "plate_type": plate_type, "plate_type_cn": _PT_CN.get(plate_type),
        "n_days": n_days,
        "top": top_rows,
        "new_in": new_in[:top],
        "surge": surge[:top],
        "drop": drop[:top],
        "accel": accel[:top],
        "drop_accel": accel[-min(top, len(accel)):][::-1] if accel else [],
        "counts": {"total": len(cur), "new_in": len(new_in),
                   "surge": len(surge), "drop": len(drop)},
        "note": ("轮动看的是**变化量**（新进/跃升/掉队/加速度），不是涨幅榜本身。"
                 "只陈述事实，不构成投资建议；信号有效性见 /topic-rotation/verify。"),
    }


def ranks_history(days: int = 8, top: int = 20, plate_type: int = 17,
                  n_days: int = 5) -> Dict:
    """最近 `days` 个交易日的**排名序列**（供 ECharts 排名流图用）。

    返回 `{ok, dates, items:[{code,name,ranks:[...],last}]}`
      · `dates` 正序（旧 → 新），与每条的 `ranks` 一一对应
      · 只保留「最近一天进过 top N」或「期间曾进过 top N」的题材，
        否则 100 条全画出来是一团乱麻（图的目的是看**换人**，不是看全名单）
      · `ranks` 里为 None 表示那天不在榜（掉出去了）
    """
    ensure_table()
    try:
        ds = [str(r.get("date"))[:10] for r in
              (db.fetch(f"SELECT DISTINCT date FROM {_TABLE} WHERE plate_type=%s "
                        f"AND n_days=%s ORDER BY date DESC LIMIT %s",
                        (plate_type, n_days, days)) or [])
              if r.get("date")]
    except Exception as e:
        return {"ok": False, "error": f"读取日期失败: {str(e)[:80]}"}
    if not ds:
        return {"ok": False, "error": "无数据"}
    ds = sorted(ds)
    per_day = {d: _rows(d, n_days, plate_type) for d in ds}
    latest = per_day.get(ds[-1]) or {}
    keep = {c for c, r in latest.items() if (r.get("rank") or 999) <= top}
    for d in ds:
        for c, r in (per_day.get(d) or {}).items():
            if (r.get("rank") or 999) <= top:
                keep.add(c)
    name_of = {}
    for d in ds:
        for c, r in (per_day.get(d) or {}).items():
            if c in keep and r.get("plate_name"):
                name_of[c] = r["plate_name"]
    items = []
    for c in keep:
        ranks, rates = [], []
        for d in ds:
            r = (per_day.get(d) or {}).get(c)
            # 不在榜 = 掉出（用 None，不填 999 —— 图上要断开而不是画到地板）
            ranks.append((r.get("rank") if r else None))
            rates.append((_f(r.get("sum_rate")) if r else None))
        last = ranks[-1]
        # 只画"最新仍在 top"或"期间曾冲到前 10"的，避免几十条无意义的线
        if last is None and all((x or 999) > 10 for x in ranks):
            continue
        items.append({"code": c, "name": name_of.get(c) or c,
                      "ranks": ranks, "rates": rates, "last": last})
    items.sort(key=lambda x: (x["last"] if x["last"] else 999))
    return {"ok": True, "dates": ds, "items": items[:top * 2],
            "plate_type": plate_type, "n_days": n_days,
            "note": "排名为 5 日累计口径下的位次；null = 当日不在榜。"}


# ★ 成分股名单进程缓存：变动极慢（按季度调仓），且每次要外呼，
#   与 `sector_zz._SCAN_CACHE` 同口径（一次取一份，TTL 1 天）。
_MEMBERS_CACHE: Dict[Tuple[int, str], Tuple[float, List[Dict]]] = {}
_MEMBERS_TTL = 24 * 3600.0


def _fetch_members(plate_type: int, plate_code: str) -> List[Dict]:
    """某板块的成分股名单（`[{code, name}]`），进程缓存 1 天，失败返回 []。"""
    key = (plate_type, plate_code)
    now = time.time()
    hit = _MEMBERS_CACHE.get(key)
    if hit and now - hit[0] < _MEMBERS_TTL:
        return hit[1]
    from app.zzshare_client import get_api
    try:
        rows = get_api().plates_stocks(plate_type=plate_type,
                                       plate_code=plate_code) or []
    except Exception as e:
        print(f"[topic_rot] members failed {plate_code}: {str(e)[:80]}")
        return []
    out = []
    for r in rows:
        c = str(r.get("stock_code") or "").strip()
        if c:
            out.append({"code": c, "name": str(r.get("stock_name") or "")})
    _MEMBERS_CACHE[key] = (now, out)
    return out


def constituents(plate_code: str, plate_type: int = 17, day: str = None,
                 top: int = 12) -> Dict:
    """题材/板块 → **相关个股**（按当日涨跌幅降序取前 `top` 只）。

    数据来源：
      · 名单 ← zzshare `plates_stocks`（外呼一次，进程缓存 1 天）
      · 涨跌/5日/评分 ← `stock_facts_wide`（日批产物，零外呼）
    ⚠️ `plates_stocks` **只给名单、不给行情** ⇒ 必须自己补涨跌幅，
       否则页面只能列出一堆没有涨跌的代码，等于没用（实测确认）。
    """
    members = _fetch_members(plate_type, plate_code)
    if not members:
        return {"ok": False, "error": "成分股为空（外呼失败或该板块无成分）"}
    codes = [m["code"] for m in members]
    d = str(day or "")[:10]
    if not d:
        try:
            r = db.fetch_one("SELECT MAX(date) AS d FROM stock_facts_wide") or {}
            d = str(r.get("d"))[:10]
        except Exception:
            d = ""
    info: Dict[str, Dict] = {}
    for i in range(0, len(codes), 500):        # 分批 IN，避免超长 SQL
        chunk = codes[i:i + 500]
        ph = ",".join(["%s"] * len(chunk))
        try:
            rows = db.fetch(
                f"SELECT code, name, chg_pct, ret5, ret20, score_total, phase "
                f"FROM stock_facts_wide WHERE date=%s AND code IN ({ph})",
                tuple([d] + chunk)) or []
        except Exception as e:
            print(f"[topic_rot] facts failed: {str(e)[:80]}")
            rows = []
        for r in rows:
            info[str(r.get("code"))] = r
    out = []
    for m in members:
        r = info.get(m["code"])
        out.append({
            "code": m["code"],
            "name": m["name"] or (r or {}).get("name") or m["code"],
            "chg_pct": _f((r or {}).get("chg_pct")),
            "ret5": _f((r or {}).get("ret5")),
            "ret20": _f((r or {}).get("ret20")),
            "score": _f((r or {}).get("score_total")),
            "phase": (r or {}).get("phase"),
        })
    # 有行情的排前面（按当日涨跌降序），无行情的按名字兜底放最后
    withp = [x for x in out if x["chg_pct"] is not None]
    without = [x for x in out if x["chg_pct"] is None]
    withp.sort(key=lambda x: -x["chg_pct"])
    return {
        "ok": True, "plate_code": plate_code, "date": d,
        "n_total": len(out),
        "n_with_quote": len(withp),
        "items": (withp + without)[:max(1, int(top))],
        "note": f"该板块共 {len(out)} 只成分，按 {d} 涨跌幅降序取前 {top} 只；"
                f"其中 {len(withp)} 只有当日行情（缺行情的不填 0，排在最后）。",
    }


# ================================================================
#  二·五、细分题材库（zzshare `topic_table`）—— 与板块榜**互补**，不强行匹配
# ================================================================
# ★★ 2026-11-28 实测：`topic_table` 的 140 个题材名 与 `plates_rank` 的板块名
#   只重合 **9 个 / 140**（"光通信/光模块/光纤" vs "光通信"、"华为昇腾/超节点" 等）。
#   前者是**细分题材+日期后缀**（更早、更锐），后者是**宽口径板块**（更晚、更钝）。
#   ⇒ **不强行做名称匹配**（那会产出大量错误关联），改为两块并列展示：
#       · 上半部分 = 板块级轮动（谁在涨、名次怎么变）
#       · 下半部分 = 细分题材库（哪个具体逻辑在驱动 + 官方阶段 + 相关个股）
#   zzshare 自带 `stage_tag` / `continuity_state` / `stage_confidence`
#   （算法版本 `topic_stage_v2`）—— 比本模块的 `stage()` 规则成熟，**直接采用官方判定**。
_LIB_CACHE: Dict[str, Dict] = {"ts": 0.0, "val": None}
_LIB_TTL = 600.0           # 10 分钟：题材库变动慢
_DETAIL_CACHE: Dict[str, Tuple[float, Dict]] = {}
_DETAIL_TTL = 6 * 3600.0   # 详情（含个股 rows）6 小时


def topic_lib(max_items: int = 200) -> Dict:
    """细分题材库（含官方阶段与驱动逻辑摘要），进程缓存 10 分钟。

    返回 `{ok, n, items:[{unique_key, name, stage_tag, continuity_state,
                          confidence, today_pct, stock_count, brief}], note}`
    """
    now = time.time()
    if _LIB_CACHE["val"] is not None and now - _LIB_CACHE["ts"] < _LIB_TTL:
        return _LIB_CACHE["val"]
    from app.zzshare_client import get_api
    api = get_api()
    items, total = [], 0
    try:
        for pg in (1, 2, 3):
            r = api.topic_table_list(page=pg, limit=100, brief=False) or {}
            if isinstance(r, dict):
                total = int(r.get("total") or 0)
                rows = r.get("items") or []
            else:
                rows = r or []
            if not rows:
                break
            items.extend(rows)
            if len(items) >= total:
                break
    except Exception as e:
        print(f"[topic_rot] topic_lib failed: {str(e)[:80]}")
        return {"ok": False, "error": "题材库拉取失败", "items": []}
    out = []
    for it in items[:max_items]:
        content = str(it.get("content") or "")
        out.append({
            "unique_key": str(it.get("unique_key") or ""),
            "name": str(it.get("name") or ""),
            "stage_tag": str(it.get("stage_tag") or ""),
            "continuity_state": str(it.get("continuity_state") or ""),
            "confidence": it.get("stage_confidence"),
            "today_pct": _f(it.get("today_pct")),
            "stock_count": it.get("stock_count"),
            "total_count": it.get("total_count"),
            "brief": content.replace("\n", " ")[:110],
        })
    # 排序：有涨幅的按涨幅降序，无的排后
    out.sort(key=lambda x: (x["today_pct"] is None, -(x["today_pct"] or 0)))
    val = {"ok": True, "n": len(out), "total": total, "items": out,
           "note": "zzshare 细分题材库（自 stage_tag/continuity_state/confidence，"
                   "算法 topic_stage_v2）。与上方板块榜是**两套口径**，不做名称匹配。"}
    _LIB_CACHE.update({"ts": now, "val": val})
    return val


def topic_detail(unique_key: str) -> Dict:
    """单个细分题材的详情：驱动逻辑全文 + `rows`（个股 + 相关性说明）。

    ⚠️ `topic_table_detail` 的 `tid` **必须是 `unique_key`**（传数字 id 会报
       `API Error: None`，实测确认）。
    """
    uk = str(unique_key or "").strip()
    if not uk:
        return {"ok": False, "error": "unique_key 为空"}
    now = time.time()
    hit = _DETAIL_CACHE.get(uk)
    if hit and now - hit[0] < _DETAIL_TTL:
        return hit[1]
    from app.zzshare_client import get_api
    try:
        d = get_api().topic_table_detail(tid=uk)
    except Exception as e:
        return {"ok": False, "error": f"拉取失败: {str(e)[:80]}"}
    if not isinstance(d, dict):
        return {"ok": False, "error": "返回格式异常"}
    rows = d.get("rows") or []
    clean = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        clean.append({
            "l1": str(r.get("一级大类") or ""),
            "l2": str(r.get("二级小类") or ""),
            "l3": str(r.get("三级细分") or ""),
            "stock": str(r.get("个股") or ""),
            "rel": str(r.get("相关性") or ""),
        })
    val = {
        "ok": True,
        "name": str(d.get("name") or ""),
        "content": str(d.get("content") or ""),
        "today_pct": _f(d.get("today_pct")),
        "rows": clean[:80],
        "n_rows": len(clean),
        "note": "`相关性` 是 zzshare 给出的「该公司与该题材的关系」原文，"
                "不是本系统判断；个股顺序为原始顺序（未按涨跌排）。",
    }
    _DETAIL_CACHE[uk] = (now, val)
    return val


# ================================================================
#  三、可验证性（`is_new` 到底有没有预测力）
# ================================================================
def _regime(day: str) -> Dict:
    """当日市场环境（用 104 个行业板块的横截面判定）。

    为什么不用指数涨跌：题材轮动关心的是"**钱在不在场内**"，
      而"多少板块在涨"（广度）比指数点位更能反映人气 ——
      指数被权重股拉红、但 80% 板块在跌的日子，做题材是亏钱的。
    返回 `{label, avg_pct, up_ratio, n, level}`：
      level = +1 强势 / 0 中性 / -1 弱势
    """
    try:
        rows = db.fetch("SELECT change_pct FROM plate_daily_zz "
                        "WHERE date=%s AND kind='industry'", (day,)) or []
    except Exception:
        return {"label": "未知", "avg_pct": None, "up_ratio": None,
                "n": 0, "level": 0}
    vals = [_f(r.get("change_pct")) for r in rows]
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"label": "未知", "avg_pct": None, "up_ratio": None,
                "n": 0, "level": 0}
    avg = sum(vals) / len(vals)
    up = sum(1 for v in vals if v > 0) / len(vals)
    if avg >= 0.5 and up >= 0.6:
        return {"label": "强势", "avg_pct": round(avg, 2),
                "up_ratio": round(up, 3), "n": len(vals), "level": 1}
    if avg <= -0.5 or up <= 0.35:
        return {"label": "弱势", "avg_pct": round(avg, 2),
                "up_ratio": round(up, 3), "n": len(vals), "level": -1}
    return {"label": "中性", "avg_pct": round(avg, 2),
            "up_ratio": round(up, 3), "n": len(vals), "level": 0}


def stage(day: str, plate_type: int = 17) -> Dict[str, List[Dict]]:
    """**阶段判定** —— 页面"方向标"的核心（回答"这个题材走到哪一步了"）。

    四档规则（全部基于可观测的排名/加速度/新进，不含预测）：
      emerging 启动  ：is_new=1 且 accel>0 且 rank<=30（刚进榜、在加速、名次还不高）
      leading  主升  ：rank<=10 且 accel>0 且 非新进（榜单前列且还在加速）
      crowded  高位  ：rank<=10 且 accel<=0（前排但加速度转负 ⇒ 可能见顶）
      fading   退潮  ：rank 较前一日跌 >=10 位，或 accel<=-5 且 rank>30
    ⚠️ 名称是**状态描述**，不是买卖建议。`crowded` 不等于"要跌"，
       只是"已经涨到前排且不再加速"，这类位置风险收益比通常变差。
    """
    v = view(day, n_days=5, plate_type=plate_type, top=100)
    if not v.get("ok"):
        return {"emerging": [], "leading": [], "crowded": [], "fading": []}
    accel_map = {x["code"]: x.get("accel") for x in (v.get("accel") or [])}
    accel_map.update({x["code"]: x.get("accel")
                      for x in (v.get("drop_accel") or [])})
    rank_chg = {}
    for x in (v.get("surge") or []) + (v.get("drop") or []):
        rank_chg[x["code"]] = x.get("rank_change")
    out = {"emerging": [], "leading": [], "crowded": [], "fading": []}
    for x in (v.get("top") or []):
        code = x.get("code")
        rank = x.get("rank") or 999
        ac = accel_map.get(code)
        rc = rank_chg.get(code)
        item = {**x, "accel": ac, "rank_change": rc,
                "sum_rate_20d": next((a.get("sum_rate_20d")
                                      for a in (v.get("accel") or [])
                                      if a["code"] == code), None)}
        # ⚠️ 缺 20 日档（`n_days=20` 没回补）时 `ac` 为 None ⇒ **不判阶段**，
        #   而不是当成 0 塞进 `crowded`（那会把"数据缺失"显示成"涨到顶了"）。
        if rc is not None and rc <= -10:
            out["fading"].append(item)
        elif ac is not None and ac <= -5 and rank > 30:
            out["fading"].append(item)
        elif ac is None:
            continue
        elif x.get("is_new") and ac > 0 and rank <= 30:
            out["emerging"].append(item)
        elif rank <= 10 and ac > 0 and not x.get("is_new"):
            out["leading"].append(item)
        elif rank <= 10 and ac <= 0:
            out["crowded"].append(item)
    for k in out:
        out[k] = out[k][:15]
    return out


def dashboard(day: str = None, plate_type: int = 17) -> Dict:
    """页面主接口：环境 + 统计 + 阶段 + 榜单，一次给全。

    ★ 2026-11-28：`top` 从 20 提到 **100**（全部在榜），因为前端要支持
      「涨幅 / 资金 / 加速度」三种排序切换 —— 只给前 20 的话，
      按资金排序时会出现"资金最多但涨幅排第 30"的题材不在列表里，切了也看不到。
    """
    v = view(day, n_days=5, plate_type=plate_type, top=100)
    if not v.get("ok"):
        return v
    d = v["date"]
    return {
        "ok": True,
        "date": d,
        "prev_date": v.get("prev_date"),
        "regime": _regime(d),
        "counts": v.get("counts"),
        "top": v.get("top"),
        "new_in": v.get("new_in"),
        "surge": v.get("surge"),
        "drop": v.get("drop"),
        "accel": v.get("accel"),
        "drop_accel": v.get("drop_accel"),
        "stages": stage(d, plate_type),
        "note": ("本页是**风向标**：看哪个方向有人气（资金在流入）、"
                 "处在启动/主升/高位/退潮哪一步，用来避免在没人气且在下行的方向硬扛。"
                 "⚠️ 阶段是状态描述不是买卖建议；is_new 的可交易性已验证为 False"
                 "（胜率 48.6%、超额 +0.157% 覆盖不了成本）。"),
    }


def _regime_map() -> Dict[str, Dict]:
    """一次性算所有交易日的市场环境（避免 `_regime()` 逐日查库）。

    返回 `{date: {label, level, avg_pct, up_ratio}}`。
    """
    from collections import defaultdict
    try:
        rows = db.fetch("SELECT date, change_pct FROM plate_daily_zz "
                        "WHERE kind='industry'") or []
    except Exception:
        return {}
    buckets: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        v = _f(r.get("change_pct"))
        if v is None:
            continue
        buckets[str(r.get("date"))[:10]].append(v)
    out = {}
    for d, vals in buckets.items():
        avg = sum(vals) / len(vals)
        up = sum(1 for v in vals if v > 0) / len(vals)
        if avg >= 0.5 and up >= 0.6:
            lv, lb = 1, "强势"
        elif avg <= -0.5 or up <= 0.35:
            lv, lb = -1, "弱势"
        else:
            lv, lb = 0, "中性"
        out[d] = {"label": lb, "level": lv,
                  "avg_pct": round(avg, 2), "up_ratio": round(up, 3)}
    return out


def verify(plate_type: int = 17, horizon: int = 1,
           min_days: int = 30, day_from: str = None, day_to: str = None,
           regime_level: int = None) -> Dict:
    """**事件研究**：`is_new=1` 的题材，其后 `horizon` 日涨幅 vs 全体均值。

    口径：
      · 事件 = 某日 `n_days=5` 口径下 `is_new=1` 的题材
      · 收益 = 该题材在 **D+1**（或 D+horizon）的 `n_days=1` 的 `sum_rate`（当日涨幅）
      · 基准 = 同日**全部**题材次日涨幅均值
      · 超额 = 事件组均值 − 基准均值

    ⚠️ 与 11-28 产业链的教训一致：**`valid` 只有在样本足够且超额显著时才是 True**。
       样本 < `min_days` 个交易日 或 |超额| < 0.15% ⇒ `valid=False`，只报数字不下结论。
    """
    ensure_table()
    try:
        ds = [str(r.get("date"))[:10] for r in
              (db.fetch(f"SELECT DISTINCT date FROM {_TABLE} WHERE plate_type=%s "
                        f"AND n_days=5 ORDER BY date", (plate_type,)) or [])
              if r.get("date")]
    except Exception as e:
        return {"ok": False, "error": f"读取日期失败: {str(e)[:80]}"}
    if day_from:
        ds = [x for x in ds if x >= day_from[:10]]
    if day_to:
        ds = [x for x in ds if x <= day_to[:10]]
    # ★★ 2026-11-28（用户："看他在牛市以及熊市的表现"）：
    #   按市场环境（104 板块横截面广度）把交易日分组，分别检验 is_new 的有效性。
    #   这一步比总体均值有用得多 —— 题材轮动是"人气驱动"，熊市里人气本身就不存在，
    #   同一个信号在两种环境下的表现可能完全相反。
    rmap = _regime_map()
    if regime_level is not None:
        ds = [x for x in ds if (rmap.get(x) or {}).get("level") == regime_level]
    if len(ds) < 2:
        return {"ok": False,
                "error": "样本不足（至少需要 2 个交易日）"
                         + ("（已按市场环境过滤）" if regime_level is not None else ""),
                "n_days_total": len(ds),
                "regime_level": regime_level}

    # 一次性把所有 (date, code) 的单日涨幅读进来（n_days=1）
    try:
        one = db.fetch(f"SELECT date, plate_code, sum_rate FROM {_TABLE} "
                       f"WHERE plate_type=%s AND n_days=1", (plate_type,)) or []
    except Exception as e:
        return {"ok": False, "error": f"读取单日涨幅失败: {str(e)[:80]}"}
    daily = {}
    for r in one:
        daily.setdefault(str(r.get("date"))[:10], {})[str(r.get("plate_code"))] = _f(r.get("sum_rate"))
    if not daily:
        return {"ok": False, "error": "缺少 n_days=1 数据 ⇒ 无法还原单日收益，"
                                      "请用 n_days_list=(1,5,20) 重新回补"}

    ev_ret, base_ret = [], []
    used_days = 0
    for i in range(len(ds) - horizon):
        d = ds[i]
        nxt = ds[i + horizon]
        cur = _rows(d, 5, plate_type)
        nd = daily.get(nxt) or {}
        if not cur or not nd:
            continue
        used_days += 1
        # 基准：全部题材次日涨幅均值
        allv = [v for v in nd.values() if v is not None]
        if not allv:
            continue
        base = sum(allv) / len(allv)
        for code, r in cur.items():
            if not r.get("is_new"):
                continue
            v = nd.get(code)
            if v is None:
                continue
            ev_ret.append(v - base)
            base_ret.append(base)
    n = len(ev_ret)
    if n == 0:
        return {"ok": False, "error": "没有可用事件（is_new 全为空或次日数据缺失）",
                "used_days": used_days}
    avg_ex = sum(ev_ret) / n
    win = sum(1 for x in ev_ret if x > 0) / n
    # 标准误（粗算，样本间不独立 ⇒ 真实显著性会更弱，这里用保守门槛）
    var = sum((x - avg_ex) ** 2 for x in ev_ret) / max(n - 1, 1)
    se = (var / n) ** 0.5 if n > 1 else None
    valid = bool(used_days >= min_days and abs(avg_ex) >= 0.15
                 and se is not None and abs(avg_ex) > 2 * se)
    # ★★ 2026-11-28 实测（122 个交易日 / 455 个事件）驱动 —— 区分"统计显著"与"可交易"：
    #   D+1 +0.032%(t=0.76) / D+3 +0.101%(t=2.17) / D+5 +0.157%(t=3.24)
    #   ⇒ D+5 **统计上显著**，但**胜率只有 48.6%**（低于抛硬币）。
    #   含义：少数题材大涨把均值拉起来，多数是回落 —— 典型右偏分布。
    #   +0.157% 的 5 日超额，扣掉双边成本（佣金+印花+滑点 ≈ 0.2~0.3%）后为负。
    #   ⇒ `tradable` 必须同时满足：胜率 > 52% 且 超额 > 0.30%（覆盖成本）。
    #   ⚠️ 这个字段才是"能不能用"的判据，`valid` 只说明"统计上不是噪声"。
    tradable = bool(valid and win >= 0.52 and avg_ex >= 0.30)
    return {
        "ok": True,
        "plate_type": plate_type, "horizon": horizon,
        "regime_level": regime_level,
        "regime_label": ({1: "强势", 0: "中性", -1: "弱势"}.get(regime_level)
                         if regime_level is not None else "全部环境"),
        "n_events": n, "n_days": used_days,
        "avg_excess_pct": round(avg_ex, 3),
        "win_rate": round(win, 4),
        "se": (round(se, 3) if se else None),
        "t_like": (round(avg_ex / se, 2) if se else None),
        "valid": valid,
        "tradable": tradable,
        "note": ("口径：is_new=1 的题材，其 D+horizon 涨幅 − 同日全体题材均值。"
                 "⚠️ 样本间不独立（同一天多个题材共享同一基准）⇒ se 偏乐观。"
                 "【valid】= 统计上不是噪声（样本≥min_days 且 |超额|>2×se）；"
                 "【tradable】= 真的能用（还需 胜率>52% 且 超额>0.30% 覆盖双边成本）。"
                 "2026-11-28 实测 D+5 超额 +0.157%、胜率 48.6% ⇒ valid=True 但 "
                 "tradable=False：均值被少数大涨拉高，多数在回落。"),
    }
