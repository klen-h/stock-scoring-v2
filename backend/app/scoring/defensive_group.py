"""宽防御组（方案 A：银行 ∪ 高股息代理 ∪ 低波）—— **对照用，未定参、不进判定**。

================================================================================
【为什么有这个模块】
H2' 原组只是"银行"（`name` 含"银行"），δ=0.80 也是按银行为组 in-sample 定的。
用户实测反馈：组内选出来的是"银行里评分最高的"，不是"最强的"，且工行这类大行
根本不在评分榜前 1000 ⇒ 想扩成"高股息/低波组"。

【三个硬约束（决定了本模块的形状）】
1. **系统没有股息率字段**（全库 `information_schema` 查 div/yield 为空）⇒ "高股息"
   只能用**代理口径**：行业白名单 + 大市值。真实股息率需另接数据源（未接）。
2. **市值不能用 `kline_cache.market_cap`**（实测为 0）⇒ 取 `market_snapshot`
   （腾讯 15:10 全市场快照，`float_cap` 万元）反推。
3. **不进判定**：`δ=0.80` 是银行为组的定参，扩组后必须重新定参 + 预登记；
   在那之前本组只作**对照榜**，`defensive_edge_check` 仍只看 `variant='defensive'`。

【口径（可复算，改动须留痕）】
· bank        `name` 含"银行"（全量，无市值门槛——与原 H2' 一致）
· dividend    `stock_industry.main_industry` ∈ {金融,交通运输,电力,煤炭,石油,钢铁}
              且 流通市值 ≥ 300 亿（高股息集中在这些"类债"行业）
· low_vol     流通市值 ≥ 300 亿 且 60 日年化波动率（日收益标准差 ×√252）全池最低 60 只
              （再要求低于全池中位数，避免普涨市里"低波"名不副实）
⇒ 三组并集，落 `defensive_group_daily`；日批算一次。
   **只读候选代码的 kline_data**（市值≥300亿 ∪ 银行 ≈300 只，分块 150，≈4~5MB）——
   不整表读（整表 41MB 且 PG 有 statement timeout 风险，`mainforce/state.py` 早有注释）。
   API 只读 ~137 行（≈15KB）。
================================================================================
"""
import json
import math
from datetime import datetime, timedelta, timezone

from app.database import db

_TABLE = "defensive_group_daily"
_BJ = timezone(timedelta(hours=8))

DIV_INDUSTRIES = ("金融行业", "交通运输", "电力行业", "煤炭行业", "石油行业", "钢铁行业")
DIV_MIN_CAP = 300e8          # 元
LOWVOL_MIN_CAP = 300e8
LOWVOL_TOP_N = 60
VOL_WINDOW = 61              # 60 个日收益


def ensure_table() -> None:
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            grp TEXT,
            vol60 DOUBLE PRECISION,
            mktcap DOUBLE PRECISION,
            industry TEXT,
            PRIMARY KEY (date, code)
        )
    """)


def _vol60(kline_json: str):
    """60 日年化波动率（%）；数据不足返回 None。"""
    try:
        bars = json.loads(kline_json) if isinstance(kline_json, str) else kline_json
    except (TypeError, ValueError):
        return None
    if not bars or len(bars) < 21:
        return None
    closes = [float(b.get("close") or 0) for b in bars[-(VOL_WINDOW):]]
    closes = [c for c in closes if c > 0]
    if len(closes) < 21:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))
            if closes[i - 1] > 0]
    if len(rets) < 20:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252) * 100, 2)


def _snap_maps():
    """行情快照 → ({code: 流通市值元}, {code: 名称})。

    ⚠️ `stocks` 是 **{code: {...}} 字典**（不是 list）——实测 5257 只，
       `float_cap` 单位万元（平安银行 21,734,400 万 = 2173 亿）。按 list 解析会
       得到空 map ⇒ dividend/low_vol 两组恒为 0（这个坑本节记录一次）。
    ★ 名称也直接取自快照 ⇒ 不必为了"名字含银行"去读 kline_cache 全表。
    """
    try:
        from app.flash import store as _store
        snap = _store.load_market_snapshot() or {}
        raw = snap.get("stocks")
        rows = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception as e:
        print(f"[defensive_group] 行情快照读取失败（市值/名称按空处理）: {str(e)[:80]}")
        return {}, {}
    caps, names = {}, {}
    items = rows.items() if isinstance(rows, dict) else []
    for code, s in items:
        if not isinstance(s, dict):
            continue
        code = str(code)
        nm = s.get("name")
        if nm:
            names[code] = nm
        fc = s.get("float_cap") or s.get("market_cap")
        if not fc:
            continue
        try:
            caps[code] = float(fc) * 1e4      # 万元 → 元
        except (TypeError, ValueError):
            continue
    return caps, names


def _vol_map(codes) -> dict:
    """只对**候选代码**读 kline_data（分块 150），算 60 日年化波动率。

    ★ 2026-10-25（用户："读 kline_cache 全表是之前就有还是这次新增？"）：
      第一版是 `SELECT code, name, kline_data FROM kline_cache` **整表**（41MB），
      既多烧 egress，也踩了 `state.py` 早就写过的坑——"整表一次查会 statement
      timeout ⇒ 分块 + 限列"。现在：候选 = 市值≥300亿 ∪ 银行（≈300 只），
      按 150 一只分块读 ⇒ **≈4~5MB**，语义不变（低波本来就只在这些大市值里选）。
    """
    out = {}
    lst = [c for c in codes if c]
    for i in range(0, len(lst), 150):
        chunk = lst[i:i + 150]
        ph = ",".join(["%s"] * len(chunk))
        try:
            rows = db.fetch(f"SELECT code, kline_data FROM kline_cache "
                            f"WHERE code IN ({ph})", tuple(chunk))
        except Exception as e:
            print(f"[defensive_group] K 线读取失败（块 {i // 150}）: {str(e)[:80]}")
            continue
        for r in rows or []:
            v = _vol60(r.get("kline_data"))
            if v is not None:
                out[r["code"]] = v
    return out


def build(day: str = None, apply: bool = True) -> dict:
    """算当日宽防御组并落库（幂等：只替换当日行）。"""
    ensure_table()
    day = day or datetime.now(_BJ).strftime("%Y-%m-%d")

    caps, names = _snap_maps()

    try:
        ind = {r["code"]: r.get("main_industry") or ""
               for r in db.fetch("SELECT code, main_industry FROM stock_industry")}
    except Exception as e:
        print(f"[defensive_group] 行业映射读取失败（dividend 组退化为空）: {str(e)[:80]}")
        ind = {}

    bank, div, low = [], [], []
    for code, name in names.items():
        if "银行" in name:
            bank.append(code)

    for code, cap in caps.items():
        if cap < DIV_MIN_CAP:
            continue
        if ind.get(code) in DIV_INDUSTRIES:
            div.append(code)

    # 低波：候选限定为"市值≥300亿"（∪银行，防止银行没进快照市值），再取波动最低 N 只
    cand = {c for c, cap in caps.items() if cap >= LOWVOL_MIN_CAP} | set(bank)
    vol = _vol_map(sorted(cand))
    pool = [(c, v) for c, v in vol.items() if caps.get(c, 0) >= LOWVOL_MIN_CAP]
    pool.sort(key=lambda x: x[1])
    if pool:
        vals = sorted(v for _, v in pool)
        med = vals[len(vals) // 2]
        low = [c for c, v in pool[:LOWVOL_TOP_N] if v <= med]

    recs, seen = [], set()
    for grp, codes in (("bank", bank), ("dividend_proxy", div), ("low_vol", low)):
        for c in codes:
            if c in seen:
                continue
            seen.add(c)
            recs.append({
                "date": day, "code": c, "name": names.get(c) or "",
                "grp": grp, "vol60": vol.get(c),
                "mktcap": caps.get(c), "industry": ind.get(c) or "",
            })

    summary = {"date": day, "n": len(recs), "bank": len(bank),
               "dividend_proxy": len(div), "low_vol": len(low)}
    if not apply:
        summary["rows"] = recs[:10]
        return summary

    db.execute(f"DELETE FROM {_TABLE} WHERE date = %s", (day,))
    if recs:
        db.upsert_many(_TABLE, recs, ["date", "code"])
    print(f"[defensive_group] {day} 宽防御组 {len(recs)} 只 "
          f"(bank={len(bank)} div={len(div)} low={len(low)})")
    return summary


def load(day: str = None) -> dict:
    """读最新一日宽防御组（小表，≈120 行）。"""
    ensure_table()
    if not day:
        row = db.fetch_one(f"SELECT MAX(date) AS d FROM {_TABLE}")
        day = str((row or {}).get("d") or "")
    if not day:
        return {"ok": False, "date": None, "items": [], "note": "尚无宽防御组（等日批或手动 build）"}
    rows = db.fetch(
        f"SELECT code, name, grp, vol60, mktcap, industry FROM {_TABLE} "
        f"WHERE date = %s ORDER BY grp, code", (day,))
    items = [dict(r) for r in rows or []]
    cnt = {}
    for it in items:
        cnt[it["grp"]] = cnt.get(it["grp"], 0) + 1
    # 门控态随组一起下发：前端本地实时要用它决定"要不要加成"（与官方榜同一门控源）
    gate, eq_state, eq_date = False, "", ""
    try:
        from app.backtest.eq_anchor import STATE_BEAR_SLOW, latest_state
        eq = latest_state() or {}
        eq_state = eq.get("state") or ""
        eq_date = str(eq.get("anchor_date") or "")
        gate = eq_state == STATE_BEAR_SLOW
    except Exception as e:
        print(f"[defensive_group] 等权锚读取失败（门控按 OFF）: {str(e)[:80]}")
    return {"ok": True, "date": day, "n": len(items), "counts": cnt, "items": items,
            "gate": gate, "eq_state": eq_state, "eq_date": eq_date}
