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
        # ★ 2026-10-10（P1-2）：个股维度也要按 code 查（详情页标签）⇒ 补 code 索引，
        #   否则每次详情页都要全表扫（含 seats_json 大字段所在的页）。
        db.execute("CREATE INDEX IF NOT EXISTS idx_lhb_code ON lhb_history (code)")
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS lhb_history (
                code TEXT NOT NULL, name TEXT, date TEXT NOT NULL,
                net_buy REAL, buy_total REAL, sell_total REAL,
                quote_change REAL, up_reason TEXT, concepts TEXT,
                turnover_ratio REAL, seats_json TEXT,
                circ_mktcap REAL,
                UNIQUE (code, date)
            )
        """)
    # ★★ 2026-10-11：增列 `circ_mktcap`（**上榜日流通市值**，元）—— 用户："按你建议来"
    #   （本项 = 让风险标签覆盖科创板）。为什么必须存它：
    #     · 强度 = 净买额 / **流通市值** ⇒ 分母是唯一缺的东西；
    #     · 原来只能"本地包收盘 × market_snapshot 股本"现算 ⇒ 包**无科创板/北交所**
    #       ⇒ 这些个股**静默打不出标签**（实测 6 只净卖样本仅 4 只可打）；
    #     · 而 zzshare `lhb_list` **自带 `circ_price`（当日流通市值）** ⇒ 一行一个数，
    #       与预登记口径**数学等价**（收盘×股本 ≡ 流通市值），且**不依赖包/快照**
    #       ⇒ 全板块（含 688/北交所）可分档，还省掉每请求一次包读取。
    #   ⚠️ 旧行该列为 NULL ⇒ 读取侧保留"包收盘 × 股本"回退（见 `_event_bucket`）。
    try:
        db.execute("ALTER TABLE lhb_history ADD COLUMN IF NOT EXISTS "
                   "circ_mktcap DOUBLE PRECISION")
    except Exception:
        try:
            db.execute("ALTER TABLE lhb_history ADD COLUMN circ_mktcap REAL")
        except Exception:
            pass


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
                # ★ 2026-10-11：源自带**上榜日流通市值**（`circ_price`，元）——
                #   净买强度的分母（占流通市值 %），见 ensure_table 处的注释。
                "circ_mktcap": float(it.get("circ_price") or 0) or None,
            })
        except (TypeError, ValueError):
            continue
    return [x for x in out if len(x["code"]) == 6]


def fetch_detail_seats(date: str, code: str) -> list | None:
    """单股单日席位明细 → [{trader_name, buy_amount, sell_amount, youzi_icon}]。"""
    try:
        resp = get_api().lhb_detail(date, code)
        if not isinstance(resp, dict):
            return None
        # ★ 2026-10-24（BK-13）：席位可能在顶层 `traders` 或 `detail.traders`——
        #   旧实现只认顶层（且 `d` 是死变量）⇒ 嵌套形态的席位**静默永不落库**。
        traders = resp.get("traders") or (resp.get("detail") or {}).get("traders")
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
                        turnover_ratio, seats_json, circ_mktcap)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (code, date) DO UPDATE SET
                        name=EXCLUDED.name, net_buy=EXCLUDED.net_buy,
                        buy_total=EXCLUDED.buy_total, sell_total=EXCLUDED.sell_total,
                        quote_change=EXCLUDED.quote_change, up_reason=EXCLUDED.up_reason,
                        concepts=EXCLUDED.concepts, turnover_ratio=EXCLUDED.turnover_ratio,
                        circ_mktcap=COALESCE(EXCLUDED.circ_mktcap, lhb_history.circ_mktcap),
                        seats_json=COALESCE(EXCLUDED.seats_json, lhb_history.seats_json)
                """, (r["code"], r["name"], r["date"], r["net_buy"], r.get("buy_total"),
                      r.get("sell_total"), r["quote_change"], r["up_reason"],
                      r["concepts"], r["turnover_ratio"], seats_json,
                      r.get("circ_mktcap")))
            else:
                # ★ 2026-10-24（BK-13）：INSERT OR REPLACE 是**整行替换**——seats_json/
                #   circ_mktcap 为 None 时会抹掉已有历史值（PG 分支有 COALESCE 保护）。
                #   sqlite ≥3.24 支持 ON CONFLICT DO UPDATE（表已有 UNIQUE(code,date)），
                #   与 PG 分支同语义。
                db.execute("""
                    INSERT INTO lhb_history (code, name, date, net_buy,
                        buy_total, sell_total, quote_change, up_reason, concepts,
                        turnover_ratio, seats_json, circ_mktcap)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (code, date) DO UPDATE SET
                        name=EXCLUDED.name, net_buy=EXCLUDED.net_buy,
                        buy_total=EXCLUDED.buy_total, sell_total=EXCLUDED.sell_total,
                        quote_change=EXCLUDED.quote_change, up_reason=EXCLUDED.up_reason,
                        concepts=EXCLUDED.concepts, turnover_ratio=EXCLUDED.turnover_ratio,
                        circ_mktcap=COALESCE(EXCLUDED.circ_mktcap, lhb_history.circ_mktcap),
                        seats_json=COALESCE(EXCLUDED.seats_json, lhb_history.seats_json)
                """, (r["code"], r["name"], r["date"], r["net_buy"], r.get("buy_total"),
                      r.get("sell_total"), r["quote_change"], r["up_reason"],
                      r["concepts"], r["turnover_ratio"], seats_json,
                      r.get("circ_mktcap")))
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


# ── 净买强度分档：口径与预登记脚本**逐字同源**（2026-10-11，用户："先做 1"）────────
# ★★ 为什么加：P1-2 的标签只是"上榜次数/净买合计"这类**事实**；而
#   `scripts/lhb_edge_check.py` 的 **v2 定案（PASS，2026-10-11）** 给出了可用的方向性结论：
#     big_buy  T+5 **+0.53%**（胜率 44.9%，<50%）   ← 弱
#     big_sell T+5 **−3.76%**（胜率 32.3%）         ← 强
#     主判定 big_buy − big_sell = **+4.29pct**（按上榜日聚类 bootstrap P=0.0000，
#     95%CI [+2.24,+6.53]，96 个聚类日），**四档单调**、T+10 同向 +5.26pct。
#   ⇒ 脚本头原话：「**信息主要在空头侧**：本信号更像**风险/回避标签**…
#      **不宜当买入信号**」⇒ 生产侧**只做空头提示**（对 big_buy 不提示，避免暗示可买）。
# ★ 阈值 `1.0`（占流通市值 %）与四档边界**一字不改** —— 预登记纪律：改判据必须在脚本头
#   新开一节并注明日期；本模块只**复用**该口径（`bucket_of` 与脚本逐字一致，有断言）。
LHB_BIG = 1.0
LHB_BUCKET_CN = {"big_buy": "大额净买", "small_buy": "小幅净买",
                 "small_sell": "小幅净卖", "big_sell": "大额净卖"}


def strength_pct(net_buy, close, float_shares):
    """净买强度（占流通市值 %）= 净买额 /（上榜日收盘 × 流通股本）× 100。

    与 `scripts/lhb_edge_check.py`【研究对象】同一口径；缺任一输入 ⇒ None（**不猜**）。
    """
    try:
        nb, px, fs = float(net_buy or 0), float(close or 0), float(float_shares or 0)
    except (TypeError, ValueError):
        return None
    if px <= 0 or fs <= 0:
        return None
    return nb / (px * fs) * 100.0


def strength_from_mktcap(net_buy, circ_mktcap):
    """净买强度（占流通市值 %）——**直接用它**（`lhb_history.circ_mktcap`，源自带上榜日
    流通市值）。

    ★ 与 `strength_pct`（收盘 × 流通股本）**数学等价**（收盘×股本 ≡ 流通市值），
      但**不依赖本地包/行情快照** ⇒ 科创板/北交所个股同样可分档
      （2026-10-11 新增，用户："按你建议来"）。
    """
    try:
        nb, mc = float(net_buy or 0), float(circ_mktcap or 0)
    except (TypeError, ValueError):
        return None
    if mc <= 0:
        return None
    return nb / mc * 100.0


def bucket_of(strength):
    """四档（与 `scripts/lhb_edge_check.py::bucket_of` **逐字同源**，BIG=1.0）。"""
    if strength is None:
        return None
    if strength > LHB_BIG:
        return "big_buy"
    if strength > 0:
        return "small_buy"
    if strength > -LHB_BIG:
        return "small_sell"
    return "big_sell"


def _event_bucket(code: str, date_str: str, net_buy, circ_mktcap=None):
    """单事件分档。

    ★ **首选 `circ_mktcap`**（`lhb_history.circ_mktcap` = 源自带的**上榜日流通市值**）：
      一行一个数、**零额外取数**、且**全板块可用**（含科创板 688 / 北交所）——
      2026-10-11 起新写入的行都有；这是"让风险标签覆盖科创板"的落点。
    ★ 旧行（该列为 NULL，2026-10-11 之前入库的）回退：本地包收盘 × `market_snapshot`
      股本（`flow.get_float_shares_from_snapshot`，30min 进程缓存）—— 那条路**包无 688/
      北交所** ⇒ 老票里的科创板仍可能打不出标签（会随日批新写入自然变好，亦可回填）。
    ★ 两路都缺 ⇒ None —— **不猜、也不降级到别的口径**：换口径（如用涨跌幅代替强度）
      就等于"用未经检验的指标下方向性结论"，违反项目红线（信号须经预登记背书）。
    """
    try:
        if circ_mktcap:
            return bucket_of(strength_from_mktcap(net_buy, circ_mktcap))
        from app.scoring.kline_cache import get_cached_klines
        close = None
        for b in (get_cached_klines(code) or []):
            if str(b.get("date"))[:10] == str(date_str)[:10]:
                close = b.get("close")
                break
        if not close:
            return None
        from app.mainforce.flow import get_float_shares_from_snapshot
        fs = (get_float_shares_from_snapshot() or {}).get(code)
        return bucket_of(strength_pct(net_buy, close, fs))
    except Exception as e:
        print(f"[lhb] 强度分档失败 {code} {date_str}: {str(e)[:60]}")
        return None


def recent_summary(code: str, days: int = 10) -> dict:
    """近 `days` 个自然日的上榜汇总（供个股详情页的「候选信号 / 风险」标签）。

    ★ 2026-10-10（P1-2，把纯归档的龙虎榜**接成候选信号**的第一步）：
      · **窄列读取** —— 详情页每次打开都会调它，`SELECT *`（`load_lhb` 的做法）会把
        `seats_json` 这类大字段整份拖回来，纯烧 egress（本项目的老账）。
      · 只做**展示口径**：**不进决策链**（不改分、不参与排序、不拦闸门）——
        项目红线：信号未经预测力检验背书不得进决策链；检验见 `scripts/lhb_edge_check.py`
        （预登记 + 接月度日批）。
    ★ 2026-10-11：按 v2 定案补**逐事件强度分档** + **空头侧风险标记**（见上方常量注释）。
      成本可控：取价/股本**只在有上榜记录时**发生（最多 5 次，包内取价零 egress）
      ⇒ 绝大多数个股（无上榜）成本不变。
    返回 {"n","net","net_cn","last","summary_cn",
          "recent":[{date, net_buy, chg, bucket}], "buckets":{分档:次数},
          "last_bucket","last_bucket_cn","risk","risk_cn","risk_why"}
      · `summary_cn` / `risk_cn` / `risk_why` 是**唯一事实源**（前端直接渲染，不自行推导）。
    """
    ensure_table()
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d")
    _empty = {"n": 0, "net": 0.0, "net_cn": "", "last": "", "summary_cn": "",
              "recent": [], "buckets": {}, "last_bucket": None,
              "last_bucket_cn": "", "risk": False, "risk_cn": "", "risk_why": ""}
    try:
        rows = db.fetch("SELECT date, net_buy, quote_change, circ_mktcap FROM lhb_history "
                        "WHERE code = %s AND date >= %s ORDER BY date DESC",
                        (code, start)) or []
    except Exception as e:
        print(f"[lhb] recent_summary 读取失败 {code}: {e}")
        return dict(_empty)
    net = 0.0
    recent = []
    for r in rows:
        v = float(r.get("net_buy") or 0)
        net += v
        if len(recent) < 5:
            recent.append({"date": str(r.get("date"))[:10], "net_buy": v,
                           "chg": r.get("quote_change"),
                           "circ_mktcap": r.get("circ_mktcap")})
    buckets = {}
    for x in recent:
        x["bucket"] = _event_bucket(code, x["date"], x["net_buy"], x.get("circ_mktcap"))
        if x["bucket"]:
            buckets[x["bucket"]] = buckets.get(x["bucket"], 0) + 1
    last = (str(rows[0].get("date"))[:10] if rows else "")
    last_bucket = (recent[0].get("bucket") if recent else None)
    net_cn = f"{net / 1e8:+.2f}亿" if rows else ""
    summary_cn = ""
    if rows:
        summary_cn = (f"近 {days} 日上榜 {len(rows)} 次，净买合计 {net_cn}，"
                      f"最近 {last}"
                      + (f"（{LHB_BUCKET_CN.get(last_bucket or '', '')}）" if last_bucket else ""))
    # ★ 风险标记 = **最近一次上榜是「大额净卖」**（不是"窗口内出现过"——
    #   v2 定案检验的是**事件后 T+5**，十几天前的事件早已过期，不可拿来警示当下）。
    risk = last_bucket == "big_sell"
    risk_cn = "龙虎榜风险：最近上榜为大额净卖" if risk else ""
    risk_why = (
        "净卖额占流通市值 >1%（口径与 `scripts/lhb_edge_check.py` 预登记一致）。"
        "该脚本 v2 定案（2026-10-11；样本 7,428 条 / 2026-05-06~09-24；覆盖率 95.4%）："
        "大额净卖后 T+5 均值 **−3.76%**、胜率 **32.3%**（对比大额净买 +0.53%/44.9%），"
        "四档单调、diff 4.29pct（按上榜日聚类 bootstrap P=0.0000）"
        "⇒ **只作风险/回避参考，不是买入信号**；未扣成本、单一市况、仅 5 个月样本。"
    ) if risk else ""
    return {"n": len(rows), "net": net, "net_cn": net_cn, "last": last,
            "summary_cn": summary_cn, "recent": recent, "buckets": buckets,
            "last_bucket": last_bucket,
            "last_bucket_cn": LHB_BUCKET_CN.get(last_bucket or "", ""),
            "risk": risk, "risk_cn": risk_cn, "risk_why": risk_why}


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    print(backfill_days(days=days))
