# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】经营现金流历史（按报告期）—— L3「连续失血」跟踪的数据底座
================================================================================

为什么需要：stock_finance_zz 是"一股一期"（只存最新报告期），无法判断
"连续 N 期失血"。金十 mp-api 的 cash_flow/indicator 接口按【报告期】查
全市场（一个调用 = 全部股票的一个季度），历史回填极其便宜：
  N 个季度 = 2N 个请求（cash_flow + indicator），全池覆盖。

表：stock_finance_ocf_hist(code, report_date, ocf, adjusted_profit,
                           pub_date, updated_at)，PK(code, report_date)
  - ocf              经营现金流净额（YTD 累计口径）
  - adjusted_profit  扣非净利润（可空——部分公司部分期缺失）

口径说明：
  - OCF 为累计值：Q1=Q1，中报=H1，以此类推。"连续失血" = 相邻报告期
    累计 OCF 均 <0（全年逐季失血比单季失血严重得多，天然分级）。
  - 失血判定不除以利润（避免微利噪音），profit 门槛在 l3_scanner 侧过滤。

调度：zz_finance_sync_loop（每周一 04:30）随主表一起同步。
================================================================================
"""

import time as _time
from datetime import datetime
from typing import Dict, Optional

from app.database import db

_QUARTER_ENDS = ("03-31", "06-30", "09-30", "12-31")


def ensure_hist_table() -> None:
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS stock_finance_ocf_hist (
                code VARCHAR(12) NOT NULL,
                report_date DATE NOT NULL,
                ocf DOUBLE PRECISION,
                adjusted_profit DOUBLE PRECISION,
                pub_date TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_ocf_hist UNIQUE (code, report_date)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_ocf_hist_code "
                   "ON stock_finance_ocf_hist (code)")
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS stock_finance_ocf_hist (
                code TEXT NOT NULL, report_date TEXT NOT NULL,
                ocf REAL, adjusted_profit REAL, pub_date TEXT,
                UNIQUE (code, report_date)
            )
        """)


def quarter_ends(n: int = 4) -> list:
    """最近 n 个已结束的季度末日期（升序，含可能的当季——数据未出时为空表）。"""
    today = datetime.now().date()
    ends = []
    y = today.year
    while len(ends) < n:
        for q in reversed(_QUARTER_ENDS):
            d = datetime.strptime(f"{y}-{q}", "%Y-%m-%d").date()
            if d < today:
                ends.insert(0, d.isoformat())
                if len(ends) == n:
                    break
        y -= 1
    return ends


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def sync_ocf_history(quarters: int = 4) -> dict:
    """回填/刷新最近 N 个报告期的全市场 OCF + 扣非（幂等 upsert）。"""
    ensure_hist_table()
    from app.zzshare_client import get_api
    api = get_api()
    total = 0
    for date in quarter_ends(quarters):
        try:
            cf = api.finance_cash_flow(date)
            ind = api.finance_indicator(date)
        except Exception as e:
            print(f"[ocf_hist] {date} 拉取失败: {e}")
            continue
        if cf is None or cf.empty:
            print(f"[ocf_hist] {date} 无数据")
            continue
        ocf_map, adj_map, pub_map = {}, {}, {}
        for _, r in cf.iterrows():
            code = str(r.get("code") or "").split(".")[0].zfill(6)
            if len(code) == 6:
                ocf_map[code] = _f(r.get("net_operate_cash_flow"))
                p = r.get("pubDate")
                if p is not None:
                    pub_map[code] = str(p)[:10]
        if ind is not None and not ind.empty:
            code_col = "code" if "code" in ind.columns else ind.columns[0]
            for _, r in ind.iterrows():
                code = str(r.get(code_col) or "").split(".")[0].zfill(6)
                if len(code) == 6 and r.get("adjusted_profit") is not None:
                    try:
                        adj_map[code] = float(r["adjusted_profit"])
                    except (TypeError, ValueError):
                        pass
        # 批量写入（远程库逐行 upsert 太慢：5200 行 ≈ 5 分钟 → 100 行/批 ≈ 数秒）
        items = [{"code": c, "ocf": o, "adj": adj_map.get(c), "pub": pub_map.get(c)}
                 for c, o in ocf_map.items() if o is not None]
        CH = 100
        for i in range(0, len(items), CH):
            batch = items[i:i + CH]
            if db._use_postgres:
                values = ", ".join(
                    ["(%s, %s, %s, %s, %s)"] * len(batch))
                params = []
                for it in batch:
                    params += [it["code"], date, it["ocf"], it["adj"], it["pub"]]
                db.execute(f"""
                    INSERT INTO stock_finance_ocf_hist
                        (code, report_date, ocf, adjusted_profit, pub_date)
                    VALUES {values}
                    ON CONFLICT (code, report_date) DO UPDATE SET
                        ocf = EXCLUDED.ocf,
                        adjusted_profit = COALESCE(EXCLUDED.adjusted_profit,
                                                   stock_finance_ocf_hist.adjusted_profit),
                        pub_date = EXCLUDED.pub_date,
                        updated_at = NOW()
                """, tuple(params))
            else:
                for it in batch:
                    db.execute("""
                        INSERT OR REPLACE INTO stock_finance_ocf_hist
                            (code, report_date, ocf, adjusted_profit, pub_date)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (it["code"], date, it["ocf"], it["adj"], it["pub"]))
            total += len(batch)
        print(f"[ocf_hist] {date}: +{len(items)} 行（累计 {total}）")
    return {"quarters": quarters, "rows": total}


def load_ocf_hist() -> Dict[str, Dict[str, float]]:
    """{code: {report_date: ocf}}，升序——连续失血计算原料。"""
    ensure_hist_table()
    rows = db.fetch("SELECT code, report_date, ocf FROM stock_finance_ocf_hist "
                    "ORDER BY code, report_date ASC")
    out: Dict[str, Dict[str, float]] = {}
    for r in rows or []:
        if r.get("ocf") is not None:
            out.setdefault(r["code"], {})[str(r["report_date"])] = float(r["ocf"])
    return out


def _qkey(date_str: str) -> str:
    return str(date_str)[:7]   # YYYY-MM，报告期月度标识


def bleeding_streak_map() -> Dict[str, int]:
    """
    连续失血期数：以库内最新报告期为终点，向前数"累计 OCF <0"的连续期数。
    只要有一次转正即断。返回 {code: streak}（streak ≥1 才收录）。
    """
    hist = load_ocf_hist()
    streaks = {}
    for code, by_date in hist.items():
        dates = sorted(by_date)
        if not dates:
            continue
        streak = 0
        for d in reversed(dates):
            if by_date[d] < 0:
                streak += 1
            else:
                break
        if streak >= 1:
            streaks[code] = streak
    return streaks


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", ".env"),
              encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip())
    print(sync_ocf_history(4))
