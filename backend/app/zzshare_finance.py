"""
================================================================================
【文件作用】zzshare 财报扩展因子存储（质量维度的现金流/扣非/商誉等扩展数据源）
================================================================================

设计：
  - 独立表 stock_finance_zz（每 code 一行最新快照，含 indicator/balance/cash_flow
    三表 JSON），不修改现有 stock_finance/engine，杜绝影响线上评分。
  - 表内保留 pub_date（zzshare statDate/pubDate），供"无前视"因子分析过滤。
  - 合并 key：code 主键。code 统一转内部 6 位。

用法：
  from app.zzshare_finance import sync_latest_finance
  sync_latest_finance(codes, chunk=50, verbose=True)
================================================================================
"""

import json
import time
from datetime import datetime

from app.database import db
from app.zzshare_client import get_api, to_zz_code

_TABLES = ("indicator", "balance", "cash_flow", "valuation")
_COLUMNS = {
    "code": "TEXT PRIMARY KEY",
    "report_date": "TEXT",     # zzshare statDate（最新报告期）
    "pub_date": "TEXT",        # zzshare pubDate（报告公告日，防未来函数用）
    "ind_json": "TEXT",
    "bal_json": "TEXT",
    "cf_json": "TEXT",
    "val_json": "TEXT",
    "updated_at": "TEXT",
}


def ensure_table():
    """创建 stock_finance_zz（幂等，兼容 PostgreSQL/SQLite）。"""
    cols = ",\n    ".join(f"{k} {v}" for k, v in _COLUMNS.items())
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS stock_finance_zz (
            {cols}
        )
    """)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rows_from_df(df):
    if df is None:
        return []
    try:
        import pandas as pd
        if isinstance(df, pd.DataFrame):
            return df.to_dict("records")
    except Exception:
        pass
    if isinstance(df, list):
        return df
    return []


def _fetch_table(api, table: str, zz_codes: str) -> list:
    """拉某财务表的最新快照（失败返回 []，不中断整体）。"""
    try:
        df = api.finance_latest(table=table, codes=zz_codes)
        return _rows_from_df(df)
    except Exception as e:
        print(f"[zzshare_finance] {table} 拉取失败: {type(e).__name__}: {e}")
        return []


def sync_latest_finance(codes: list, chunk: int = 50, verbose: bool = True) -> dict:
    """
    按批拉取每只股票最新一期 indicator/balance/cash_flow/valuation → 合并落库。
    返回 {"ok": n, "skipped": n, "errors": n}。
    """
    ensure_table()
    codes = [str(c) for c in codes if str(c).isdigit() or "." in str(c)]
    api = get_api()
    merged: dict = {}
    for i in range(0, len(codes), chunk):
        chunk_codes = codes[i:i + chunk]
        zz = ",".join(to_zz_code(c) for c in chunk_codes)
        got = {"indicator": _fetch_table(api, "indicator", zz),
               "balance": _fetch_table(api, "balance", zz),
               "cash_flow": _fetch_table(api, "cash_flow", zz),
               "valuation": _fetch_table(api, "valuation", zz)}
        # 按 code 合并（内部 6 位）
        for table, rows in got.items():
            for r in rows:
                raw_code = str(r.get("code") or "")
                code6 = raw_code.split(".")[0].zfill(6) if raw_code else None
                if not code6 or len(code6) != 6:
                    continue
                rec = merged.setdefault(code6, {
                    "report_date": "", "pub_date": "", "_tables": set()})
                rec["_tables"].add(table)
                if r.get("statDate"):
                    rec["report_date"] = str(r["statDate"])
                if r.get("pubDate"):
                    rec["pub_date"] = str(r["pubDate"])
                rec[f"{table}_json"] = json.dumps(
                    {k: v for k, v in r.items()
                     if k not in ("code", "statDate", "pubDate")},
                    ensure_ascii=False, default=str)
        if verbose:
            print(f"[zzshare_finance] 批次 {i}-{i + len(chunk_codes) - 1}: "
                  f"合并 {len(merged)} 只")
        time.sleep(0.2)   # 轻度限流保护（匿名/慢源）
    # 落库
    rows = []
    for code6, rec in merged.items():
        rows.append({
            "code": code6,
            "report_date": rec.get("report_date"),
            "pub_date": rec.get("pub_date"),
            "ind_json": rec.get("indicator_json"),
            "bal_json": rec.get("balance_json"),
            "cf_json": rec.get("cash_flow_json"),
            "val_json": rec.get("valuation_json"),
            "updated_at": _now(),
        })
    try:
        db.upsert_many("stock_finance_zz", rows, conflict_columns=["code"])
        return {"ok": len(rows), "requested": len(codes)}
    except Exception as e:
        print(f"[zzshare_finance] 批量写入失败: {e}")
        return {"ok": 0, "requested": len(codes)}


def get_extra(code: str) -> dict:
    """读某只股票的最新扩展财报快照（无则返回 {}）。"""
    row = db.fetch_one(
        "SELECT * FROM stock_finance_zz WHERE code = %s", (str(code),))
    if not row:
        return {}
    out = dict(row)
    for k in ("ind_json", "bal_json", "cf_json", "val_json"):
        try:
            out[k[:-5]] = json.loads(out.pop(k) or "{}")
        except (json.JSONDecodeError, TypeError, KeyError):
            out[k[:-5]] = {}
    return out


def stats() -> dict:
    """覆盖概况。"""
    try:
        total = db.fetch_one("SELECT COUNT(*) AS n FROM stock_finance_zz")
        return {"rows": int((total or {}).get("n") or 0)}
    except Exception:
        return {"rows": 0}
