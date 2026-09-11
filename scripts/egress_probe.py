#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】Supabase 出口流量探针：按语句归因"某一段时间到底读了多少行/多少 MB"
================================================================================

回答的问题（2026-09-12 起因）：
  "我 push 一次代码 → Render 重启一次，到底消耗多少 Supabase 流量？"
  "今天这根 691MB 的柱子，是谁读出来的？"

用法：
  python scripts/egress_probe.py snapshot --tag before     # 取一次基线快照
  # ── 之后做点事：push 触发部署 / 跑一轮日批 / 打开前端页面 ──
  python scripts/egress_probe.py snapshot --tag after
  python scripts/egress_probe.py diff --from before --to after
  python scripts/egress_probe.py list                      # 看已存快照
  python scripts/egress_probe.py widths --refresh          # 重算各行"过网宽度"估计

原理：
  · 数据源 = Supabase 的 `pg_stat_statements`（每条语句的**累计** calls/rows）
    + `pg_stat_database`（元组计数）+ `pg_stat_statements_info`（是否被重置）
  · 快照只存累计值；两次快照做差 = 这段时间**真实发生**的读取（行数精确）
  · 字节数 = Δ行数 × 该表「平均过网行宽」——行宽由 `pg_column_size` 抽样 200 行估计
    （注意：Postgres 发 TOAST 大字段时传的是压缩形态，所以按 pg_column_size 估比
      length() 更接近真实过网字节）

局限（务必知道）：
  · 只看得到数据库侧；GitHub Pages（数据包/前端包）、腾讯/东财 的流量不在这里
  · Supabase 自身 exporter 的语句（pg_timezone_names / pg_stat_statements 自省等）
    会在报告里单独标注为「非本项目」，不计入合计
  · 若两次快照之间 `stats_reset` 变了（Supabase 重置统计/实例重启），差值无效 —— 脚本会直说

快照落盘：backend/data/egress_probe.json（该目录已被 .gitignore 忽略，不会进仓库）
================================================================================
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
STORE = os.path.join(BACKEND, "data", "egress_probe.json")
BJ = timezone(timedelta(hours=8))

# 非本项目自己的语句（Supabase 平台侧自省/监控），不参与合计
_NOISE = ("pg_timezone_names", "pg_stat_statements", "pg_settings",
          "pg_database", "pg_namespace", "pg_class", "pg_attribute",
          "pg_stat_", "pg_catalog", "information_schema", "pg_locks",
          "pg_stat_activity", "pg_stat_database", "pg_stat_user_tables")


def _load_env():
    path = os.path.join(BACKEND, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _conn():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url or not url.startswith("postgres"):
        print("::error:: 需要 PostgreSQL 的 DATABASE_URL（backend/.env）")
        sys.exit(2)
    import psycopg2
    return psycopg2.connect(url, connect_timeout=15)


def _store() -> dict:
    if os.path.exists(STORE):
        try:
            return json.load(open(STORE, encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {"version": 1, "widths": {}, "snapshots": {}}


def _save(data: dict):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    json.dump(data, open(STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ── 表名解析（用于把 Δ行数 换算成 MB）────────────────────────────────────────
# 兼容 `FROM t` 与 PostgREST 风格的 `FROM public.t`（带 schema 前缀）
_TBL_RE = re.compile(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+"
                     r"(?:([a-z_][a-z0-9_]*)\.)?([a-z_][a-z0-9_]*)", re.I)
_SKIP = ("select", "where", "values", "set", "as", "only", "lateral")


def _table_of(q: str) -> str:
    for m in _TBL_RE.finditer(q or ""):
        schema, t = (m.group(1) or "").lower(), m.group(2).lower()
        if t in _SKIP:
            continue
        return t if t != schema else schema
    return ""


def measure_widths(conn, refresh=False) -> dict:
    """每条表「平均过网行宽」（字节，pg_column_size 抽样 200 行取均值）。"""
    st = _store()
    widths = {} if refresh else dict(st.get("widths") or {})
    cur = conn.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        if t in widths:
            continue
        try:
            cur.execute(f"SELECT AVG(pg_column_size(x))::int AS w FROM "
                        f"(SELECT * FROM {t} LIMIT 200) x")
            w = cur.fetchone()[0]
            if w:
                widths[t] = int(w)
        except Exception as e:
            conn.rollback()
            print(f"  [widths] {t} 估计失败（忽略）: {str(e)[:80]}")
    return widths


# ── 快照 ────────────────────────────────────────────────────────────────────
def cmd_snapshot(args):
    conn = _conn()
    cur = conn.cursor()
    now = datetime.now(BJ)
    snap = {"taken_at_bj": now.isoformat(timespec="seconds"),
            "taken_at_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds")}

    try:
        cur.execute("SELECT stats_reset, dealloc FROM pg_stat_statements_info")
        r = cur.fetchone()
        snap["stats_reset"] = str(r[0]) if r else None
        snap["dealloc"] = r[1] if r else None
    except Exception as e:
        conn.rollback()
        snap["stats_reset"] = None
        print(f"  [warn] pg_stat_statements_info 不可读: {str(e)[:80]}")

    cur.execute("SELECT SUM(calls)::bigint AS c, SUM(rows)::bigint AS r, "
                "COUNT(*)::int AS n FROM pg_stat_statements")
    c, r, n = cur.fetchone()
    snap["total"] = {"calls": int(c or 0), "rows": int(r or 0), "stmts": int(n or 0)}

    cur.execute("""SELECT tup_returned, tup_fetched, tup_inserted, tup_updated,
                          tup_deleted, xact_commit, blks_read, blks_hit, temp_bytes
                   FROM pg_stat_database WHERE datname = current_database()""")
    cols = [d[0] for d in cur.description]
    snap["db"] = {k: int(v or 0) for k, v in zip(cols, cur.fetchone())}

    cur.execute("""SELECT queryid::text AS qid, calls::bigint, rows::bigint,
                          total_exec_time,
                          regexp_replace(query, '\\s+', ' ', 'g') AS q
                   FROM pg_stat_statements ORDER BY rows DESC LIMIT 300""")
    snap["stmts"] = [{"qid": qid, "calls": int(calls), "rows": int(rows),
                      "ms": round(float(ms or 0), 1), "q": (q or "")[:400]}
                     for qid, calls, rows, ms, q in cur.fetchall()]

    widths = measure_widths(conn)
    conn.close()

    data = _store()
    data["widths"] = widths
    data["snapshots"][args.tag] = snap
    _save(data)

    print(f"✅ 快照已存: tag={args.tag}  {snap['taken_at_bj'][:19]}（北京时间）")
    print(f"   累计: {snap['total']['calls']:,} 次调用 / {snap['total']['rows']:,} 行 / "
          f"{snap['total']['stmts']} 条不同语句")
    print(f"   stats_reset={snap['stats_reset']}  （下次 diff 会用它判断统计是否被重置）")
    print(f"   行宽估计覆盖 {len(widths)} 张表 → {STORE}")


# ── 差值报告 ────────────────────────────────────────────────────────────────
def cmd_diff(args):
    data = _store()
    snaps = data.get("snapshots") or {}
    a, b = snaps.get(args.frm), snaps.get(args.to)
    if not a or not b:
        print(f"::error:: 找不到快照 {args.frm!r} 或 {args.to!r}；现有: {list(snaps)}")
        sys.exit(2)

    t0 = datetime.fromisoformat(a["taken_at_bj"])
    t1 = datetime.fromisoformat(b["taken_at_bj"])
    mins = (t1 - t0).total_seconds() / 60
    print(f"=== 窗口：{t0:%Y-%m-%d %H:%M} → {t1:%Y-%m-%d %H:%M}（{mins:.0f} 分钟）===")

    if a.get("stats_reset") != b.get("stats_reset"):
        print("⚠️ pg_stat_statements 在窗口内被重置（stats_reset 变化）→ 差值不可用，请重新取基线")
    d_calls = b["total"]["calls"] - a["total"]["calls"]
    d_rows = b["total"]["rows"] - a["total"]["rows"]
    if d_rows < 0 or d_calls < 0:
        print("⚠️ 累计值出现负数（统计被重置/实例重启）→ 本次差值不可用")
    print(f"总计: 调用 {d_calls:+,} 次 / 返回 {d_rows:+,} 行\n")

    widths = data.get("widths") or {}
    before = {s["qid"]: s for s in a.get("stmts") or []}
    rows = []
    for s in b.get("stmts") or []:
        old = before.get(s["qid"])
        dc = s["calls"] - (old["calls"] if old else 0)
        dr = s["rows"] - (old["rows"] if old else 0)
        if dc <= 0 and dr <= 0:
            continue
        tbl = _table_of(s["q"])
        w = widths.get(tbl)
        est_mb = (dr * w / 1048576.0) if (w and dr > 0) else None
        rows.append({"q": s["q"], "dc": dc, "dr": dr, "mb": est_mb, "tbl": tbl,
                     "noise": any(n in s["q"].lower() for n in _NOISE),
                     "new": old is None, "kind": _kind(s["q"]),
                     "subset": _subset_cols(s["q"])})
    rows.sort(key=lambda x: -(x["mb"] if x["mb"] is not None else (x["dr"] / 1e6)))

    ours = [x for x in rows if not x["noise"] and x["kind"] == "read"]
    writes = [x for x in rows if x["kind"] == "write"]
    total_mb = sum(x["mb"] or 0 for x in ours)
    print("按 Δ行数（Top 25；≤ = 只取了部分列，按整行宽算的**上限**）：")
    print(f"  {'Δ行数':>12} {'Δ调用':>7} {'估计MB':>8}  {'表':<20} 语句")
    for x in ours[:25]:
        mb = ("≤%.2f" % x["mb"]) if (x["mb"] is not None and x["subset"]) \
            else ("%.2f" % x["mb"] if x["mb"] is not None else "?")
        print(f"  {x['dr']:>12,} {x['dc']:>7,} {mb:>8}  {x['tbl'][:20]:<20} "
              f"{x['q'][:110]}{'  [窗口内新语句]' if x['new'] else ''}")
    print(f"\n★ 读语句估计合计：**约 {total_mb:.1f} MB**"
          f"（{total_mb / max(mins, 1) * 1440:.0f} MB/天 折算）")
    if writes:
        wmb = sum(x["mb"] or 0 for x in writes)
        print(f"（写入语句 {len(writes)} 条不计出口流量，受影响 "
              f"{sum(x['dr'] for x in writes):,} 行 / 若按行宽算为 {wmb:.1f}MB，仅参考）")
    noise_rows = sum(x["dr"] for x in rows if x["noise"])
    print(f"（另有非本项目/平台自省语句 {noise_rows:,} 行，未计入）")

    skipped = [x for x in rows if x["mb"] is None and x["dr"] > 0]
    if skipped:
        unknown = {x["tbl"] for x in skipped}
        print(f"⚠️ 有语句没解析出表名、未计入 MB（表: {sorted(t for t in unknown if t) or '—'}）；"
              f"可 `python scripts/egress_probe.py widths --refresh` 后重算")


def cmd_top(args):
    """自 stats_reset 以来的**累计**归因（= 面板上那个周期总量，按语句摊开）。

    两条重要口径（避免误判，2026-09-12 修正）：
      · **写入（INSERT/UPDATE/DELETE）不计出口流量** —— pg_stat_statements 的 rows 只是
        受影响行数，数据是往服务端送的。这类单独列在下面。
      · **只选部分列的语句，估计值是「上限」**（行宽按整行算）→ 输出里带 `≤` 前缀。
    """
    data = _store()
    snaps = data.get("snapshots") or {}
    if not snaps:
        print("::error:: 还没有快照")
        sys.exit(2)
    tag = args.tag or list(snaps)[-1]
    s = snaps[tag]
    widths = data.get("widths") or {}
    print(f"=== 累计归因（tag={tag}，统计起点 {s.get('stats_reset')}）===")
    print(f"    累计 {s['total']['calls']:,} 次调用 / {s['total']['rows']:,} 行\n")

    reads, writes = [], []
    for st in s.get("stmts") or []:
        tbl = _table_of(st["q"])
        w = widths.get(tbl)
        mb = (st["rows"] * w / 1048576.0) if w else None
        noise = any(n in st["q"].lower() for n in _NOISE)
        item = (mb, st, tbl, noise)
        (writes if _kind(st["q"]) == "write" else reads).append(item)
    reads.sort(key=lambda x: -(x[0] if x[0] is not None else 0))

    print(f"  {'估计MB':>9} {'行数':>13} {'调用':>9}  {'表':<20} 语句")
    shown = reads[: args.limit]
    for mb, st, tbl, noise in shown:
        flag = "（平台自省）" if noise else ""
        tmb = ("≤%.1f" % mb) if (mb is not None and _subset_cols(st["q"])) \
            else ("%.1f" % mb if mb is not None else "?")
        print(f"  {tmb:>9} {st['rows']:>13,} {st['calls']:>9,}  {tbl[:20]:<20} "
              f"{st['q'][:100]}{flag}")
    total = sum(mb for mb, st, tbl, noise in reads if mb and not noise)
    print(f"\n★ 读语句累计估计：**约 {total / 1024:.2f} GB**"
          f"（面板同期 16.24GB；差额 = 未解析出表名的语句 + 上限高估 + 行宽偏差）")
    if writes:
        wtop = sorted(writes, key=lambda x: -(x[0] or 0))[:3]
        print("（写入语句不计出口流量，仅列前 3 条参考受影响行数："
              + "；".join(f"{t or '?'} {int(st['rows']):,} 行" for _, st, t, _ in wtop)
              + "）")


def _kind(q: str) -> str:
    """read = 会往客户端送数据的语句；write = 只往服务端送（不占出口流量）。"""
    head = (q or "").lstrip().lower()
    if head.startswith("select") or head.startswith("with") or head.startswith("show"):
        return "read"
    return "write"


def _subset_cols(q: str) -> bool:
    """SELECT 里是不是只取了部分列（是 → 行宽按整行算，估计值是上限）。"""
    return not re.search(r"\bselect\s+(?:[a-z_][a-z0-9_]*\.)?\*", q or "", re.I)


def cmd_list(args):
    data = _store()
    for tag, s in (data.get("snapshots") or {}).items():
        print(f"  {tag:<12} {s['taken_at_bj'][:19]}  "
              f"{s['total']['calls']:,} 调用 / {s['total']['rows']:,} 行")
    print(f"  行宽估计: {len(data.get('widths') or {})} 张表  文件: {STORE}")


def cmd_widths(args):
    conn = _conn()
    w = measure_widths(conn, refresh=args.refresh)
    conn.close()
    data = _store()
    data["widths"] = w
    _save(data)
    print(f"✅ 行宽估计已更新：{len(w)} 张表")


def main():
    _load_env()
    ap = argparse.ArgumentParser(description="Supabase egress 探针")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("snapshot"); p1.add_argument("--tag", required=True)
    p1.set_defaults(fn=cmd_snapshot)
    p2 = sub.add_parser("diff")
    p2.add_argument("--from", dest="frm", required=True)
    p2.add_argument("--to", dest="to", required=True)
    p2.set_defaults(fn=cmd_diff)
    p3 = sub.add_parser("list"); p3.set_defaults(fn=cmd_list)
    p5 = sub.add_parser("top")           # 累计归因（自 stats_reset）
    p5.add_argument("--tag", default=None)
    p5.add_argument("--limit", type=int, default=25)
    p5.set_defaults(fn=cmd_top)
    p4 = sub.add_parser("widths"); p4.add_argument("--refresh", action="store_true")
    p4.set_defaults(fn=cmd_widths)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
