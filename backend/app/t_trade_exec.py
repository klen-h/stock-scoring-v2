# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】做T 的**执行层**：预案落库、成对成交记账、当日汇总
================================================================================
【与 `t_trade.py` 的分工】
  · `t_trade.py`  = **纯计算**（ATR / 支撑阻力 / 方向选择 / 股数 / 触发判断），零 IO、可单测；
  · 本文件        = **有 IO**（建表、读写、记账），不掺业务公式。

【★ 为什么单独建 `t_trades` 而不是复用 `paper_positions`】
  用户（2026-11-15）："两者都要（既推送也记模拟盘）"。
  但 `paper_trading` 是**按日**的"信号→持仓→止盈止损"框架，
  而做T 是**同一标的一天内两次反向操作**：
    · 塞进 `paper_positions` 会与底仓混在一起，**算不出这一笔T 的独立盈亏**；
    · 它的 `auto_ingest_signals` 也按"一条信号一个持仓"建模，做T 是"一笔配对"。
  ⇒ 独立表，一次T = 一行（open_* 先手 + close_* 后手），与底仓完全隔离。

【盈亏方向（容易写反，注意）】
  · `long_t`（正T，先买后卖）：`pnl = (close_price - open_price) × shares`
  · `short_t`（反T，先卖后买）：`pnl = (open_price - close_price) × shares`
================================================================================
"""
import json
from typing import Dict, List, Optional

from app.database import db
from app.flash import rules as flash_rules

_T_PLAN_TABLE = "t_plans"
_T_TRADE_TABLE = "t_trades"
_T_POOL_TABLE = "t_watchlist"
_ready = False


def _now() -> str:
    return flash_rules.beijing_now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return flash_rules.beijing_now().strftime("%Y-%m-%d")


def ensure_tables() -> None:
    """幂等建表（新部署走 schema.sql，老库自动补）。"""
    global _ready
    if _ready:
        return
    try:
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS {_T_PLAN_TABLE} (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                plan_date TEXT NOT NULL,
                price_ref REAL,
                support REAL, support_type TEXT,
                resistance REAL, resistance_type TEXT,
                atr REAL, atr_pct REAL,
                space_pct REAL, space_atr_mult REAL,
                ratio REAL,
                feasible INTEGER DEFAULT 0,
                reason TEXT DEFAULT '',
                candidates_json TEXT,
                created_at TEXT, updated_at TEXT
            )""")
        # 同一只票同一天只保留一份预案（收盘后重算覆盖，盘中不得重算防位漂移）
        db.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_t_plan_code_day "
                   f"ON {_T_PLAN_TABLE} (code, plan_date)")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS {_T_TRADE_TABLE} (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                name TEXT DEFAULT '',
                trade_date TEXT NOT NULL,
                direction TEXT NOT NULL,
                open_price REAL NOT NULL,
                open_shares INTEGER NOT NULL,
                open_time TEXT,
                close_price REAL,
                close_time TEXT,
                pnl REAL,
                pnl_pct REAL,
                status TEXT DEFAULT 'open',
                ratio REAL,
                note TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT
            )""")
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_t_trade_date "
                   f"ON {_T_TRADE_TABLE} (trade_date, status)")
        # ★ 2026-11-15：**做T 独立池**。
        #   【为什么不用 `user_watchlist`】
        #     用户自选只有 4 只（精心挑的），把 19 只做T 候选塞进去会变成 23 只
        #     ⇒ **污染自选页**，而且自选是"我想盯着看的票"，与"今天能做T 的票"
        #     是两个语义，混在一起后两边都变糊。
        #   ⇒ 独立表，做T 的关注范围 = 自选 + 持仓 + 本表。
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS {_T_POOL_TABLE} (
                code TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                note TEXT DEFAULT '',
                added_at TEXT
            )""")
        _ready = True
    except Exception as e:
        # 吞异常必须可见（GUARDRAILS #11）
        print(f"[t-trade] ensure_tables failed: {str(e)[:90]}")


# ══════════════════════════════════════════════════════════════════════════
#  预案
# ══════════════════════════════════════════════════════════════════════════

def save_plan(plan: Dict, day: str = None) -> bool:
    """保存预案（按 `code + plan_date` 幂等覆盖）。

    ⚠️ **只应在收盘后调用**（用当日收盘重算次日预案）。盘中重算会让支撑/阻力漂移，
       触发判断就失去意义 —— 这是 `key_level_watch` 已验证的同款纪律。
    """
    ensure_tables()
    d = day or _today()
    code = str(plan.get("code") or "").strip()
    if not code:
        return False
    pid = f"{code}|{d}"
    try:
        db.upsert(_T_PLAN_TABLE, {
            "id": pid, "code": code, "name": plan.get("name") or code,
            "plan_date": d, "price_ref": plan.get("price"),
            "support": plan.get("support"), "support_type": plan.get("support_type"),
            "resistance": plan.get("resistance"),
            "resistance_type": plan.get("resistance_type"),
            "atr": plan.get("atr"), "atr_pct": plan.get("atr_pct"),
            "space_pct": plan.get("space_pct"),
            "space_atr_mult": plan.get("space_atr_mult"),
            "ratio": plan.get("ratio"),
            "feasible": 1 if plan.get("feasible") else 0,
            "reason": plan.get("reason") or "",
            "candidates_json": json.dumps(plan.get("candidates") or {},
                                          ensure_ascii=False),
            "created_at": _now(), "updated_at": _now(),
        }, conflict_columns=["id"])
        return True
    except Exception as e:
        print(f"[t-trade] save_plan {code} failed: {str(e)[:80]}")
        return False


def load_plan(code: str, day: str = None) -> Optional[Dict]:
    """读某只票某日的预案（默认今天）。没有返回 None。"""
    ensure_tables()
    row = db.fetch_one(
        f"SELECT * FROM {_T_PLAN_TABLE} WHERE code=%s AND plan_date=%s",
        (str(code), day or _today()))
    return dict(row) if row else None


def plan_for_trading(code: str, day: str = None) -> Optional[Dict]:
    """取**当日可用**的预案：优先今天，其次最近一份（盘前还没重算时的兜底）。

    ⚠️ 兜底到"最近一份"是有意的：预案在收盘后生成，次日盘前必然还没有"今天"的行；
      若只认 `plan_date=今天`，盘中永远拿不到预案 ⇒ 功能等于不存在。
      但返回里带 `plan_date`，调用方/界面**必须显示它是哪天的**，不能假装是今天算的。
    """
    ensure_tables()
    d = day or _today()
    row = load_plan(code, d)
    if row:
        return row
    row = db.fetch_one(
        f"SELECT * FROM {_T_PLAN_TABLE} WHERE code=%s "
        f"ORDER BY plan_date DESC LIMIT 1", (str(code),))
    return dict(row) if row else None


def list_plans(day: str = None, feasible_only: bool = False) -> List[Dict]:
    """某日的预案清单；**当天没有则回退到最近一批**（盘前场景）。

    ⚠️ 回退是有意的：预案在**收盘后**生成，次日盘前还没有"今天"的行。
      若只认 `plan_date=今天`，界面上班时间永远是空的。
      但返回里保留 `plan_date`，**界面必须显示它是哪天的**（不能假装今天算的）。
    """
    ensure_tables()
    d = day or _today()
    cond = " AND feasible=1" if feasible_only else ""
    rows = db.fetch(
        f"SELECT * FROM {_T_PLAN_TABLE} WHERE plan_date=%s{cond} ORDER BY code",
        (d,)) or []
    if not rows:
        last = db.fetch_one(
            f"SELECT MAX(plan_date) AS d FROM {_T_PLAN_TABLE} WHERE 1=1{cond}") or {}
        d2 = last.get("d")
        if d2 and str(d2) != d:
            rows = db.fetch(
                f"SELECT * FROM {_T_PLAN_TABLE} WHERE plan_date=%s{cond} "
                f"ORDER BY code", (d2,)) or []
    return [dict(r) for r in rows]


def _load_bars_batch(codes: List[str], per_code: int = 120) -> Dict[str, List[Dict]]:
    """批量取日线（**必须在一条 SQL 里取多只**，否则 1000 只 × 1 次查询要 160 秒）。

    ⚠️ `backtest_prices` 里个股是**裸 6 位码**（指数才带 `sh/sz` 前缀）。
    """
    out: Dict[str, List[Dict]] = {}
    if not codes:
        return out
    placeholders = ",".join(["%s"] * len(codes))
    rows = db.fetch(
        f"SELECT code, date, high, low, close FROM backtest_prices "
        f"WHERE code IN ({placeholders}) ORDER BY code, date", tuple(codes)) or []
    for r in rows:
        c = str(r.get("code") or "")
        out.setdefault(c, []).append({
            "date": str(r.get("date"))[:10],
            "high": r.get("high"), "low": r.get("low"), "close": r.get("close"),
        })
    for c, bs in out.items():
        bs.sort(key=lambda x: x["date"])
        out[c] = bs[-per_code:]
    return out


def scan_market(day: str = None, min_bars: int = 120,
                batch: int = 200, save: bool = True) -> Dict:
    """**盘后**扫全市场，生成/更新预案（`feasible=True` 的落库）。

    【为什么只存 feasible 的】
      全市场约 1000 只/天，全存 = 25 万行/年，而 99% 的日子你只看"今天能做哪几只"。
      需要看某只"为什么不能做"时用 `explain(code)` 实时算（一次只算一只，毫秒级）。

    【为什么必须盘后跑】
      支撑/阻力是**日频**的，盘中重算会让位漂移（`key_level_watch` 已验证的纪律）。
      所以：收盘后算一次 → 存 → 次日盘中只读库 + 实时价判断触发。

    【耗时】1000 只约 **3~10 秒**（批量 SQL 后），远低于逐只查的 164 秒。
    """
    ensure_tables()
    from app import t_trade

    d = day or _today()
    t0 = _now()
    rows = db.fetch(
        "SELECT code, name, COUNT(1) AS n FROM backtest_prices "
        "WHERE code NOT LIKE 'sh%%' AND code NOT LIKE 'sz%%' "
        "GROUP BY code, name HAVING COUNT(1) >= %s ORDER BY code", (min_bars,)) or []
    codes = [str(r["code"]) for r in rows if r.get("code")]
    name_of = {str(r["code"]): str(r.get("name") or "") for r in rows}

    total = feasible = 0
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        bars_map = _load_bars_batch(chunk)
        for c in chunk:
            bs = bars_map.get(c) or []
            if len(bs) < 80:
                continue
            total += 1
            try:
                p = t_trade.build_plan(c, name_of.get(c, c), bs,
                                       price=bs[-1]["close"])
            except Exception as e:
                # 单只失败不影响整批；但必须可见（否则"扫了但没结果"无从排查）
                print(f"[t-trade] build_plan {c} failed: {str(e)[:60]}")
                continue
            if not p.get("feasible"):
                continue
            feasible += 1
            if save:
                save_plan(p, day=d)

    return {"date": d, "scanned": total, "feasible": feasible,
            "started_at": t0, "finished_at": _now(),
            "note": "只落库 feasible=True 的预案；不可行的用 explain() 实时查"}


def explain(code: str, day: str = None) -> Dict:
    """实时解释某只票当前能不能做T（一次只算一只，毫秒级）。

    用于"为什么 000048 不在今天的清单里"这类问题 —— 直接给位、ATR、空间、原因。
    """
    ensure_tables()
    from app import t_trade
    bars_map = _load_bars_batch([str(code)])
    bs = bars_map.get(str(code)) or []
    if len(bs) < 80:
        return {"code": code, "feasible": False, "reason": "日线不足（<80 根）"}
    name = ""
    try:
        row = db.fetch_one(
            "SELECT name FROM backtest_prices WHERE code=%s LIMIT 1", (str(code),))
        name = str((row or {}).get("name") or "")
    except Exception as e:
        print(f"[t-trade] explain name failed: {str(e)[:60]}")
    p = t_trade.build_plan(str(code), name, bs, price=bs[-1]["close"])
    p["as_of"] = bs[-1]["date"]
    return p


# ══════════════════════════════════════════════════════════════════════════
#  持仓（底仓）
# ══════════════════════════════════════════════════════════════════════════

def watch_codes() -> set:
    """做T **关注列表**：自选（`user_watchlist`）+ 持仓（`user_portfolio`）。

    盘中触发只盯这些票 —— 全市场今天可能有 300+ 只 feasible，
    全盯会变成推送灾难（用户只需要"我关心的那几只能不能做"）。
    """
    ensure_tables()
    out = set()
    try:
        from app.portfolio_scope import portfolio_where
        w, p = portfolio_where()
        rows = db.fetch(f"SELECT code FROM user_portfolio {w}", p) or []
        out |= {str(r.get("code") or "").strip() for r in rows}
        # 自选（用户主动关注的票）
        rows = db.fetch("SELECT code FROM user_watchlist") or []
        out |= {str(r.get("code") or "").strip() for r in rows}
        # ★ 做T 独立池（与自选分开，避免污染自选页）
        rows = db.fetch(f"SELECT code FROM {_T_POOL_TABLE}") or []
        out |= {str(r.get("code") or "").strip() for r in rows}
    except Exception as e:
        print(f"[t-trade] watch_codes failed: {str(e)[:70]}")   # 吞异常必须可见
    return {c for c in out if c}


def pool_codes() -> List[Dict]:
    """做T 池里的票（独立于自选）。"""
    ensure_tables()
    rows = db.fetch(f"SELECT code, name, note, added_at FROM {_T_POOL_TABLE} "
                    f"ORDER BY added_at DESC, code") or []
    return [dict(r) for r in rows]


def add_to_pool(codes: List[str], note: str = "") -> Dict:
    """把股票加进做T 池（幂等，已存在的跳过）。

    ⚠️ **不写 `user_watchlist`** —— 自选是"我想盯的票"，
      与"今天能做T 的票"是两个语义，混在一起会污染自选页。
    """
    ensure_tables()
    added, skipped = [], []
    for c in codes or []:
        code = str(c or "").strip()
        if not code:
            continue
        if db.fetch_one(f"SELECT 1 AS x FROM {_T_POOL_TABLE} WHERE code=%s",
                        (code,)):
            skipped.append(code)
            continue
        # 取名兜底链：`t_plans` → `kline_cache` → `backtest_prices` → `stock_facts_wide`
        #   ⚠️ 实测：`kline_cache` / `backtest_prices` 里 000423 / 000088 / 600483 /
        #      688188 / 000415 的 `name` **都是空串**（科创板与部分主板缺失）
        #      ⇒ `name` 被存成代码本身，界面显示"000423"。
        #      **`t_plans` 里这些票有名字**（`scan_market` 时从 `backtest_prices.name`
        #      写入……但那也是空；实际是 `list_plans` 读到的是库里的 name 列有值）
        #      ⇒ 把 `t_plans` 放**最前**，它是"做T 域"最权威的名字来源。
        nm = ""
        for tbl in (_T_PLAN_TABLE, "kline_cache", "backtest_prices", "stock_facts_wide"):
            try:
                row = db.fetch_one(
                    f"SELECT name FROM {tbl} WHERE code=%s "
                    f"AND name IS NOT NULL AND name<>'' LIMIT 1", (code,))
                if row and row.get("name"):
                    nm = str(row["name"])
                    break
            except Exception as e:
                print(f"[t-trade] pool name {code}@{tbl} failed: {str(e)[:40]}")
        try:
            db.upsert(_T_POOL_TABLE, {
                "code": code, "name": nm, "note": note or "", "added_at": _now(),
            }, conflict_columns=["code"])
            added.append(code)
        except Exception as e:
            print(f"[t-trade] pool add {code} failed: {str(e)[:60]}")
    return {"added": added, "skipped": skipped,
            "total": len(pool_codes())}


def remove_from_pool(code: str) -> bool:
    ensure_tables()
    try:
        db.execute(f"DELETE FROM {_T_POOL_TABLE} WHERE code=%s", (str(code),))
        return True
    except Exception as e:
        print(f"[t-trade] pool remove failed: {str(e)[:60]}")
        return False


def holdings() -> Dict[str, Dict]:
    """当前底仓 `{code: {name, shares, cost}}`（与 `portfolio_radar` 同口径）。"""
    out: Dict[str, Dict] = {}
    try:
        from app.portfolio_scope import portfolio_where
        w, p = portfolio_where()
        rows = db.fetch(
            f"SELECT code, name, shares, cost FROM user_portfolio {w}", p) or []
        for r in rows:
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            out[code] = {"name": str(r.get("name") or ""),
                         "shares": float(r.get("shares") or 0),
                         "cost": float(r.get("cost") or 0)}
    except Exception as e:
        print(f"[t-trade] holdings failed: {str(e)[:80]}")
    return out


def today_bought(code: str, day: str = None) -> float:
    """今日已买入的做T 股数（**T+1 不可卖**，反T 卖出量必须扣掉）。

    ⚠️ 只能感知做T 自己买的；**用户手动加仓无法感知** ⇒
      反T 的文案里必须提示"若今日手动加仓，请自行核对可卖数量"。
    """
    ensure_tables()
    row = db.fetch_one(
        f"SELECT SUM(open_shares) AS n FROM {_T_TRADE_TABLE} "
        f"WHERE code=%s AND trade_date=%s AND direction='long_t'",
        (str(code), day or _today()))
    try:
        return float((row or {}).get("n") or 0)
    except (TypeError, ValueError):
        return 0.0


# ══════════════════════════════════════════════════════════════════════════
#  成交记账
# ══════════════════════════════════════════════════════════════════════════

def has_traded(code: str, day: str = None) -> bool:
    """今天这只票是否已经开过T（**每只每天最多 1 次**，防抖）。"""
    ensure_tables()
    row = db.fetch_one(
        f"SELECT 1 AS x FROM {_T_TRADE_TABLE} WHERE code=%s AND trade_date=%s LIMIT 1",
        (str(code), day or _today()))
    return bool(row)


def open_trade(code: str, name: str, direction: str, price: float,
               shares: int, ratio: float, note: str = "",
               day: str = None) -> Optional[Dict]:
    """开仓（正T 买入 / 反T 卖出底仓）。返回记账行。"""
    ensure_tables()
    d = day or _today()
    if shares <= 0 or price <= 0:
        return None
    if has_traded(code, d):
        return None                    # 每只每天 1 次
    tid = f"{d}|{code}|{direction}"
    ts = _now()
    try:
        db.upsert(_T_TRADE_TABLE, {
            "id": tid, "code": code, "name": name or code, "trade_date": d,
            "direction": direction, "open_price": round(price, 3),
            "open_shares": int(shares), "open_time": ts,
            "close_price": None, "close_time": None, "pnl": None, "pnl_pct": None,
            "status": "open", "ratio": ratio, "note": note or "",
            "created_at": ts, "updated_at": ts,
        }, conflict_columns=["id"])
        return {"id": tid, "code": code, "direction": direction,
                "open_price": round(price, 3), "shares": int(shares)}
    except Exception as e:
        print(f"[t-trade] open_trade {code} failed: {str(e)[:80]}")
        return None


def close_trade(tid: str, price: float, status: str = "closed",
                note: str = "") -> Optional[Dict]:
    """平仓（正T 卖出 / 反T 买回），写盈亏。"""
    ensure_tables()
    row = db.fetch_one(f"SELECT * FROM {_T_TRADE_TABLE} WHERE id=%s", (tid,))
    if not row:
        return None
    r = dict(row)
    if r.get("status") != "open":
        return None
    op = float(r.get("open_price") or 0)
    sh = int(r.get("open_shares") or 0)
    if op <= 0 or sh <= 0 or price <= 0:
        return None
    # 正T 先买后卖 ⇒ (后−先)；反T 先卖后买 ⇒ (先−后)
    sign = 1 if r.get("direction") == "long_t" else -1
    pnl = round(sign * (price - op) * sh, 2)
    pnl_pct = round(sign * (price / op - 1) * 100, 3)
    try:
        db.execute(
            f"UPDATE {_T_TRADE_TABLE} SET close_price=%s, close_time=%s, pnl=%s, "
            f"pnl_pct=%s, status=%s, note=%s, updated_at=%s WHERE id=%s",
            (round(price, 3), _now(), pnl, pnl_pct, status,
             (note or r.get("note") or ""), _now(), tid))
        return {"id": tid, "close_price": round(price, 3), "pnl": pnl,
                "pnl_pct": pnl_pct, "status": status}
    except Exception as e:
        print(f"[t-trade] close_trade {tid} failed: {str(e)[:80]}")
        return None


def open_trades(day: str = None) -> List[Dict]:
    """当日**未平**的做T（盘中要盯的）。"""
    ensure_tables()
    rows = db.fetch(
        f"SELECT * FROM {_T_TRADE_TABLE} WHERE trade_date=%s AND status='open' "
        f"ORDER BY open_time", (day or _today(),)) or []
    return [dict(r) for r in rows]


def daily_summary(day: str = None) -> Dict:
    """当日做T 汇总：预案 + 未平 + 已平 + 统计（含模拟盈亏）。"""
    ensure_tables()
    d = day or _today()
    rows = db.fetch(
        f"SELECT * FROM {_T_TRADE_TABLE} WHERE trade_date=%s ORDER BY open_time",
        (d,)) or []
    trades = [dict(r) for r in rows]
    closed = [t for t in trades if t.get("status") in ("closed", "stopped")]
    opened = [t for t in trades if t.get("status") == "open"]
    pnl = round(sum(float(t.get("pnl") or 0) for t in closed), 2)
    win = sum(1 for t in closed if float(t.get("pnl") or 0) > 0)
    return {
        "date": d,
        "plans": list_plans(d, feasible_only=True),
        "open": opened,
        "closed": closed,
        "stats": {
            "trades": len(trades),
            "closed": len(closed),
            "open": len(opened),
            "win": win,
            "win_rate_pct": round(win / len(closed) * 100, 1) if closed else None,
            "pnl": pnl,
        },
    }
