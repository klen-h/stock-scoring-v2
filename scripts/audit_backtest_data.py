# -*- coding: utf-8 -*-
"""回测数据体检：覆盖 / 连续性 / 异常值 / 关联表口径 / 包与库一致性。

用法：python scripts/audit_backtest_data.py [--sample 120]

★ egress 纪律（见根目录 EGRESS.md）：
  · 能用聚合 SQL 说清的，绝不返回行数据；
  · 需要逐只时间序列的检查（复权断点）走 `research_cache.ohlc_for`（本机数据包优先，
    零 Supabase 流量），并只抽样 N 只。
  所以跑这个脚本本身几乎不产生出站流量。
================================================================================
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BACKEND, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip())

from app.database import db      # noqa: E402

BENCH = "sh000300"
GAP = "-" * 78
_LAST = {}          # 跨函数传值（⑤ 收集的异常清单 → --write-anomalies）


def limit_pct(code: str) -> float:
    """该代码的涨跌停幅度上限（%）。用于判定「不可能的单日涨跌」。

    主板 ±10% / 创业板·科创板 ±20% / 北交所 ±30%。ETF 一律按 20% 保守处理
    （部分宽基/行业 ETF 实际 ±10%，会**少报**，不会误报）。
    """
    c = str(code).lower()
    body = c[2:] if c[:2] in ("sh", "sz", "bj") else c
    if body.startswith(("300", "301", "688", "689", "51", "56", "58", "15")):
        return 20.0
    if body[:1] in ("4", "8", "9") and len(body) == 6:
        return 30.0
    return 10.0


def sec(title):
    print(f"\n{GAP}\n{title}\n{GAP}")


def q1(sql, params=None):
    r = db.fetch_one(sql, params) if params else db.fetch_one(sql)
    return r or {}


def q(sql, params=None):
    return db.fetch(sql, params) if params else db.fetch(sql)


def audit_prices(sample):
    sec("① backtest_prices 总量与基准")
    t = q1("SELECT COUNT(*) n, COUNT(DISTINCT code) codes, "
           "MIN(date) mn, MAX(date) mx FROM backtest_prices")
    print(f"  行数 {t.get('n'):,} | 代码 {t.get('codes'):,} 只 | 日期 {t.get('mn')} ~ {t.get('mx')}")
    b = q1("SELECT COUNT(*) n, MIN(date) mn, MAX(date) mx FROM backtest_prices "
           "WHERE code = %s", (BENCH,))
    print(f"  基准 {BENCH}: {b.get('n')} 个交易日 | {b.get('mn')} ~ {b.get('mx')}")
    if not b.get("n"):
        print("  [X][X] 基准 sh000300 不存在！regime 判定会失效")
        return
    bench_mx = b["mx"]
    bench_days = set(r["date"] for r in q("SELECT date FROM backtest_prices "
                                          "WHERE code = %s", (BENCH,)) or [])

    sec("② 覆盖完整度（每只根数与末日期）")
    rows = q("SELECT code, COUNT(*) n, MIN(date) mn, MAX(date) mx "
             "FROM backtest_prices GROUP BY code") or []
    ns = sorted(r["n"] for r in rows)
    med = ns[len(ns) // 2] if ns else 0
    print(f"  每只根数：min {ns[0]} / 中位 {med} / max {ns[-1]}")
    for th in (250, 500, 700):
        k = sum(1 for n in ns if n < th)
        print(f"    不足 {th} 根：{k} 只" + ("   ← 半年内新股可解释" if th == 250 else ""))
    behind = [r for r in rows if str(r["mx"]) < str(bench_mx)]
    print(f"  末日落后于基准（{bench_mx}）：{len(behind)} 只")
    for r in behind[:12]:
        print(f"    {r['code']} 停于 {r['mx']}（{r['n']} 根，落后 {len(bench_days) - len(bench_days & set([r['mx']]))} 不可比）")
    stale = Counter(str(r["mx"]) for r in rows)
    print("  末日期 Top6 分布：", stale.most_common(6))

    sec("③ 数据质量（异常值行数）")
    checks = [
        ("close 为空或 ≤0", "close IS NULL OR close <= 0"),
        ("open/high/low 为空或 ≤0", "open IS NULL OR open <= 0 OR high IS NULL OR high <= 0 OR low IS NULL OR low <= 0"),
        ("high < low（逻辑倒挂）", "high < low"),
        ("close 越出 [low, high]", "close > high OR close < low"),
        ("volume 为空或 ≤0", "volume IS NULL OR volume <= 0"),
        ("date 为空", "date IS NULL"),
    ]
    for label, cond in checks:
        n = q1(f"SELECT COUNT(*) n FROM backtest_prices WHERE {cond}")["n"]
        flag = "[OK]" if n == 0 else "[!]"
        print(f"  {flag} {label}: {n:,} 行")
    dup = q1("SELECT COUNT(*) n FROM (SELECT code, date FROM backtest_prices "
             "GROUP BY code, date HAVING COUNT(*) > 1) t")["n"]
    print(f"  {'[OK]' if dup == 0 else '[X]'} (code, date) 重复组合: {dup:,} 个")

    sec("④ 交易日连续性（抽样个股 vs 基准）")
    codes = [r["code"] for r in rows if r["n"] >= 500][:sample]
    # ★ 走本机序列（research_cache：数据包优先，零 Supabase 流量）——
    #   原实现是逐只 `SELECT date ... WHERE code=$1`（120 次 ≈ 9 万行），
    #   与本文档顶部的 egress 纪律自相矛盾。
    m = {}
    try:
        from app import research_cache
        m = research_cache.ohlc_for(codes)
        print(f"  取自本机（research_cache）：{len(m)} 只")
    except Exception as e:
        print(f"  [跳过] 本机数据不可用: {e}")
    gaps = []
    for c in codes:
        ds = set(b["date"] for b in (m.get(c) or []))
        if not ds:
            continue
        lo, hi = min(ds), max(ds)
        expect = {d for d in bench_days if lo <= d <= hi}
        miss = expect - ds
        if miss:
            gaps.append((c, len(miss), len(expect), sorted(miss)[:3]))
    print(f"  抽样 {len(codes)} 只（≥500 根）：{len(gaps)} 只有缺口")
    for c, n_m, e, ex in sorted(gaps, key=lambda x: -x[1])[:12]:
        print(f"    {c}: 缺 {n_m}/{e} 天，例 {ex}")

    sec("⑤ 「源自身跳变」影响面（不可能的单日涨跌）")
    # 判据：|单日涨跌| 超过该品种的涨跌停上限即「不可能」，视为数据异常。
    # 排除三类**合法**的无限制情形，避免误报：
    #   ① 复牌首日（前一根 bar 不紧邻 → 中间有停牌期，首日无涨跌幅限制）
    #   ② 序列前 5 根（注册制新股上市初期无涨跌幅限制）
    #   ③ 按品种放宽上限（创业板/科创板/ETF 20%、北交所 30%，见 limit_pct）
    hits = []
    n_bars = 0
    for c, bars in m.items():
        lim = limit_pct(c) + 1.0          # +1 容差（复权精度）
        for i in range(max(1, 5), len(bars)):   # ← 跳过前 5 根（次新股）
            n_bars += 1
            try:
                p0, p1 = float(bars[i - 1]["close"]), float(bars[i]["close"])
            except (TypeError, ValueError):
                continue
            if p0 <= 0:
                continue
            ch = (p1 / p0 - 1) * 100
            if abs(ch) <= lim:
                continue
            d0, d1 = str(bars[i - 1]["date"]), str(bars[i]["date"])
            if [d for d in bench_days if d0 < d < d1]:
                continue                   # 中间有停牌 → 复牌首日，合法
            hits.append((c, d1, round(ch, 2), lim))
    if not n_bars:
        print("  [跳过] 无本机序列")
        return
    n_codes = len(m)
    by_code = Counter(h[0] for h in hits)
    print(f"  覆盖 {n_codes} 只 / {n_bars:,} 根 bar")
    print(f"  ★ 受影响股票：{len(by_code)} 只（{len(by_code) / max(n_codes, 1):.1%}）")
    print(f"  ★ 跳变处数  ：{len(hits)} 处（占 bar 的 {len(hits) / n_bars:.4%}）")
    if not hits:
        return
    print(f"  按年分布    ：{sorted(Counter(h[1][:4] for h in hits).items())}")
    print(f"  最严重的 12 只：{by_code.most_common(12)}")
    print(f"  幅度 Top10：")
    for c, d, ch, lim in sorted(hits, key=lambda x: -abs(x[2]))[:10]:
        print(f"    {c} {d} {ch:+7.2f}%（该品种上限 ±{lim - 1:.0f}%）")
    # 影响面粗估：这些 bar 若被当成真实行情，会让跨越它的 N 日收益失真
    print("  说明：这些是**源数据本身**的异常（非拼接/解析），"
          "回测中跨越这些日期的收益会失真；可按 ① 剔除或 ② 标记不可信。")
    _LAST["hits"] = hits          # 供 --write-anomalies 落库


def write_anomalies(hits) -> dict:
    """把异常清单落库到 `price_anomalies`（幂等：先删同 code 再插）。

    ★ 为什么落库而不是落本机文件：回测跑在 **GitHub Actions**（另一台机器），
      本机文件它读不到。表结构简单，`app/backtest/data.py::anomalies_in()` 读它。
    """
    if not hits:
        print("  [跳过] 无异常可写")
        return {}
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS price_anomalies (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                chg REAL,
                limit_pct REAL,
                detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(code, date)
            )
        """)
    except Exception as e:
        print(f"  [!] 建表失败: {str(e)[:70]}")
        return {}
    ok = 0
    for code, date, chg, lim in hits:
        try:
            db.execute("DELETE FROM price_anomalies WHERE code = %s AND date = %s",
                       (code, date))
            db.execute("INSERT INTO price_anomalies (code, date, chg, limit_pct) "
                       "VALUES (%s, %s, %s, %s)", (code, date, chg, lim - 1.0))
            ok += 1
        except Exception as e:
            print(f"  [!] 写入失败 {code} {date}: {str(e)[:60]}")
    print(f"  [OK] 异常清单已落库 price_anomalies：{ok}/{len(hits)} 条")
    return {"written": ok, "total": len(hits)}


def audit_signal_overlap():
    sec("⑧ 异常日 × 战法信号扫描日（粗粒度污染估计）")
    try:
        from app.backtest import data as bdata
    except Exception as e:
        print(f"  [跳过] {e}")
        return
    an = bdata.anomalies_in()
    if not an:
        print("  [跳过] price_anomalies 为空（先跑 --write-anomalies）")
        return
    dates = sorted({a["date"] for a in an})
    print(f"  异常清单 {len(an)} 条 / 涉及 {len(dates)} 个交易日"
          f"（{dates[0]} ~ {dates[-1]}）")
    rows = q("SELECT scan_date, COUNT(*) n FROM strategy_results "
             "WHERE count > 0 GROUP BY scan_date ORDER BY scan_date") or []
    if not rows:
        print("  [跳过] strategy_results 无扫描记录")
        return
    print(f"  战法扫描日 {len(rows)} 个（{rows[0]['scan_date']} ~ {rows[-1]['scan_date']}）")

    def _d(s):
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")

    near, sig = [], 0
    for r in rows:
        d = str(r["scan_date"])[:10]
        if not d or d == "None":
            continue
        # 扫描日 + 10 个交易日 ≈ 14 个日历日；T+1 开盘介入 → 窗口约在扫描日 +1..+11 日
        hit = any(0 <= (_d(x) - _d(d)).days <= 14 for x in dates)
        if hit:
            near.append((d, r["n"]))
            sig += r["n"]
    print(f"  ★ 信号买入窗口（扫描日 +0~14 日历日）内含异常日的扫描日："
          f"{len(near)}/{len(rows)} 个，涉及信号 {sig} 条")
    for d, n in near[:12]:
        print(f"      {d}  该日 {n} 条信号")
    if len(near) > 12:
        print(f"      …（另有 {len(near) - 12} 个扫描日）")
    print("  ⚠️ 这是**粗估**（用日历日近似交易日）。要精确，"
          "回测侧用 data.has_anomaly(code, 信号日, 信号日+持有期) 逐样本判定。")


def audit_related():
    sec("⑥ 关联表覆盖（回测/评分的输入底座）")
    tables = [
        # (表, 日期列, 是否有 code 列)
        ("mainflow_history", "date", True),
        ("mainforce_state", "date", True),
        ("ranking_history", "rank_date", True),
        ("strategy_results", "scan_date", False),   # 信号在 results_json 里，无 code 列
        ("lhb_history", "date", True),
        ("sector_daily", "date", True),
    ]
    for t, dcol, has_code in tables:
        try:
            sel = ("COUNT(*) n, COUNT(DISTINCT code) codes, " if has_code
                   else "COUNT(*) n, ")
            r = q1(f"SELECT {sel}MIN({dcol}) mn, MAX({dcol}) mx FROM {t}")
            extra = f"{r.get('codes') or 0:>5} 只 | " if has_code else ""
            print(f"  {t:<20} {r.get('n') or 0:>9,} 行 | {extra}"
                  f"{r.get('mn')} ~ {r.get('mx')}")
        except Exception as e:
            print(f"  {t:<20} [查询失败] {str(e)[:60]}")
    for t in ("stock_finance", "stock_industry", "stock_finance_zz"):
        try:
            r = q1(f"SELECT COUNT(DISTINCT code) codes FROM {t}")
            print(f"  {t:<20} 覆盖 {r.get('codes') or 0} 只")
        except Exception as e:
            print(f"  {t:<20} [查询失败] {str(e)[:60]}")


def audit_pack():
    sec("⑦ 数据包（前端/后端读侧）与库的一致性")
    try:
        from app import pack_source
        print(f"  DATA_SOURCE = {pack_source.source_name()} | enabled = {pack_source.enabled()}")
        codes = pack_source.get_codes()
        print(f"  包内代码 {len(codes)} 只")
        probe = [BENCH, "000001", "600519", "002452"]
        for c in probe:
            try:
                bars = pack_source.get_prices(c)
            except Exception as e:
                print(f"    {c}: 读取异常 {str(e)[:50]}")
                continue
            if not bars:
                print(f"    {c}: 包内无数据（回退 DB）")
                continue
            db_mx = q1("SELECT MAX(date) mx FROM backtest_prices WHERE code = %s", (c,)).get("mx")
            pk_mx = bars[-1]["date"]
            ok = "[OK]" if str(pk_mx) == str(db_mx) else "[!] 末根不一致"
            print(f"    {c}: 包 {len(bars)} 根 至 {pk_mx} | 库至 {db_mx}  {ok}")
    except Exception as e:
        print(f"  [跳过] {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=120,
                    help="连续性/复权检查的抽样只数（默认 120；全池请用大值如 1000）")
    ap.add_argument("--skip-related", action="store_true")
    ap.add_argument("--write-anomalies", action="store_true",
                    help="把 ⑤ 检出的「源数据异常日」落库到 price_anomalies")
    args = ap.parse_args()
    audit_prices(args.sample)
    if not args.skip_related:
        audit_related()
    audit_pack()
    if args.write_anomalies:
        sec("⑨ 落库异常清单")
        write_anomalies(_LAST.get("hits") or [])
        audit_signal_overlap()
    else:
        print("\n提示：加 --write-anomalies 可把 ⑤ 的异常清单落库到 price_anomalies，"
              "\n      并用 ⑧ 估计「有多少战法信号窗口跨越异常日」。")
    print(f"\n{GAP}\n体检完成。\n{GAP}")


if __name__ == "__main__":
    main()
