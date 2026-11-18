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
import os
from typing import Dict, List, Optional

from app.database import db
from app.flash import rules as flash_rules

# ★ 2026-11-19（用户："池子系统通过挑选自动加入池"）：自动选池的数量上限。
#   【为什么是 15】池子会进 `watch_codes` ⇒ 盘中每 60s 被扫一遍（读内存，成本低），
#     但**推送**是按"触发"出的：池子越大越容易一天好几次提醒。
#     15 只 ≈ 一天 1~3 条，是可接受的噪音水平。
POOL_AUTO_TOP = int(os.environ.get("T_POOL_TOP") or 15)

_T_PLAN_TABLE = "t_plans"
_T_TRADE_TABLE = "t_trades"
_T_POOL_TABLE = "t_watchlist"
# ★ 2026-11-16（用户："价格到位，系统自动处理"）：做T **模拟底仓**
#   【为什么必须单独一张表】系统没有券商接口，自动建仓**不可能真实成交**；
#     而 `user_portfolio` 是用户手工维护的真实持仓（且无 source 列可区分），
#     自动写入会让人误以为真的买了 ⇒ 只能建在独立的模拟层。
_T_SIM_TABLE = "t_sim_positions"
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
        # ★★ 2026-11-19（用户："池子系统通过挑选自动加入池"）：**来源列**。
        #   【为什么必须有】自动选池每次都会"替换自己挑的那批"，
        #     若不区分来源，就会把**用户手动加的票一起清掉** ——
        #     用户挑的票被系统删掉，是最不能接受的一类交互事故。
        #   取值：`auto`（系统挑选，每日可替换）/ `manual`（用户手动，永不自动删）
        #   ⚠️ 老库升级：默认 `manual` —— 历史行都是用户/我手工加的，归 manual 才对。
        try:
            db.execute(f"ALTER TABLE {_T_POOL_TABLE} "
                       f"ADD COLUMN source TEXT DEFAULT 'manual'")
        except Exception:
            pass        # 列已存在 —— 幂等升级的正常路径
        # ★ 2026-11-16：做T 模拟底仓（"价格到位自动处理"的落点）
        #   `status`: holding（持有）/ closed（已平）
        #   `plan_id`: 来自哪条建仓计划（便于回溯，NULL 表示手工建）
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS {_T_SIM_TABLE} (
                code TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                shares INTEGER NOT NULL DEFAULT 0,
                cost REAL DEFAULT 0,
                open_price REAL DEFAULT 0,
                open_date TEXT,
                plan_id TEXT DEFAULT '',
                status TEXT DEFAULT 'holding',
                note TEXT DEFAULT '',
                created_at TEXT, updated_at TEXT
            )""")
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_t_sim_status "
                   f"ON {_T_SIM_TABLE} (status, open_date)")
        # ★★ 2026-11-18（用户："自动用模拟仓模拟"）：**平仓记账字段**。
        #   老表只有开仓信息（cost/open_price/status），平仓只能把 status 改成 closed，
        #   **赚了多少完全没记** ⇒ 界面仍只是"达目标✓"这种纸面状态，
        #   无法回答"模拟仓到底盈利多少"。补列走幂等 ALTER（老库自动升级）。
        for _col in ("close_price REAL", "close_date TEXT", "close_reason TEXT",
                     "pnl REAL", "pnl_pct REAL"):
            try:
                db.execute(f"ALTER TABLE {_T_SIM_TABLE} ADD COLUMN {_col}")
            except Exception:
                pass     # 列已存在 —— 幂等升级，正常路径
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

    # ★★ 2026-11-16（用户："做T 交易台的扫描是最新的了吗？"）：
    #   **把当日"本次没重算"的旧预案标为不可行**。
    #   原实现只 upsert `feasible=True` 的行，于是一只票从"可行"变"不可行"时，
    #   旧行仍留在库里且 `feasible=1` ⇒ `/plans`（默认只返回 feasible）继续展示它
    #   ⇒ 用户按**已经失效的价位**设建仓点。
    #   实测 000878 云南铜业：15:42 那批按 11-13 基准 13.01 写入
    #   （支撑 12.71）；22:12 重算后今日收 12.62、空间只剩 0.70×ATR < 0.8
    #   门槛 ⇒ 不再 feasible ⇒ 没被覆盖 ⇒ 旧预案冒充"今日预案"留在界面上。
    #   判定口径：`updated_at < 本次开始时间` = 本次没写过；
    #   只处理 `plan_date=d`，不动历史日期的预案。
    stale = 0
    try:
        stale = int(db.execute(
            f"UPDATE {_T_PLAN_TABLE} SET feasible=0 "
            f"WHERE plan_date=%s AND feasible=1 AND updated_at<%s",
            (d, t0)) or 0)
        if stale:
            print(f"[t-trade] marked {stale} stale plans for {d}")
    except Exception as e:
        # 吞异常必须可见，但**不能让清理失败毁掉本次已经写好的预案**
        print(f"[t-trade] mark stale plans failed: {str(e)[:90]}")

    # ★★ 2026-11-19（用户："池子系统通过挑选自动加入池"）：
    #   扫描完**顺手自动选池** —— 池子与预案同源（都是 `t_plans` 的当日 feasible），
    #   不必再独立跑一遍查询。放在 stale 标记之后，保证只挑**本次有效**的预案。
    pick = {}
    try:
        pick = auto_pick_pool(d)
    except Exception as e:
        # 选池失败不能毁掉本次已经写好的预案（吞异常必须可见）
        print(f"[t-trade] auto_pick after scan failed: {str(e)[:80]}")

    return {"date": d, "scanned": total, "feasible": feasible,
            "stale_marked": stale,
            "pool_picked": len(pick.get("picked") or []),
            "pool_removed": pick.get("removed") or 0,
            "started_at": t0, "finished_at": _now(),
            "note": "只落库 feasible=True 的预案；本次未重算的旧预案标为不可行；"
                    "池子只替换自动挑选的行"}


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
        # ★★ 2026-11-15（用户："现有 4 只去掉"）：**不再包含 `user_watchlist`**。
        #   【为什么】自选是"我想盯的票"，做T 池是"能做T 的票"，两个语义。
        #     上一版把自选自动并进来 ⇒ 用户自选的 4 只（焦作万方/赞宇科技/
        #     东方铁塔/冠农股份）会被做T 循环顺带监控，用户明确要去掉。
        #   【怎么加票】想把某只票拿来做T ⇒ 加进 `t_watchlist`，**不要加自选**。
        #   ⚠️ 只改"做T 关注范围"，**没有动 `user_watchlist` 的数据**。
        rows = db.fetch(f"SELECT code FROM {_T_POOL_TABLE}") or []
        out |= {str(r.get("code") or "").strip() for r in rows}
        # ★ 2026-11-16：**模拟底仓必须继续被盯** ——
        #   自动建仓的票若从池里被删，只看"持仓+池"就会漏掉，
        #   等于建了仓却不做T（比不建更糟：白占一个仓位假设）。
        out |= set(sim_holdings().keys())
    except Exception as e:
        print(f"[t-trade] watch_codes failed: {str(e)[:70]}")   # 吞异常必须可见
    return {c for c in out if c}


def pool_codes() -> List[Dict]:
    """做T 池里的票（独立于自选），带 `source`（auto / manual）。"""
    ensure_tables()
    rows = db.fetch(
        f"SELECT code, name, note, added_at, "
        f"COALESCE(source,'manual') AS source FROM {_T_POOL_TABLE} "
        f"ORDER BY CASE WHEN COALESCE(source,'manual')='manual' THEN 0 ELSE 1 END, "
        f"added_at DESC, code") or []
    return [dict(r) for r in rows]


def add_to_pool(codes: List[str], note: str = "", source: str = "manual") -> Dict:
    """把股票加进做T 池（幂等）。

    ★ 2026-11-19：**手动加会"认领"自动挑的票** ——
      若该票在池里且 `source='auto'`，手动加入时升级为 `manual`，
      否则明天的自动选池会把它当"自己挑的"一并清掉（用户手动加的票被系统删掉，
      是最不能接受的一类交互事故）。

    ⚠️ **不写 `user_watchlist`** —— 自选是"我想盯的票"，
      与"今天能做T 的票"是两个语义，混在一起会污染自选页。
    """
    ensure_tables()
    added, skipped, upgraded = [], [], []
    for c in codes or []:
        code = str(c or "").strip()
        if not code:
            continue
        exist = db.fetch_one(
            f"SELECT COALESCE(source,'manual') AS source FROM {_T_POOL_TABLE} "
            f"WHERE code=%s", (code,))
        if exist:
            # 已存在：自动挑的票被手动加入 ⇒ 升级为 manual（防明天被清）
            if str(exist.get("source") or "manual") == "auto" and source == "manual":
                try:
                    db.execute(
                        f"UPDATE {_T_POOL_TABLE} SET source='manual', "
                        f"note=%s WHERE code=%s",
                        (note or "手动加入（原为自动挑选）", code))
                    upgraded.append(code)
                except Exception as e:
                    print(f"[t-trade] pool upgrade {code} failed: {str(e)[:50]}")
            else:
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
                "code": code, "name": nm, "note": note or "",
                "added_at": _now(), "source": source,
            }, conflict_columns=["code"])
            added.append(code)
        except Exception as e:
            print(f"[t-trade] pool add {code} failed: {str(e)[:60]}")
    return {"added": added, "skipped": skipped, "upgraded": upgraded,
            "total": len(pool_codes())}


def remove_from_pool(code: str) -> bool:
    ensure_tables()
    try:
        db.execute(f"DELETE FROM {_T_POOL_TABLE} WHERE code=%s", (str(code),))
        return True
    except Exception as e:
        print(f"[t-trade] pool remove failed: {str(e)[:60]}")
        return False


def auto_pick_pool(day: str = None, top: int = None) -> Dict:
    """盘后**自动挑选**做T 候选进池 —— **只替换 `source='auto'` 的行**。

    ★★ 2026-11-19（用户："池子系统通过挑选自动加入池"）：
      原先池子完全靠手动（11-15 手工批量写入 19 只，后来因为都没底仓又清空）。
      手动维护的问题是"挑哪几只"没有口径、也不随时间更新 ——
      今天可行的票明天可能就不可行了，而池子还停在旧名单上。

    【选票口径 —— 与「全市场可行」同一份数据，不另算】
      · 当日**最新批次**且 `feasible=1` 的预案；
      · 按 **`space_pct × ratio`（空间% × 仓位比例）降序** ——
        这才是"做一次T 对总资产的期望收益"。
        【为什么不用 `space_atr_mult`】实测当日 343 只 feasible 里 **142 只都是 1.00**
          （预案的阻力位本就按 1×ATR 设），排序等于失效；当时选出的 15 只
          期望从 0.99% 一路掉到 **0.57%**，等于随机挑。
        【为什么不用裸 `space_pct`】那只是价格的函数，高价股天然空间大；
          而且高波动票的 `ratio` 会被自动压到 0.2，光看空间会挑到"看着大、实际下注小"的票。
      · 排除**已持仓 / 已有模拟仓**的票 —— 它们本来就在 `watch_codes` 里，
        再进池只是重复（而且持仓做T 走的是另一条判断）；
      · 排除**手动加的票**（`source='manual'`）—— 用户自己挑的永远不动。
      ⚠️ 未纳入**流动性**（`t_plans` 无成交额字段）；若某只票日成交极低，
         进池后可能触发但做不成 —— 后续可 join `stock_facts_wide` 补这条。

    【为什么不顺便建计划】
      用户 2026-11-16 明确区分过"观察"与"建仓"：池子是"值得盯"，
      设建仓点是"准备买"。自动建计划会一次性糊出十几条计划，
      且每条的买点都是支撑位 —— 那是另一套决策，不该由选池顺手决定。

    返回 `{date, plan_date, picked, removed, total, note}`。
    """
    ensure_tables()
    n_top = max(1, int(top or POOL_AUTO_TOP))
    d = str(day or _today())[:10]

    # 用"不晚于 d 的最新批次"—— 手动补跑历史日时不会误用今天的预案
    row = db.fetch_one(
        f"SELECT MAX(plan_date) AS d FROM {_T_PLAN_TABLE} "
        f"WHERE plan_date <= %s", (d,)) or {}
    pd = str(row.get("d") or "")[:10]
    if not pd:
        return {"date": d, "plan_date": None, "picked": [], "removed": 0,
                "total": len(pool_codes()),
                "note": "还没有任何预案批次（先跑一次盘后扫描）"}

    rows = db.fetch(
        f"SELECT code, name, space_atr_mult, space_pct, ratio, atr_pct "
        f"FROM {_T_PLAN_TABLE} WHERE plan_date=%s AND feasible=1 "
        f"ORDER BY (COALESCE(space_pct,0) * COALESCE(ratio,0)) DESC, "
        f"         space_atr_mult DESC, COALESCE(atr_pct,0) ASC LIMIT %s",
        (pd, n_top * 3)) or []

    held = set(holdings().keys())
    manual = {str(r.get("code") or "").strip() for r in db.fetch(
        f"SELECT code FROM {_T_POOL_TABLE} "
        f"WHERE COALESCE(source,'manual') <> 'auto'") or []}

    picked: List[Dict] = []
    for r in rows:
        c = str(r.get("code") or "").strip()
        if not c or c in held or c in manual:
            continue
        picked.append(r)
        if len(picked) >= n_top:
            break

    # 先清掉"上一轮自动挑的"，再写新一轮（manual 的行一条都不碰）
    removed = 0
    try:
        removed = int(db.execute(
            f"DELETE FROM {_T_POOL_TABLE} "
            f"WHERE COALESCE(source,'manual') = 'auto'") or 0)
    except Exception as e:
        print(f"[t-trade] auto_pick clear failed: {str(e)[:70]}")

    added: List[str] = []
    for r in picked:
        c = str(r.get("code") or "").strip()
        try:
            _sp = float(r.get("space_pct") or 0)
            _rt = float(r.get("ratio") or 0)
            db.upsert(_T_POOL_TABLE, {
                "code": c,
                "name": resolve_name(c, r.get("name") or ""),
                "note": "自动挑选（空间 %.2f%% × 仓位 %.0f%% ≈ 期望 %.2f%%）"
                        % (_sp, _rt * 100, _sp * _rt),
                "added_at": _now(), "source": "auto",
            }, conflict_columns=["code"])
            added.append(c)
        except Exception as e:
            print(f"[t-trade] auto_pick add {c} failed: {str(e)[:60]}")

    if added or removed:
        print(f"[t-trade] auto_pick {pd}: +{len(added)} -{removed} "
              f"(held {len(held)} / manual {len(manual)} skipped)")
    return {"date": d, "plan_date": pd, "picked": added, "removed": removed,
            "excluded_held": len(held), "excluded_manual": len(manual),
            "total": len(pool_codes()),
            "note": "只替换自动挑选的行；手动加入的票不动"}


def holdings() -> Dict[str, Dict]:
    """当前底仓 `{code: {name, shares, cost, sim}}` = **真实持仓 + 模拟底仓**。

    【为什么合并两层，以及为什么真实优先】
      用户（2026-11-16）："价格到位，系统自动处理。"
      系统没有券商接口，**不可能真实下单**；自动建仓只能建在**模拟层**。
      但做T 的判断只需要"账面上有多少股可卖"，所以把两层合起来喂给
      `calc_shares` 即可 —— **前提是真实持仓永远压过模拟仓**：
        · 同一只票真实持有 500 股、模拟仓也有 500 股 ⇒ 取真实 500（不叠加，
          否则会凭空多出可卖数量，算出不存在的 T）；
        · `sim=True` 让调用方能在文案里说清"这是模拟底仓"。

    ⚠️ **绝不写 `user_portfolio`**：那张表是用户手工维护的真实持仓，
      自动写入会让人误以为真的买入了（且无 `source` 列可区分）。
    """
    ensure_tables()
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
                         "cost": float(r.get("cost") or 0),
                         "sim": False}
    except Exception as e:
        print(f"[t-trade] holdings(real) failed: {str(e)[:80]}")
    # 模拟底仓：只补**真实持仓里没有**的票
    for code, s in sim_holdings().items():
        if code not in out:
            out[code] = s
    return out


def sim_holdings() -> Dict[str, Dict]:
    """做T **模拟底仓** `{code: {name, shares, cost, sim: True, ...}}`。

    由「建仓点触发」自动写入（`_AUTO_BUILD` 开启时），或手动调用
    `auto_build()`。**与 `user_portfolio` 完全隔离**。
    """
    ensure_tables()
    out: Dict[str, Dict] = {}
    try:
        rows = db.fetch(
            f"SELECT * FROM {_T_SIM_TABLE} WHERE status='holding'") or []
    except Exception as e:
        print(f"[t-trade] sim_holdings failed: {str(e)[:80]}")
        return out
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        out[code] = {
            "name": str(r.get("name") or ""),
            "shares": float(r.get("shares") or 0),
            "cost": float(r.get("cost") or 0),
            "sim": True,
            "plan_id": r.get("plan_id"),
            "open_date": str(r.get("open_date") or "")[:10],
            "open_price": float(r.get("open_price") or 0),
        }
    return out


def resolve_name(code: str, name: str = "") -> str:
    """取股票名（`name` 已是有效值就直接用）。

    ⚠️ **踩过坑**：`kline_cache` / `backtest_prices` 里部分票（000423 东阿阿胶、
      000088 盐田港、600483 福能股份、688188 柏楚电子、000415 渤海租赁）
      的 `name` **是空串** ⇒ 界面显示成代码本身。
      兜底链把 `t_plans` 放最前（做T 域最权威的名字源）。
    """
    nm = str(name or "").strip()
    c = str(code or "").strip()
    if nm and nm != c:
        return nm
    for tbl in (_T_PLAN_TABLE, "kline_cache", "backtest_prices", "stock_facts_wide"):
        try:
            row = db.fetch_one(
                f"SELECT name FROM {tbl} WHERE code=%s "
                f"AND name IS NOT NULL AND name<>'' LIMIT 1", (c,))
            if row and row.get("name"):
                return str(row["name"])
        except Exception as e:
            print(f"[t-trade] resolve_name {c}@{tbl} failed: {str(e)[:40]}")
    return nm or c


def sim_positions() -> List[Dict]:
    """模拟底仓列表（前端展示 / 清除用）。"""
    ensure_tables()
    try:
        rows = db.fetch(
            f"SELECT * FROM {_T_SIM_TABLE} ORDER BY open_date DESC, code") or []
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[t-trade] sim_positions failed: {str(e)[:80]}")
        return []


def auto_build(code: str, name: str, price: float, shares: int,
               plan_id: str = "", note: str = "",
               day: str = None) -> Optional[Dict]:
    """**模拟**建仓（建仓点触发时调用）。幂等：已有 holding 行就跳过。

    ⚠️ 这是模拟，不是真实成交。文案必须让用户看到"模拟"两个字。
    """
    ensure_tables()
    c = str(code or "").strip()
    if not c or shares <= 0 or price <= 0:
        return None
    exist = db.fetch_one(
        f"SELECT * FROM {_T_SIM_TABLE} WHERE code=%s AND status='holding'", (c,))
    if exist:
        return {"skipped": True, "code": c,
                "shares": float(exist.get("shares") or 0)}
    d = day or _today()
    ts = _now()
    try:
        db.upsert(_T_SIM_TABLE, {
            "code": c, "name": resolve_name(c, name),
            "shares": int(shares), "cost": round(float(price), 3),
            "open_price": round(float(price), 3), "open_date": d,
            "plan_id": plan_id or "", "status": "holding",
            "note": note or "", "created_at": ts, "updated_at": ts,
        }, conflict_columns=["code"])
        # 策略：同一只票当天只建一次；价格变化不重算成本（避免"越跌越改成本"）
        return {"skipped": False, "code": c, "shares": int(shares),
                "price": round(float(price), 3)}
    except Exception as e:
        print(f"[t-trade] auto_build {c} failed: {str(e)[:80]}")
        return None


def close_sim_by_plan(plan_id: str, price: float, reason: str = "") -> Optional[Dict]:
    """按**交易计划 ID** 平掉模拟仓并**记账盈亏**（幂等，重复调用只记一次）。

    ★★ 2026-11-18（用户："不需要我手动上车，自动用模拟仓模拟"）：
      原来的 `close_sim(code)` 只把 status 改成 closed、**不记任何盈亏** ——
      于是 UI 上永远只有"达目标✓"这种价位状态，回答不了"模拟赚了多少"。

    【为什么按 plan_id 而不是 code】
      `t_sim_positions` 主键是 code（一只票同时只有一个模拟底仓），
      但计划是按 plan_id 触发的。按 plan_id 查能确保**平的是这条计划建的仓**，
      不会误平另一条计划的仓位（例如同一只票后来又设了新计划）。

    【盈亏以开仓 cost × shares 计算】
      `cost` 是建仓时的**实际触发价**（见 `auto_build`），不用 buy_price ——
      否则一开始就带着一个假的成本偏差。

    返回 `{code, shares, cost, close, pnl, pnl_pct}` 或 None（无仓/数据异常）。
    """
    ensure_tables()
    pid = str(plan_id or "").strip()
    px = float(price or 0)
    if not pid or px <= 0:
        return None
    row = db.fetch_one(
        f"SELECT * FROM {_T_SIM_TABLE} WHERE plan_id=%s AND status='holding'", (pid,))
    if not row:
        return None
    cost, shares = float(row.get("cost") or 0), float(row.get("shares") or 0)
    if cost <= 0 or shares <= 0:
        return None
    pnl = round((px - cost) * shares, 2)
    pnl_pct = round((px / cost - 1) * 100, 2)
    try:
        n = db.execute(
            f"UPDATE {_T_SIM_TABLE} SET status='closed', close_price=%s, "
            f"close_date=%s, close_reason=%s, pnl=%s, pnl_pct=%s, "
            f"updated_at=%s, note=%s "
            f"WHERE plan_id=%s AND status='holding'",
            (round(px, 3), _today(), reason or "", pnl, pnl_pct, _now(),
             reason or "", pid))
        if not n:                      # 并发下另一路已平 ⇒ 不重复记账
            return None
        return {"code": row.get("code"), "shares": shares, "cost": cost,
                "close": round(px, 3), "pnl": pnl, "pnl_pct": pnl_pct}
    except Exception as e:
        print(f"[t-trade] close_sim_by_plan {pid} failed: {str(e)[:80]}")
        return None


def close_sim(code: str, note: str = "") -> bool:
    """平掉**模拟底仓**（仅标记 status，不删历史）。"""
    ensure_tables()
    try:
        db.execute(
            f"UPDATE {_T_SIM_TABLE} SET status='closed', updated_at=%s, "
            f"note=%s WHERE code=%s AND status='holding'",
            (_now(), note or "", str(code)))
        return True
    except Exception as e:
        print(f"[t-trade] close_sim failed: {str(e)[:80]}")
        return False


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
