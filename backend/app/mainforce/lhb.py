# -*- coding: utf-8 -*-
from __future__ import annotations  # 兼容 Python 3.9（Render/Docker）：允许 -> list | None 注解
"""
================================================================================
【文件作用】龙虎榜数据层（zzshare 源）—— 主力行为的"实名制"补强
================================================================================

数据源：zzshare DataApi（低频补充源原则，匿名可用；接口实测 2026-09-05）
  lhb_list(date)            当日全榜：净买额/涨跌幅/换手/上榜原因/题材
  lhb_detail(date, code)    单股席位明细：买卖前五席位名称/金额/游资标识
  lhb_stock_history(code)   个股历史榜记录
  lhb_trader_history(...)   席位历史（游资跟踪，暂未落库）

表：lhb_history(code, name, date, net_buy, buy_total, sell_total, quote_change,
                up_reason, concepts, turnover_ratio, seats_json, created_at)
  UNIQUE(code, date)。seats_json 仅池内个股增量富化（detail 逐股调用慢）。

调度：lhb_refresh_loop 每日 17:45（主力状态 17:30 之后）。
用途（PLAN_NEXT_PHASE P1-4）：
  - 龙虎榜净买 + 筹码低位 = 吸筹确认器（观察清单：重叠 ≥30 例上线）
  - 机构席位净买入持续性 vs mainflow 交叉验证
  - 撤退提醒上下文（高位股上榜卖出 → 提前预警）
================================================================================
"""

import json
import time
from datetime import datetime, timedelta

from app.database import db
from app.zzshare_client import get_api


def ensure_table() -> None:
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS lhb_history (
                id BIGSERIAL PRIMARY KEY,
                code VARCHAR(12) NOT NULL,
                name VARCHAR(32),
                date DATE NOT NULL,
                net_buy BIGINT,
                buy_total BIGINT,
                sell_total BIGINT,
                quote_change DOUBLE PRECISION,
                up_reason TEXT,
                concepts TEXT,
                turnover_ratio DOUBLE PRECISION,
                seats_json JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_lhb UNIQUE (code, date)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_lhb_date ON lhb_history (date)")
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS lhb_history (
                code TEXT NOT NULL, name TEXT, date TEXT NOT NULL,
                net_buy REAL, buy_total REAL, sell_total REAL,
                quote_change REAL, up_reason TEXT, concepts TEXT,
                turnover_ratio REAL, seats_json TEXT,
                UNIQUE (code, date)
            )
        """)


def _unwrap(resp):
    """zzshare 返回解包：{data: {items/list/...}} 或裸列表。"""
    if isinstance(resp, dict):
        d = resp.get("data", resp)
        if isinstance(d, dict):
            return d.get("items") or d.get("list") or d
        return d
    return resp


def fetch_day_list(date: str) -> list:
    """某日龙虎榜全榜 → [{code, name, net_buy, ...}]；失败/非交易日返回 []。"""
    try:
        resp = get_api().lhb_list(date)
        items = _unwrap(resp) or []
    except Exception as e:
        print(f"[lhb] {date} 榜单拉取失败: {str(e)[:120]}")
        return []
    out = []
    for it in items:
        try:
            out.append({
                "code": str(it.get("stock_code") or "")[:6],
                "name": it.get("stock_name") or "",
                "net_buy": int(float(it.get("buy_in") or 0)),
                "quote_change": float(it.get("quote_change") or 0),
                "up_reason": it.get("up_desc") or it.get("up_reason") or "",
                "concepts": (it.get("concepts") or "")[:2000],
                "turnover_ratio": float(it.get("turnover_ratio") or 0),
            })
        except (TypeError, ValueError):
            continue
    return [x for x in out if len(x["code"]) == 6]


def fetch_detail_seats(date: str, code: str) -> list | None:
    """单股单日席位明细 → [{trader_name, buy_amount, sell_amount, youzi_icon}]。"""
    try:
        resp = get_api().lhb_detail(date, code)
        d = (resp or {}).get("detail") if isinstance(resp, dict) else None
        traders = (resp or {}).get("traders") if isinstance(resp, dict) else None
        if not traders:
            return None
        seats = []
        for t in traders:
            seats.append({
                "name": t.get("trader_name") or "",
                "buy": float(t.get("buy_amount") or 0),
                "sell": float(t.get("sell_amount") or 0),
                "youzi": bool(t.get("youzi_icon")),
                "rank": t.get("rank"),
            })
        return seats
    except Exception:
        return None


def save_rows(rows: list) -> int:
    if not rows:
        return 0
    n = 0
    for r in rows:
        seats_json = json.dumps(r["seats"], ensure_ascii=False) if r.get("seats") else None
        try:
            if db._use_postgres:
                db.execute("""
                    INSERT INTO lhb_history (code, name, date, net_buy, buy_total,
                        sell_total, quote_change, up_reason, concepts,
                        turnover_ratio, seats_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (code, date) DO UPDATE SET
                        name=EXCLUDED.name, net_buy=EXCLUDED.net_buy,
                        buy_total=EXCLUDED.buy_total, sell_total=EXCLUDED.sell_total,
                        quote_change=EXCLUDED.quote_change, up_reason=EXCLUDED.up_reason,
                        concepts=EXCLUDED.concepts, turnover_ratio=EXCLUDED.turnover_ratio,
                        seats_json=COALESCE(EXCLUDED.seats_json, lhb_history.seats_json)
                """, (r["code"], r["name"], r["date"], r["net_buy"], r.get("buy_total"),
                      r.get("sell_total"), r["quote_change"], r["up_reason"],
                      r["concepts"], r["turnover_ratio"], seats_json))
            else:
                db.execute("""
                    INSERT OR REPLACE INTO lhb_history (code, name, date, net_buy,
                        buy_total, sell_total, quote_change, up_reason, concepts,
                        turnover_ratio, seats_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (r["code"], r["name"], r["date"], r["net_buy"], r.get("buy_total"),
                      r.get("sell_total"), r["quote_change"], r["up_reason"],
                      r["concepts"], r["turnover_ratio"], seats_json))
            n += 1
        except Exception as e:
            print(f"[lhb] 写入失败 {r['code']} {r['date']}: {str(e)[:100]}")
    return n


def _pool_codes() -> set:
    try:
        rows = db.fetch("SELECT DISTINCT code FROM backtest_prices")
        return {r["code"] for r in rows}
    except Exception:
        return set()


def backfill_days(days: int = 120, detail_recent: int = 10, gap: float = 0.6) -> dict:
    """
    回填近 N 个自然日的龙虎榜（跳过空日=周末/节假日）。
    席位明细（lhb_detail，逐股慢）只对【池内个股】且近 detail_recent 天内
    的上榜记录增量富化；更早的历史明细随每日调度自然累积。
    """
    ensure_table()
    pool = _pool_codes()
    t0 = time.time()
    n_list, n_detail, days_hit = 0, 0, 0
    cutoff_detail = (datetime.now() - timedelta(days=detail_recent)).strftime("%Y-%m-%d")
    for i in range(days, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_rows = fetch_day_list(d)
        if not day_rows:
            time.sleep(gap)
            continue
        days_hit += 1
        rows = []
        for it in day_rows:
            it["date"] = d
            it["seats"] = None
            if it["code"] in pool and d >= cutoff_detail:
                seats = fetch_detail_seats(d, it["code"])
                if seats:
                    it["seats"] = seats
                    it["buy_total"] = sum(s["buy"] for s in seats)
                    it["sell_total"] = sum(s["sell"] for s in seats)
                    n_detail += 1
                time.sleep(gap)
            rows.append(it)
        n_list += save_rows(rows)
        time.sleep(gap)
        if days_hit % 10 == 0:
            print(f"[lhb] {d} 累计榜行 {n_list}，席位明细 {n_detail} ({time.time()-t0:.0f}s)")
    return {"days": days_hit, "rows": n_list, "details": n_detail,
            "seconds": round(time.time() - t0, 1)}


def load_lhb(code: str = None, start: str = None, end: str = None) -> list:
    """读龙虎榜记录（升序）。code 可选；start/end 形如 '2026-05-01'。"""
    ensure_table()
    sql = "SELECT * FROM lhb_history WHERE 1=1"
    params: list = []
    if code:
        sql += " AND code = %s"
        params.append(code)
    if start:
        sql += " AND date >= %s"
        params.append(start)
    if end:
        sql += " AND date <= %s"
        params.append(end)
    sql += " ORDER BY date ASC"
    rows = db.fetch(sql, tuple(params) if params else None)
    out = []
    for r in rows:
        seats = r.get("seats_json")
        try:
            seats = json.loads(seats) if isinstance(seats, str) else seats
        except (TypeError, ValueError):
            seats = None
        out.append({**{k: v for k, v in r.items() if k != "seats_json"},
                    "date": str(r["date"]), "seats": seats})
    return out


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    print(backfill_days(days=days))
