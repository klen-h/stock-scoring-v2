"""每日数据库流量（egress）归因快照 —— 回答"今天的 500MB 是哪条 SQL 烧的"。

================================================================================
【为什么需要】
Supabase 的 egress 只在控制台看总量（"500MB / 数据库占 81%"），**无法归因到语句**：
`pg_stat_statements` 是**累计值**，跑两天后你只知道"总共读了 47.9M 行"，不知道
"昨天读了多少行、是哪条"。2026-10-25 那次 500MB 只能靠推断（部署次数 × 冷启整表读），
证据链不完整。

【做法】
每天日批末尾快照一次**原始计数器**（两张小表，各 100~300 行）：
  · `egress_stmt_raw`：pg_stat_statements 的前 N 条（calls / rows / 耗时 / SQL 摘要）
  · `egress_table_raw`：pg_stat_user_tables 的读放大（seq_tup_read / idx_tup_fetch / 写入）
第二天用 `report()` **对两天的原始值做差** ⇒ 得到"昨天每条 SQL 读了多少行"，
再乘表的**平均行宽**（heap 字节 / 活行数）≈ 出站字节数。

【口径边界（必须写清，避免又把代理指标当真相）】
· rows 是**返回行数**，不是字节；字节是"行数 × 平均行宽"的**估算**，误差 ±30%
  （宽列如 kline_data 的行宽远高于整表均值 ⇒ 对这类表按 **表均值** 会**低估**）。
· `pg_stat_statements` 有 `track` 配置与容量上限（超出会淘汰），delta 可能为负 ——
  负值一律按 0 处理（表示"统计被淘汰/重置"，不表示"昨天读的是负数"）。
· 只读**计数器**，不碰业务表；失败一律降级（不影响日批）。
================================================================================
"""
import re
from datetime import datetime, timedelta, timezone

from app.database import db

_T_STMT = "egress_stmt_raw"
_T_TBL = "egress_table_raw"
_BJ = timezone(timedelta(hours=8))
TOP_STMT = 300          # 语句快照条数（按返回行数取前 N，其余对总量影响小）


def ensure_tables() -> None:
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_STMT} (
            day TEXT NOT NULL,
            queryid BIGINT,
            calls BIGINT,
            rows BIGINT,
            ms DOUBLE PRECISION,
            q TEXT,
            PRIMARY KEY (day, queryid)
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_T_TBL} (
            day TEXT NOT NULL,
            relname TEXT NOT NULL,
            seq_tup_read BIGINT,
            idx_tup_fetch BIGINT,
            n_tup_ins BIGINT,
            n_tup_upd BIGINT,
            live_rows BIGINT,
            heap_bytes BIGINT,
            PRIMARY KEY (day, relname)
        )
    """)


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", q or "").strip()[:200]


def snapshot(day: str = None) -> dict:
    """把当前累计计数器存成"今天"的一份（幂等：同日重跑覆盖）。"""
    ensure_tables()
    day = day or datetime.now(_BJ).strftime("%Y-%m-%d")
    stmt_n = tbl_n = 0
    try:
        stmts = db.fetch(
            "SELECT queryid, calls, rows, total_exec_time AS ms, query "
            "FROM pg_stat_statements ORDER BY rows DESC LIMIT %s", (TOP_STMT,)) or []
        db.execute(f"DELETE FROM {_T_STMT} WHERE day = %s", (day,))
        if stmts:
            db.upsert_many(_T_STMT, [{
                "day": day, "queryid": int(r["queryid"] or 0),
                "calls": int(r["calls"] or 0), "rows": int(r["rows"] or 0),
                "ms": float(r["ms"] or 0), "q": _norm(r["query"]),
            } for r in stmts], ["day", "queryid"])
            stmt_n = len(stmts)
    except Exception as e:
        print(f"[egress] pg_stat_statements 快照失败（跳过）: {str(e)[:100]}")

    try:
        # ★ 必须 GROUP BY relname：同名表可能出现在多个 schema（实测 upsert 报
        #   "cannot affect row a second time"）⇒ 按 relname 聚合。
        rows = db.fetch(
            "SELECT relname, SUM(seq_tup_read) AS seq_tup_read, "
            "       SUM(idx_tup_fetch) AS idx_tup_fetch, SUM(n_tup_ins) AS n_tup_ins, "
            "       SUM(n_tup_upd) AS n_tup_upd, SUM(n_live_tup) AS n_live_tup, "
            "       SUM(pg_relation_size(relid)) AS bytes "
            "FROM pg_stat_user_tables GROUP BY relname ORDER BY relname") or []
        db.execute(f"DELETE FROM {_T_TBL} WHERE day = %s", (day,))
        if rows:
            db.upsert_many(_T_TBL, [{
                "day": day, "relname": r["relname"],
                "seq_tup_read": int(r["seq_tup_read"] or 0),
                "idx_tup_fetch": int(r["idx_tup_fetch"] or 0),
                "n_tup_ins": int(r["n_tup_ins"] or 0),
                "n_tup_upd": int(r["n_tup_upd"] or 0),
                "live_rows": int(r["n_live_tup"] or 0),
                "heap_bytes": int(r["bytes"] or 0),
            } for r in rows], ["day", "relname"])
            tbl_n = len(rows)
    except Exception as e:
        print(f"[egress] 表统计快照失败（跳过）: {str(e)[:100]}")

    print(f"[egress] {day} 快照: 语句 {stmt_n} 条 / 表 {tbl_n} 张")
    return {"day": day, "stmts": stmt_n, "tables": tbl_n}


def _prev_day(day: str) -> str:
    r = db.fetch_one(f"SELECT MAX(day) AS d FROM {_T_STMT} WHERE day < %s", (day,))
    return str((r or {}).get("d") or "")


def _table_of(q: str) -> str:
    m = re.search(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+([a-z_][a-z0-9_]*)", q or "", re.I)
    return (m.group(1).lower() if m else "")


def report(day: str = None, top: int = 15) -> dict:
    """对比 `day` 与上一份快照，给出"昨天谁最贵"（行数 + 估算字节）。"""
    ensure_tables()
    day = day or str((db.fetch_one(f"SELECT MAX(day) AS d FROM {_T_STMT}") or {}).get("d") or "")
    prev = _prev_day(day) if day else ""
    if not day:
        return {"ok": False, "note": "尚无快照（先跑 egress_report / 日批）"}
    if not prev:
        return {"ok": False, "day": day, "note": "只有一份快照，缺前一天 ⇒ 无法做差"}

    t_now = {r["relname"]: r for r in db.fetch(
        f"SELECT * FROM {_T_TBL} WHERE day = %s", (day,)) or []}
    width = {}
    for rel, r in t_now.items():
        live = int(r.get("live_rows") or 0)
        if live > 0:
            width[rel] = float(r.get("heap_bytes") or 0) / live

    cur = {int(r["queryid"]): r for r in db.fetch(
        f"SELECT * FROM {_T_STMT} WHERE day = %s", (day,)) or []}
    old = {int(r["queryid"]): r for r in db.fetch(
        f"SELECT * FROM {_T_STMT} WHERE day = %s", (prev,)) or []}

    items = []
    for qid, r in cur.items():
        o = old.get(qid) or {}
        d_rows = max(int(r["rows"] or 0) - int(o.get("rows") or 0), 0)
        d_calls = max(int(r["calls"] or 0) - int(o.get("calls") or 0), 0)
        if not d_rows and not d_calls:
            continue
        tbl = _table_of(r.get("q"))
        est = d_rows * width.get(tbl, 0) if tbl else 0
        items.append({
            "calls": d_calls, "rows": d_rows,
            "est_mb": round(est / 1048576, 2), "table": tbl, "q": r.get("q"),
        })
    items.sort(key=lambda x: (x["rows"], x["calls"]), reverse=True)
    # ⚠️ 总量必须用**全部**差值求和，不能只加 Top N（否则报表出现 "est 0MB" 的误导）
    total_mb = round(sum(x["est_mb"] for x in items), 1)

    tbl_delta = []
    for rel, r in t_now.items():
        o = next((x for x in db.fetch(
            f"SELECT * FROM {_T_TBL} WHERE day = %s AND relname = %s", (prev, rel)) or []), {})
        d_read = max((int(r["seq_tup_read"] or 0) + int(r["idx_tup_fetch"] or 0))
                     - (int(o.get("seq_tup_read") or 0) + int(o.get("idx_tup_fetch") or 0)), 0)
        if d_read:
            tbl_delta.append({"table": rel, "rows": d_read,
                              "est_mb": round(d_read * width.get(rel, 0) / 1048576, 2)})
    tbl_delta.sort(key=lambda x: x["rows"], reverse=True)

    return {"ok": True, "day": day, "prev": prev,
            "est_total_mb": total_mb,
            "statements": items[:top], "tables": tbl_delta[:top]}


if __name__ == "__main__":
    import json
    import sys

    if "--snapshot" in sys.argv:
        print(json.dumps(snapshot(), ensure_ascii=False))
    else:
        print(json.dumps(report(), ensure_ascii=False, indent=1))
