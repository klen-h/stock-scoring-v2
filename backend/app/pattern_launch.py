# -*- coding: utf-8 -*-
"""
================================================================================
「缩量调整 → 放量启动」形态 · 每日扫描 + 观察追踪（生产版）
================================================================================
2026-10-15 落地（用户需求：每日自动扫描符合走势形态的个股 + 醒目标注 + 持续观察 +
预留回测条件重设接口）。

★ 研究结论（`scripts/pattern_launch_check.py` / `pattern_launch_grid.py`，已过
  基准口径修正 + 参数网格 + 成本/可执行性三关）：
    · T+10 超额 +2.09pp（净 +1.80pp）、网格 12 组合全正且显著 ⇒ **稳健的正边际**；
    · 但胜率 43~45%、中位收益为负、T+5 全负 ⇒ **正边际但不赚钱**；
    · ⇒ 本功能定位 = **描述性观察工具（候选清单 + 后续追踪），不构成买卖建议**。

【数据源】`backtest_prices`（989 只回测池、每日更新到昨日）—— 唯一"每日更新"的日线源；
  `zzshare_daily.db` 冻结在 2026-09-24 只供离线研究。未来要全市场：把 loader 换成
  `pack_source`（K 线包，~2700 只）—— `scan_stock`/`evaluate` 与数据源解耦，预留。

【参数】`pattern_config` 表（JSON 单行），`PUT /api/pattern/config` 可重设
  ⇒ 满足"未来根据新发现重新设定回测条件"的预留接口（要求④）。

【表】
  · pattern_scan_daily：每日扫描结果（幂等，PK=(date,code)）
  · pattern_watch   ：个人观察清单 + 后续变化追踪（T+1..T+20 收益/最大涨幅/最大回撤）
  · pattern_config  ：形态参数（JSON，单行 key='launch'）

【口径】与 `scripts/pattern_launch_check.py` 的 scan_stock 逐条一致（缩量/调整幅度/
  不创新高/启动涨幅/放量/突破/冷却），唯一差异：本模块只扫"最新已完成交易日"的信号。
"""
import json
import statistics
import time

from app.database import db

# ── 参数默认值（= 研究网格最优档：3% 启动 / 0.7 缩量，T+10 净超额 +1.99pp）──
DEFAULTS = {
    "enabled": True,
    "launch_pct": 3.0,          # 启动日最小涨幅（%）
    "vol_shrink": 0.7,          # 调整期均量 / 基准均量 上限
    "pullback_days": 20,        # 调整窗口（交易日）
    "vol_base_days": 60,        # 基准量窗口（交易日）
    "vol_surge": 2.0,           # 启动日量 / 调整期均量 下限
    "pullback_min": -0.25,      # 调整期累计涨跌下限
    "pullback_max": 0.03,       # 调整期累计涨跌上限
    "below_high_tol": 0.0,      # 调整期最高价 ≤ 基准期最高价 × (1+tol)
    "breakout": True,           # 启动日收盘 > 调整期最高价
    "cooldown": 10,             # 同股相邻信号最小间隔（交易日）
    "fwd_watch": [1, 5, 10, 20],  # 观察追踪的 T+N 窗口
    "target": 0.10,             # 达标阈值（区间最大涨幅）
    "max_results": 30,          # 每日输出上限
}
_SCAN_TABLE = "pattern_scan_daily"
_WATCH_TABLE = "pattern_watch"
_CONFIG_TABLE = "pattern_config"


# ================================================================
# 表初始化（幂等；通用类型兼容 PG / SQLite）
# ================================================================
def ensure_tables() -> None:
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_SCAN_TABLE} (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            params_hash TEXT,
            launch_date TEXT,
            launch_pct REAL,
            vol_ratio REAL,
            pullback_ret REAL,
            surge REAL,
            price REAL,
            board TEXT,
            industry TEXT,
            score INTEGER,
            score_json TEXT,
            created_at TEXT,
            PRIMARY KEY (date, code)
        )""")
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_WATCH_TABLE} (
            code TEXT PRIMARY KEY,
            name TEXT,
            added_date TEXT,
            launch_date TEXT,
            params_json TEXT,
            first_price REAL,
            last_price REAL,
            last_date TEXT,
            ret_json TEXT,
            max_up REAL,
            max_dd REAL,
            status TEXT DEFAULT '观察中',
            note TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        )""")
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_CONFIG_TABLE} (
            key TEXT PRIMARY KEY,
            cfg_json TEXT,
            updated_at TEXT
        )""")


# ================================================================
# 参数配置（要求④：可重设，重设后下次扫描生效）
# ================================================================
def get_config() -> dict:
    row = db.fetch_one(f"SELECT cfg_json FROM {_CONFIG_TABLE} WHERE key='launch'")
    if not row or not row.get("cfg_json"):
        return dict(DEFAULTS)
    try:
        cfg = json.loads(row["cfg_json"])
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULTS)
    out = dict(DEFAULTS)
    out.update(cfg)
    return out


def set_config(cfg: dict) -> dict:
    """校验并落库；未知键忽略、缺省键用 DEFAULTS 补齐（防把系统改坏）。"""
    clean = {k: v for k, v in cfg.items() if k in DEFAULTS}
    ensure_tables()
    db.upsert(_CONFIG_TABLE, {
        "key": "launch",
        "cfg_json": json.dumps(clean, ensure_ascii=False),
        "updated_at": _now(),
    }, conflict_columns=["key"])
    return get_config()


def _params_hash(cfg: dict) -> str:
    import hashlib
    s = json.dumps({k: cfg[k] for k in sorted(cfg) if k not in ("enabled", "max_results")},
                   sort_keys=True, ensure_ascii=False)
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:10]


def _now() -> str:
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


# ================================================================
# 数据加载（与数据源解耦 ⇒ 未来换 pack 只改这里）
# ================================================================
def load_bars(code: str, tail: int = None) -> list:
    """读单只 bars（升序）。

    ★★ 2026-10-18（egress 治理）：新增 `tail` —— 只取**最近 N 根**。
      背景（pg_stat_statements 实测）：`SELECT ... WHERE code=$1 ORDER BY date ASC`
      累计 9,463 次 / 696 万行，其中本模块的每日扫描每次要把 995 只票的**全部历史**
      （每只 ~740 根）读一遍 ≈ 45MB/天，而形态判定只需要 `pullback+base+余量` ≈ 120 根。
      ⇒ 扫描路径改 `tail`（走 (code,date) 索引倒序取 N 再反转），日流量降至 ~8MB/天。
      ⚠️ 观察追踪（update_watch_returns）需要**从 launch_date 起的完整段** ⇒ 不传 tail。
    """
    if tail and int(tail) > 0:
        rows = db.fetch(
            "SELECT date, open, high, low, close, volume FROM backtest_prices "
            "WHERE code = %s ORDER BY date DESC LIMIT %s", (code, int(tail))) or []
        rows = list(reversed(rows))
    else:
        rows = db.fetch(
            "SELECT date, open, high, low, close, volume FROM backtest_prices "
            "WHERE code = %s ORDER BY date ASC", (code,)) or []
    bars, prev = [], None
    for r in rows:
        cl, op, hi, lo = float(r["close"] or 0), float(r["open"] or 0), \
            float(r["high"] or 0), float(r["low"] or 0)
        if cl <= 0 or op <= 0 or hi <= 0 or lo <= 0:
            prev = cl if cl > 0 else prev
            continue
        pct = (cl / prev - 1) * 100 if prev else None
        bars.append({"date": str(r["date"])[:10], "open": op, "high": hi, "low": lo,
                     "close": cl, "volume": float(r["volume"] or 0), "pct": pct})
        prev = cl
    return bars


def load_bars_bulk(tail: int = 140, start: str = None) -> dict:
    """**一次查询**取全池最近 `tail` 根 → {code: [bars]}。

    ★★ 2026-10-18（egress + 延迟双治理）：原先 scan_latest 对 995 只票**逐只查**
      （995 次往返，实测 **178 秒**）；改为一条 `ROW_NUMBER() OVER (PARTITION BY code)`
      ⇒ **1 次往返、秒级**，且只取窗口内的行（egress 与逐只尾部读相同 ≈8MB）。
      服务端扫描量由 `date >= start` 限制（默认按最新日回退 400 自然日，覆盖 120 交易日 + 长假）。
    """
    if start is None:
        r = db.fetch_one("SELECT MAX(date) AS d FROM backtest_prices")
        latest = str((r or {}).get("d") or "")[:10]
        if not latest:
            return {}
        from datetime import datetime, timedelta
        try:
            start = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
        except ValueError:
            start = latest[:4] + "-01-01"
    rows = db.fetch("""
        SELECT code, date, open, high, low, close, volume FROM (
            SELECT code, date, open, high, low, close, volume,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
            FROM backtest_prices WHERE date >= %s
        ) t WHERE rn <= %s ORDER BY code, date ASC""", (start, int(tail))) or []
    out = {}
    for r in rows:
        cl, op, hi, lo = float(r["close"] or 0), float(r["open"] or 0), \
            float(r["high"] or 0), float(r["low"] or 0)
        if cl <= 0 or op <= 0 or hi <= 0 or lo <= 0:
            continue
        out.setdefault(r["code"], []).append(
            {"date": str(r["date"])[:10], "open": op, "high": hi, "low": lo,
             "close": cl, "volume": float(r["volume"] or 0), "pct": None})
    for bars in out.values():
        prev = None
        for b in bars:
            if prev:
                b["pct"] = (b["close"] / prev - 1) * 100
            prev = b["close"]
    return out


def all_codes() -> list:
    rows = db.fetch("SELECT DISTINCT code FROM backtest_prices ORDER BY code")
    return [r["code"] for r in (rows or [])]


def latest_trading_date() -> str:
    """最近「覆盖完整」的交易日。

    ★ 不能直接用 MAX(date)：日批回填是逐股进行的，盘中/部分回填时 MAX 可能是
       **当天**（只回填了 ~150 只，其余还没补）⇒ 用它当扫描日会得到 0 信号。
       改为取「近 5 个日期里，覆盖 ≥ 800 只」的最新一日（全池 ≈995 只 ⇒ 阈值 80%）。
    """
    rows = db.fetch("SELECT date, COUNT(DISTINCT code) AS n FROM backtest_prices "
                    "GROUP BY date ORDER BY date DESC LIMIT 5") or []
    for r in rows:
        if (r["n"] or 0) >= 800:
            return str(r["date"])[:10]
    r = db.fetch_one("SELECT MAX(date) AS d FROM backtest_prices")
    return str((r or {}).get("d") or "")[:10]


# ================================================================
# 形态扫描（与 scripts/pattern_launch_check.scan_stock 逐条同口径）
# ================================================================
def scan_stock(bars: list, cfg: dict) -> list:
    n = len(bars)
    pd, vb = cfg["pullback_days"], cfg["vol_base_days"]
    need = pd + vb
    sigs, last = [], -10 ** 9
    for t in range(need, n):
        if t - last < cfg["cooldown"]:
            continue
        a = bars[t - pd:t]
        b = bars[t - pd - vb:t - pd]
        vol_a = statistics.mean(x["volume"] for x in a)
        vol_b = statistics.mean(x["volume"] for x in b)
        if vol_b <= 0 or vol_a <= 0:
            continue
        if vol_a / vol_b > cfg["vol_shrink"]:
            continue
        ret_a = bars[t - 1]["close"] / bars[t - pd - 1]["close"] - 1
        if not (cfg["pullback_min"] <= ret_a <= cfg["pullback_max"]):
            continue
        high_b = max(x["high"] for x in b)
        if max(x["high"] for x in a) > high_b * (1 + cfg["below_high_tol"]):
            continue
        pct_t = bars[t]["pct"] if bars[t]["pct"] is not None else \
            (bars[t]["close"] / bars[t - 1]["close"] - 1) * 100
        if pct_t < cfg["launch_pct"]:
            continue
        if bars[t]["volume"] < cfg["vol_surge"] * vol_a:
            continue
        if cfg["breakout"] and bars[t]["close"] <= max(x["high"] for x in a):
            continue
        sigs.append({"date": bars[t]["date"], "idx": t, "vol_ratio": vol_a / vol_b,
                     "pullback_ret": ret_a, "launch_pct": pct_t,
                     "surge": bars[t]["volume"] / vol_a, "high_a": max(x["high"] for x in a),
                     "price": bars[t]["close"]})
        last = t
    return sigs


# ================================================================
# 形态质量分（0~100，**非预测力分**——只表达量价形态的"干净程度"）
# ================================================================
def quality_score(s: dict) -> int:
    shrink_q = max(0.0, min(1.0, 1 - s["vol_ratio"] / 0.6))          # 越缩量越高（上限到 0.6）
    surge_q = max(0.0, min(1.0, s["surge"] / 4.0))                    # 放量 4× 封顶
    launch_q = max(0.0, min(1.0, 1 - abs(s["launch_pct"] - 7.0) / 7.0))  # 7% 附近最理想
    breakout_q = max(0.0, min(1.0, (s["price"] / s["high_a"] - 1) / 0.05))  # 突破幅度
    score = 100 * (0.30 * shrink_q + 0.25 * surge_q + 0.25 * launch_q + 0.20 * breakout_q)
    return int(round(max(0.0, min(100.0, score))))


def _board(code: str) -> str:
    if code.startswith(("688", "689")):
        return "科创"
    if code.startswith(("300", "301", "302")):
        return "创业"
    if code.startswith(("0", "3")):
        return "主板·深"
    return "主板·沪"


def _industry(code: str) -> str:
    try:
        r = db.fetch_one("SELECT main_industry_code FROM stock_industry WHERE code = %s",
                         (code,))
        return (r or {}).get("main_industry_code") or ""
    except Exception:
        return ""


def _name_industry(code: str) -> tuple:
    """一次查询拿 (名称, 板块)。★ 2026-10-20：`scan_latest` 原先**根本不产出 name**
    ⇒ `persist_scan` 写的是 `it.get("name","")`，落库/前端/日志全是空名
    （实测 603013 亚普股份 显示为空）。只在**命中时**查（每日 0~2 只），无 egress 压力。
    """
    try:
        r = db.fetch_one(
            "SELECT name, main_industry_code FROM stock_industry WHERE code = %s",
            (code,))
        r = r or {}
        return (r.get("name") or "", r.get("main_industry_code") or "")
    except Exception:
        return ("", "")


# ================================================================
# 每日扫描（主入口）
# ================================================================
def scan_latest(date: str = None) -> dict:
    cfg = get_config()
    day = date or latest_trading_date()
    if not day:
        return {"available": False, "date": None, "items": [], "note": "backtest_prices 为空"}
    need = cfg["pullback_days"] + cfg["vol_base_days"]
    # ★ 2026-10-18 egress：只读最近 need+20 根（扫描只认"最后一根是启动日"）；
    #   仅当显式指定了一个**非最新**的历史日时才回退全量读（管理员补扫场景，罕见）。
    _tail = None if (date and str(date)[:10] != str(latest_trading_date())[:10]) else need + 20
    items = []
    # ★ 最新日的常规扫描走**一次批量读**（1 次往返）；只有补扫历史日才逐只全量读。
    _bulk = load_bars_bulk(tail=(need + 20)) if _tail else None
    _codes = list(_bulk.keys()) if _bulk is not None else all_codes()
    for code in _codes:
        bars = _bulk[code] if _bulk is not None else load_bars(code)
        if len(bars) < need + 1:
            continue
        for s in scan_stock(bars, cfg):
            if s["date"] != day:
                continue
            score = quality_score(s)
            _nm, _ind = _name_industry(code)
            items.append({
                "code": code, "name": _nm,
                "launch_date": s["date"], "launch_pct": round(s["launch_pct"], 2),
                "vol_ratio": round(s["vol_ratio"], 3), "pullback_ret": round(s["pullback_ret"], 4),
                "surge": round(s["surge"], 2), "price": s["price"], "board": _board(code),
                "industry": _ind, "score": score,
                "score_json": json.dumps({"shrink_q": round(max(0.0, min(1.0, 1 - s["vol_ratio"] / 0.6)), 2),
                                          "surge_q": round(max(0.0, min(1.0, s["surge"] / 4.0)), 2),
                                          "launch_q": round(max(0.0, min(1.0, 1 - abs(s["launch_pct"] - 7) / 7)), 2),
                                          "breakout_q": round(max(0.0, min(1.0, (s["price"] / s["high_a"] - 1) / 0.05)), 2)},
                                         ensure_ascii=False),
            })
    items.sort(key=lambda x: -x["score"])
    items = items[:cfg["max_results"]]
    return {"available": True, "date": day, "params_hash": _params_hash(cfg),
            "params": {k: cfg[k] for k in ("launch_pct", "vol_shrink", "pullback_days",
                                           "vol_base_days", "vol_surge", "target")},
            "n": len(items), "items": items}


def persist_scan(result: dict) -> int:
    """把 scan_latest 结果落库（幂等）。返回写入行数。

    ★ 2026-10-26（用户问"形态命中加入的难点"）：**0 命中也要留痕**。
      旧实现在 `not items` 时直接 return 0、什么都不写 ⇒ `pattern_scan_daily` 里
      没有当日行，事实宽表无法区分「扫过但没中」与「当日根本没扫」（两者都成了 0）。
      现在把扫描日写进 `sync_meta['pattern_scan'].note`，事实表据此决定
      `pattern_hit` 写 0 还是 NULL（无数据 ≠ 未命中）。
    """
    cfg = get_config()
    h = _params_hash(cfg)
    day = result.get("date")
    if day:
        try:
            from app import sync_meta
            sync_meta.touch("pattern_scan", rows=len(result.get("items") or []),
                            note=str(day)[:10])
        except Exception as e:
            print(f"[pattern] 扫描留痕写入失败（不影响扫描）: {str(e)[:80]}")
    if not day or not result.get("items"):
        return 0
    n = 0
    for it in result["items"]:
        db.upsert(_SCAN_TABLE, {
            "date": day, "code": it["code"], "name": it.get("name", ""),
            "params_hash": h, "launch_date": it["launch_date"],
            "launch_pct": it["launch_pct"], "vol_ratio": it["vol_ratio"],
            "pullback_ret": it["pullback_ret"], "surge": it["surge"],
            "price": it["price"], "board": it["board"], "industry": it["industry"],
            "score": it["score"], "score_json": it.get("score_json", ""),
            "created_at": _now(),
        }, conflict_columns=["date", "code"])
        n += 1
    return n


def get_scan(date: str = None) -> dict:
    day = date or latest_trading_date()
    rows = db.fetch(f"SELECT * FROM {_SCAN_TABLE} WHERE date = %s ORDER BY score DESC",
                    (day,)) or []
    items = []
    for r in rows:
        items.append({"code": r["code"], "name": r["name"], "launch_date": r["launch_date"],
                      "launch_pct": r["launch_pct"], "vol_ratio": r["vol_ratio"],
                      "pullback_ret": r["pullback_ret"], "surge": r["surge"],
                      "price": r["price"], "board": r["board"], "industry": r["industry"],
                      "score": r["score"]})
    return {"date": day, "n": len(items), "items": items}


# ================================================================
# 观察清单（要求③：持续观察 + 记录后续变化）
# ================================================================
def watch_add(code: str, note: str = "", name: str = "") -> dict:
    cfg = get_config()
    bars = load_bars(code)
    if len(bars) < cfg["pullback_days"] + cfg["vol_base_days"] + 2:
        return {"ok": False, "error": "数据不足，无法定位启动日"}
    sigs = scan_stock(bars, cfg)
    if not sigs:
        return {"ok": False, "error": "该股当前不符合形态（最近启动日不在范围内）"}
    s = sigs[-1]                                   # 最近一次启动
    db.upsert(_WATCH_TABLE, {
        "code": code, "name": name, "added_date": _now()[:10],
        "launch_date": s["date"], "params_json": json.dumps(cfg, ensure_ascii=False),
        "first_price": s["price"], "status": "观察中", "note": note or "",
        "created_at": _now(), "updated_at": _now(),
    }, conflict_columns=["code"])
    update_watch_returns([code])
    return {"ok": True, "launch_date": s["date"], "first_price": s["price"]}


def watch_update(code: str, status: str = None, note: str = None) -> bool:
    sets, vals = [], []
    if status is not None:
        sets.append("status = %s")
        vals.append(status)
    if note is not None:
        sets.append("note = %s")
        vals.append(note)
    if not sets:
        return True
    sets.append("updated_at = %s")
    vals.append(_now())
    vals.append(code)
    db.execute(f"UPDATE {_WATCH_TABLE} SET {', '.join(sets)} WHERE code = %s", vals)
    return True


def watch_remove(code: str) -> bool:
    db.execute(f"DELETE FROM {_WATCH_TABLE} WHERE code = %s", (code,))
    return True


def update_watch_returns(codes: list = None) -> int:
    """更新观察中的票的后续变化（T+N 收益 / 最大涨幅 / 最大回撤，相对 T+1 开盘入场价）。

    每日由日批调用；也支持刚加观察时即时回填一次。
    """
    cfg = get_config()
    fwds = cfg["fwd_watch"]
    if codes is None:
        rows = db.fetch(f"SELECT code, launch_date FROM {_WATCH_TABLE} WHERE status != '失效'")
        codes = [r["code"] for r in (rows or [])]
    n = 0
    for code in codes:
        row = db.fetch_one(f"SELECT launch_date FROM {_WATCH_TABLE} WHERE code = %s", (code,))
        if not row or not row.get("launch_date"):
            continue
        bars = load_bars(code)
        idx = next((i for i, b in enumerate(bars) if b["date"] == row["launch_date"]), None)
        if idx is None or idx + 1 >= len(bars):
            continue
        entry = bars[idx + 1]["open"]
        if entry <= 0:
            continue
        rets, max_up, max_dd = {}, 0.0, 0.0
        for f in fwds:
            j = idx + 1 + f
            if j < len(bars):
                rets[str(f)] = round(bars[j]["close"] / entry - 1, 4)
        seg = bars[idx + 1:]
        if seg:
            max_up = round(max(x["high"] for x in seg) / entry - 1, 4)
            max_dd = round(min(x["low"] for x in seg) / entry - 1, 4)
        last = bars[-1]
        db.execute(f"UPDATE {_WATCH_TABLE} SET ret_json = %s, max_up = %s, max_dd = %s, "
                   f"last_price = %s, last_date = %s, updated_at = %s WHERE code = %s",
                   (json.dumps(rets, ensure_ascii=False), max_up, max_dd,
                    last["close"], last["date"], _now(), code))
        n += 1
    return n


def get_watch() -> list:
    rows = db.fetch(f"SELECT * FROM {_WATCH_TABLE} ORDER BY added_date DESC, code") or []
    out = []
    for r in rows:
        try:
            rets = json.loads(r["ret_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            rets = {}
        out.append({"code": r["code"], "name": r["name"], "added_date": r["added_date"],
                    "launch_date": r["launch_date"], "first_price": r["first_price"],
                    "last_price": r["last_price"], "last_date": r["last_date"],
                    "rets": rets, "max_up": r["max_up"], "max_dd": r["max_dd"],
                    "status": r["status"], "note": r["note"] or ""})
    return out


def highlight_codes() -> dict:
    """供前端醒目标注：当日扫描命中的 code 集合（轻量）。"""
    day = latest_trading_date()
    rows = db.fetch(f"SELECT code FROM {_SCAN_TABLE} WHERE date = %s", (day,)) or []
    return {"date": day, "codes": [r["code"] for r in rows]}
