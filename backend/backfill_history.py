"""
================================================================================
【文件作用】回测历史数据回填：ETF 池 + 沪深300基准 + 战法历史个股 → backtest_prices
================================================================================
运行方式：
  python backfill_history.py --all         # 全部（ETF + 指数 + 个股）
  python backfill_history.py --etf         # 仅 ETF 池（28 只）
  python backfill_history.py --index       # 仅沪深300 基准
  python backfill_history.py --stocks      # 仅战法历史出现过的个股

幂等：按 code+date 去重，已回填的代码跳过（断点续传）。
限速 2 req/s（每请求间隔 0.5s），避免东财限流。
================================================================================
"""

import argparse
import json
import time
from datetime import timedelta

from app.backtest import data
from app.database import db
from app.flash import rules
from app.signals.tracker import HOLDINGS_MAP

RATE_LIMIT = 1.0   # 每请求间隔（秒），东财对连续请求会断连，放慢更稳
DAILY_STOCK_QUOTA = 150  # 每晚个股回填上限：数据源限流下长任务易被打断，分批推进更稳

_EM_FAIL_STREAK = 0   # 东财连续失败计数，>=3 后全局切换腾讯源（东财可能被临时封 IP）


def _basis_changed(code: str, start: str, rows: list) -> bool:
    """增量数据与库中「重叠日」的收盘价是否不一致（= 库里的旧数据与当前源对不上）。

    ★ 2026-09-18（回测数据体检发现）：**库与源确实会不一致** —— 实测 `603036` 在
      2026-08-21 库中收盘 15.79、源上 15.71（差 −0.51%）。成因是 `qfq` 以"取数时刻的
      最新价"为基准，而 `data.fetch_history(start=...)` 只取该日之后 ⇒ 旧入库数据可能
      是用**当时的旧基准**复权的。跨越这类日期的收益率会偏。

    判据：增量返回里含 `start` 那天（重叠日）时比对其收盘价 —— 不一致即需**全量重拉覆盖**
    （`save_prices` 是 ON CONFLICT 幂等写，全量重拉天然就是覆盖）。重叠日取不到
    （如停牌）时返回 False，避免退化成每天全量。
    ——
    ⚠️ **更正（同日实测否证了一个更激进的归因）**：曾把「|单日涨跌| > 11% 的纯跳变」
      （`603508` 单只 14 处、主板出现 +16.0%/−12.2%）也归因于此，**这是错的**。
      证据：① 对 10 只做全量重拉（单请求、单一基准）后跳变数 **48 → 48 完全未变**；
      ② 直接抓腾讯原始 `qfq` 序列（不经本项目任何代码），`603508` 那几天本身就是
      −12.18% / +16.00%，而同期 `000062` 完全正常（−7.44%）。
      ⇒ **那些跳变在源里就存在，是单只、局部的源数据问题**，与拼接/解析无关；
      既有的 `scripts/audit_backtest_data.py` 第 ⑤ 项可用于持续监测。
      本函数修的只是「库与源不一致」这一件（较小但真实）的事。
    """
    if not start or not rows:
        return False
    try:
        same = next((r for r in rows if str(r.get("date")) == str(start)), None)
        if same is None:
            return False                      # 源上该日无数据 → 无从判断
        cur = db.fetch_one(
            "SELECT close FROM backtest_prices WHERE code = %s AND date = %s",
            (code, start))
        if not cur or cur.get("close") in (None, 0):
            return False
        old, new = float(cur["close"]), float(same.get("close") or 0)
        if new <= 0:
            return False
        # ★ 阈值 0.2%（不是 1e-6）：实测库与源之间存在 **0.01%~0.09% 的系统性精度差**
        #   （几乎每个交易日都有，例 603508 @2023-09-19 库 10.981 vs 拉取 10.98）——
        #   `1e-6` 会把这种精度差误判成"复权基准已变"，导致**每次回填都全量重拉**。
        #   而真正的基准变更（分红/送股）幅度是股息率量级（≥0.5%，实测 603036 为
        #   0.51%）⇒ 取 0.2% 作分界：真的变会触发，精度噪声不会。
        if abs(old - new) / old > 0.002:
            # ★ 只用 GBK 可编码的字符：本项目控制台是 GBK，`→`/`⇒` 会
            #   UnicodeEncodeError 把日常回填打崩（2026-09-18 实测踩到）
            print(f"  [复权] {code} 重叠日 {start} 收盘 {old} -> {new}"
                  f"（{new / old - 1:+.2%}）基准已变，改全量重建")
            return True
    except Exception as e:
        print(f"  [复权] {code} 基准检测异常（按未变处理）: {e}")
    return False


def backfill(code: str, name: str) -> int:
    """增量回填单只：已有数据只补最新日期之后，无数据全量。返回写入行数。"""
    global _EM_FAIL_STREAK
    latest = db.fetch_one(
        "SELECT MAX(date) AS d FROM backtest_prices WHERE code = %s", (code,))
    start = (latest or {}).get("d")
    if start:
        print(f"  增量 {code} {name}（已有数据至 {start}）")
    rows = data.fetch_history(code, start=start)
    if not rows:
        if start:
            # 已有数据且无新增日期 = 已同步到最新，不是失败。
            # 此前被误计为 [FAIL] 并累计连续失败、触发全局切腾讯源，
            # 掩盖了真正需要补数据的新标的（农业/养殖 ETF 案例）。
            return 0
        _EM_FAIL_STREAK += 1
        if _EM_FAIL_STREAK >= 3 and not data.DISABLE_EASTMONEY:
            data.DISABLE_EASTMONEY = True
            print("  [WARN] 东财连续失败，后续全部切换腾讯源")
        print(f"  [FAIL] {code} {name} 无数据")
        return 0
    _EM_FAIL_STREAK = 0
    # ★ 2026-09-18：复权基准变了 → 全量重拉**覆盖**（否则本次追加会留下接缝，
    #   见 _basis_changed 的完整说明）。
    #   ⚠️ 必须传 `replace=True`：`save_prices` 默认是 `ON CONFLICT DO NOTHING`，
    #      **不会更新已存在的行** —— 只传全量数据而不加 replace 的话，旧价永远改不掉
    #      （这正是方案 C 初版的 bug，2026-09-18 同日发现并修正）。
    if _basis_changed(code, start, rows):
        full = data.fetch_history(code, start=None)
        if full and len(full) >= len(rows):
            rows = full
            n = data.save_prices(code, name, rows, replace=True)
            print(f"  [复权] {code} 已全量重拉并**覆盖** {n} 条")
            print(f"  [OK] {code} {name}: {n} 条 ({rows[0]['date']} ~ {rows[-1]['date']})")
            time.sleep(RATE_LIMIT)
            return n
        print(f"  [复权] {code} 全量重拉未取到更长序列，保留增量结果")
    n = data.save_prices(code, name, rows)
    print(f"  [OK] {code} {name}: {n} 条 ({rows[0]['date']} ~ {rows[-1]['date']})")
    time.sleep(RATE_LIMIT)
    return n


def backfill_etf() -> int:
    print("── ETF 池 ──")
    total = 0
    for name, code in HOLDINGS_MAP.items():
        total += backfill(code, name)
    print(f"ETF 完成，共写入 {total} 条\n")
    return total


def backfill_index() -> int:
    print("── 沪深300 基准 ──")
    n = backfill("sh000300", "沪深300指数")
    print("")
    return n


def _collect_strategy_codes(days: int = 30) -> list:
    """提取需回填的个股代码（含名称，去重）：
    1. strategy_results 中全部战法选股；
    2. 近 days 天 ranking_history 中出现过的评分股票。

    评分 TopN 每日变动，很多不在战法池中；若只回填战法个股，
    regime_review 分层复盘会因无行情跳过 90%+ 的评分记录、结论失真，
    因此必须把评分股票一并纳入回填池。"""
    codes = {}
    rows = db.fetch("SELECT results_json FROM strategy_results WHERE count > 0")
    for r in rows:
        try:
            items = json.loads(r["results_json"])
        except (json.JSONDecodeError, KeyError):
            continue
        for it in items or []:
            c = str(it.get("code") or "").strip()
            if len(c) == 6:
                codes[c] = it.get("name") or c
    since = (rules.beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.fetch(
        "SELECT code, name FROM ranking_history WHERE rank_date >= %s", (since,))
    for r in rows:
        c = str(r["code"] or "").strip()
        if len(c) == 6:
            codes.setdefault(c, r.get("name") or c)
    return list(codes.items())


def _collect_rank_codes(days: int = 7) -> set:
    """近 N 天评分上榜股（排行榜 T+5 绩效轨道的数据保障，2026-09-13）。

    背景：performance 排行榜轨道要求每只上榜股凑满 T+5 日线才计入，
    150 只/晚的配额推进下，上榜股可能排队数日 → 快照日整批缺席
    （08-17~09-01 后 09-02 起全部缺席的实证）。上榜股插队到回填最前，
    保证 T+5 窗口内数据必然就绪。
    """
    try:
        since = (rules.beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = db.fetch(
            "SELECT DISTINCT code FROM ranking_history WHERE rank_date >= %s", (since,))
        return {str(r["code"]).strip() for r in (rows or []) if len(str(r["code"]).strip()) == 6}
    except Exception:
        return set()


def backfill_stocks() -> int:
    print("── 战法历史个股 ──")
    items = _collect_strategy_codes()
    print(f"  共 {len(items)} 只个股")
    total = 0
    for code, name in items:
        total += backfill(code, name)
    print(f"个股完成，共写入 {total} 条\n")
    return total


def _latest_date_map(codes: list) -> dict:
    """一次查完池内所有 code 的最新日期（避免逐只查询，远程库往返很慢）。"""
    if not codes:
        return {}
    placeholders = ",".join(["%s"] * len(codes))
    rows = db.fetch(
        f"SELECT code, MAX(date) AS d FROM backtest_prices "
        f"WHERE code IN ({placeholders}) GROUP BY code", tuple(codes))
    return {r["code"]: r["d"] for r in rows}


def backfill_daily(quota: int = DAILY_STOCK_QUOTA) -> dict:
    """每日增量回填（供调度器调用）：ETF 池 + 沪深300 + 战法新个股。
    已回填标的只补最新日期之后，新出现的个股全量。返回统计 dict。

    额外输出 stock_missing：战法个股最新行情日期落后于沪深300基准的清单——
    增量回填对"数据源无返回"只是打印 [FAIL] 不抛异常，若不检测，
    个股行情会静默停更、战法回测无法撮合且无人知晓。

    调度策略（关键）：数据源（东财/腾讯）对连续请求会限流，单晚能成功写入的
    只数有限。若每晚都从池头顺序扫描，前面的股票会占完请求配额，
    池尾股票永远轮不到（实测 400 只池子里 334 只长期 0 数据）。
    因此：①已同步到基准的股票直接跳过不发请求；②无数据的排最前、滞后越久越前；
    ③每晚限量 quota 只（默认 DAILY_STOCK_QUOTA），分批推进。
    ★ 2026-09-13：近 7 天评分上榜股插队到最前（rank_priority）——排行榜 T+5
      绩效轨道依赖上榜股 5 日内日线就绪，普通排队可能让整批快照日缺席。
      quota=None 时不限量（一次性补历史用，见 backfill_lagging）。
    """
    stats = {"codes": 0, "rows": 0}

    # ① 先回填基准（沪深300），据此判断"应同步到哪天"
    stats["codes"] += 1
    stats["rows"] += backfill("sh000300", "沪深300指数")

    # 基准日期：个股/ETF 应同步到该日期
    benchmark = (db.fetch_one(
        "SELECT MAX(date) AS d FROM backtest_prices WHERE code='sh000300'") or {}).get("d")
    stats["benchmark"] = benchmark

    # ② ETF 优先队列：无数据/滞后的排最前优先补，已同步的直接跳过（不发请求）
    #    此前按字典顺序全量请求 28 只：新加标的排在池尾，限流/配额下长期 0 数据
    #    （农业ETF/养殖ETF 案例），已同步的也白占请求额度。
    etf_pending, etf_skipped = [], 0
    for name, code in HOLDINGS_MAP.items():
        latest = (db.fetch_one(
            "SELECT MAX(date) AS d FROM backtest_prices WHERE code = %s", (code,))
            or {}).get("d")
        if latest and benchmark and latest >= benchmark:
            etf_skipped += 1
            continue
        etf_pending.append((name, code, latest or ""))
    etf_pending.sort(key=lambda x: x[2])   # ""（无数据）排最前，滞后越久越靠前
    stats["etf_skipped"] = etf_skipped
    stats["etf_pending"] = len(etf_pending)
    for name, code, _ in etf_pending:
        stats["codes"] += 1
        stats["rows"] += backfill(code, name)

    # ETF 滞后检测：ETF 是宏观回测/信号跟踪的数据底座，此前曾全部静默停在
    # 08-24 四个交易日无人发现（战法个股有 missing 检测，ETF 池没有）→ 补上
    stats["etf_missing"] = []
    if benchmark:
        for name, code in HOLDINGS_MAP.items():
            latest = (db.fetch_one(
                "SELECT MAX(date) AS d FROM backtest_prices WHERE code = %s", (code,))
                or {}).get("d")
            if not latest or latest < benchmark:
                stats["etf_missing"].append(f"{name}({code})停于{latest or '无数据'}")

    stock_items = _collect_strategy_codes()
    # ★ 2026-09-18（回测数据体检发现）：**并入「已入库的全部代码」**。
    #   `_collect_strategy_codes` 只取「战法信号 + 近 30 天上榜股」，于是**股票一旦
    #   掉出榜单就永久停更**：实测 73 只漏回填中 **70 只不在池里**（601012 停 08-21、
    #   000027 停 09-11 …）。它们不是停牌（源上明明有数据），而是再也没被轮到 ——
    #   而历史断档会**静默污染回测/撮合**。
    #   已入库的代码必须继续维护（成本只在"确实落后"时才产生，稳态下几乎为零）。
    _known = {r["code"]: (r["name"] or "") for r in db.fetch(
        "SELECT code, MAX(name) AS name FROM backtest_prices GROUP BY code") or []}
    _have = {c for c, _ in stock_items}
    _added = sorted(set(_known) - _have)
    for _c in _added:
        stock_items.append((_c, _known[_c]))
    stats["known_added"] = len(_added)
    stats["stock_pool"] = len(stock_items)
    codes = [c for c, _ in stock_items]
    name_of = dict(stock_items)
    latest_map = _latest_date_map(codes)

    # ①+② 优先队列：跳过已同步的，无数据排最前；近 7 天上榜股插队最优先
    rank_codes = _collect_rank_codes(7)
    pending, skipped = [], 0
    for c in codes:
        latest = latest_map.get(c)
        if latest and benchmark and latest >= benchmark:
            skipped += 1
            continue
        pending.append((c, name_of.get(c) or c, latest or ""))
    # 排序：上榜股(0)优先于普通股(1)，段内 ""（无数据）最前、滞后越久越靠前
    pending.sort(key=lambda x: (0 if x[0] in rank_codes else 1, x[2]))
    stats["stock_skipped"] = skipped
    stats["stock_pending"] = len(pending)
    stats["rank_priority"] = len([c for c in rank_codes
                                  if any(p[0] == c for p in pending)])

    # ③ 限量推进（quota=None = 不限量，补历史用）
    batch = pending if quota is None else pending[:quota]
    stats["stock_quota"] = quota if quota is not None else "unlimited"
    stats["stock_rows"] = 0
    for code, name, _ in batch:
        stats["codes"] += 1
        stats["stock_rows"] += backfill(code, name)

    # 缺失检测：回填后重新取最新日期，仍落后基准的进告警清单
    stats["stock_missing"] = []
    if benchmark:
        after_map = _latest_date_map([c for c, _, _ in pending])
        for c, name, _ in pending:
            latest = after_map.get(c)
            if not latest or latest < benchmark:
                stats["stock_missing"].append(f"{c}({name})")
    print(f"[backfill_daily] 完成: {stats}")
    return stats


def backfill_lagging() -> dict:
    """一次性补齐全部滞后个股（不限配额）。用于：
    换库/迁移/长假期后的大面积滞后（2026-09-13 实测 643 只中 499 只滞后，
    其中 141 只停 09-07 —— 排行榜 T+5 轨道 09-02 起整批缺席）。
    复用 backfill_daily 的池与优先队列，quota=None 不限量；
    499 只 × 1s 限速 ≈ 10 分钟。幂等，可重复跑（已同步的自动跳过）。
    """
    print("── 滞后个股全量补齐（不限配额）──")
    return backfill_daily(quota=None)


def check_signal_coverage(days: int = 3) -> dict:
    """
    检查最新信号的"回测可撮合率"：信号股票在价格库中是否有 ≥ 信号日的价格。
    无价格（missing）→ 信号永远无法撮合；价格早于信号日（stale）→ T+1 开盘无从撮合。
    供回填完成后告警——价格库滞后会令回测静默饿死（08-28→08-31 案例的教训）。
    """
    from datetime import timedelta
    since = (rules.beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.fetch(
        "SELECT scan_date, results_json FROM strategy_results "
        "WHERE scan_date >= %s AND count > 0 ORDER BY scan_date DESC LIMIT 5", (since,))
    seen, missing, stale = set(), [], []
    for r in rows or []:
        try:
            items = json.loads(r["results_json"]) or []
        except (json.JSONDecodeError, TypeError):
            continue
        scan_date = r["scan_date"]
        for it in items:
            code = str(it.get("code") or "").strip()
            if len(code) != 6 or code in seen:
                continue
            seen.add(code)
            row = db.fetch_one("SELECT MAX(date) AS d FROM backtest_prices WHERE code=%s", (code,))
            d = (row or {}).get("d")
            if not d:
                missing.append(code)
            elif d < scan_date:
                stale.append(f"{code}停于{d}")
    return {"total": len(seen), "missing": missing, "stale": stale}


def rebuild_all_full() -> dict:
    """一次性全量重建：对**已入库的每只**全量重拉并覆盖。

    ★ 用途（2026-09-18）：清除历史遗留的「库与源不一致」（见 `_basis_changed`）。
      只重建**已入库**的代码（未入库的不影响现有回测结果）。
      ⚠️ 必须 `replace=True` —— `save_prices` 默认 `ON CONFLICT DO NOTHING`
      **不会更新已有行**，不传 replace 就等于什么都没做。
      749 只 × 1s 限速 ≈ 13 分钟；幂等，可重复跑。
      跑完请再执行 `python scripts/audit_backtest_data.py` 复检。
    """
    global _EM_FAIL_STREAK
    print("── 全量重建（修复前复权接缝）──")
    rows = db.fetch("SELECT code, MAX(name) AS name FROM backtest_prices "
                    "GROUP BY code ORDER BY code") or []
    total, ok, fail = 0, 0, 0
    for i, r in enumerate(rows, 1):
        code, name = r["code"], (r["name"] or "")
        try:
            full = data.fetch_history(code, start=None)
        except Exception as e:
            full = None
            print(f"  [FAIL] {code} {name} 全量拉取异常: {str(e)[:60]}")
        if not full:
            fail += 1
            print(f"  [FAIL] {code} {name} 全量拉取为空")
        else:
            total += data.save_prices(code, name, full, replace=True)
            ok += 1
        if i % 50 == 0:
            print(f"  ... {i}/{len(rows)}（成功 {ok} / 失败 {fail} / 累计 {total} 行）")
        time.sleep(RATE_LIMIT)
    print(f"重建完成：{ok}/{len(rows)} 只，共写入 {total} 行（失败 {fail}）")
    return {"codes": len(rows), "ok": ok, "fail": fail, "rows": total}


def main():
    parser = argparse.ArgumentParser(description="回测历史数据回填")
    parser.add_argument("--all", action="store_true", help="全部回填")
    parser.add_argument("--etf", action="store_true", help="仅 ETF 池")
    parser.add_argument("--index", action="store_true", help="仅沪深300 基准")
    parser.add_argument("--stocks", action="store_true", help="仅战法个股")
    parser.add_argument("--lagging", action="store_true",
                        help="一次性补齐全部滞后个股（不限配额，换库/迁移后用）")
    parser.add_argument("--rebuild", action="store_true",
                        help="一次性全量重建：对已入库每只全量重拉并覆盖（修库与源不一致）")
    args = parser.parse_args()

    if args.rebuild:
        rebuild_all_full()
        return
    if args.lagging:
        backfill_lagging()
        return
    do_all = args.all or not (args.etf or args.index or args.stocks)
    if do_all or args.etf:
        backfill_etf()
    if do_all or args.index:
        backfill_index()
    if do_all or args.stocks:
        backfill_stocks()


if __name__ == "__main__":
    main()
