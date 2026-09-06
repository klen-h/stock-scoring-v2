#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】生成"后端数据包"backend-pack.db.gz（SQLite，DATA_SOURCE=pack/local 用）
================================================================================

为什么是 SQLite 而不是 JSON：
  45MB 的 JSON 在 Python 里解析成 dict 后常驻 ~150-200MB（Render 512MB 直接 OOM）。
  SQLite 落在磁盘上按需查单只（<5ms），常驻内存 ≈0。前端不读这个文件
  （前端另有 indicators-pack.json.gz + kline-pack）。

三张表：
  klines      (code, date, open, high, low, close, volume)  PK(code, date)
  indicators  (code PRIMARY KEY, json)                      ← 含 _series
  codes       (code PRIMARY KEY, name, market_cap)
  meta        (key PRIMARY KEY, value)                      ← pack_date

运行（GitHub Actions 的 kline-data 工作流调用；也可本地手动）：
  python scripts/generate_backend_pack.py --output-dir ./data/kline
产出（只发 latest，避免历史版本在 Pages 上堆积）：
  backend-pack.db.gz + indicators-pack.json.gz
================================================================================
"""

import argparse
import gzip
import importlib.util
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPTS_DIR, "..", "backend")
# ★ fetch 的 days 语义是"日历天"（起点 now-(days+30)，再 [-days:] 截交易日根）：
#   传 500 实际只得到 ~356 根交易日（起点被卡）。1100 日历天 ≈ 750 根交易日，
#   与 DB backtest_prices 的深度对齐（手册回测 2 年目标 ≈ 500 交易日，留余量）。
BACKEND_KLINE_CAL_DAYS = 1100
CAP_TOP_N = 800                    # 市值兜底池上限

sys.path.insert(0, BACKEND_DIR)


def load_gkp():
    """加载 generate-kline-pack.py（文件名带连字符，需 importlib）。
    复用其成熟的：股票池/批量行情/两轮重试+WAF 退避的 K 线拉取。"""
    spec = importlib.util.spec_from_file_location(
        "gkp", os.path.join(SCRIPTS_DIR, "generate-kline-pack.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_recent_flow(codes: list, days: int = 10) -> dict:
    """读近 N 日主力资金流（只取 main_net/main_pct）——避免把 130 天整表拖下来。

    mainforce 的强口径「高位高获利 × 主力流出」只需要近 5 日 main_pct；
    免费版 Supabase 有出站流量预算，全表（700 只×130 天 ≈ 9 万行）没必要。
    读取失败返回 {} —— mainforce 自动退化为弱口径（高位高获利 + 出货阶段），
    与后端 overlay 的降级路径完全一致，不会失败。
    """
    if not codes:
        return {}
    try:
        from app import db
        from datetime import date as _date, timedelta
        cutoff = (_date.today() - timedelta(days=days + 12)).isoformat()
        sql = ("SELECT code, date, main_net, main_pct FROM mainflow_history "
               "WHERE code = ANY(%s) AND date >= %s ORDER BY code, date ASC")
        rows = db.fetch(sql, (list(codes), cutoff))
        out = {}
        for r in rows:
            out.setdefault(r["code"], []).append({
                "date": str(r["date"]),
                "main_net": r["main_net"],
                "main_pct": r["main_pct"],
            })
        print(f"  资金流: {len(out)} 只（窗口 {cutoff} 起）")
        return out
    except Exception as e:
        print(f"  ⚠️ 资金流读取失败（mainforce 退化为弱口径）: {e}")
        return {}


def compute_trend_health_batch(ind_out: dict) -> dict:
    """批量计算趋势健康度（5 维度：量能/支撑/深度/动量/均线 → 洗盘 vs 真跌）。

    ★ 零外部数据依赖：输入只有指标序列 _series（含 close/high/volume/ma5/ma20/
      ma60/dif），与后端 engine._calc_trend_health 同一函数、同一口径——
      本地/前端无需再复刻一遍算法（避免又一处 JS↔Python 对齐负担）。
    """
    try:
        from app.scoring.engine import ScoreEngine
    except Exception as e:
        print(f"  ⚠️ 评分引擎导入失败，跳过趋势健康度: {e}")
        return {}

    eng = ScoreEngine()
    out = {}
    for code, ind in ind_out.items():
        series = ind.get("_series")
        if not series or len(series) < 30:
            continue
        try:
            th = eng._calc_trend_health(series)
            if th and th.get("verdict"):
                out[code] = th
        except Exception as e:
            print(f"  趋势健康度计算失败 {code}: {e}")

    dist = {}
    for v in out.values():
        dist[v.get("verdict")] = dist.get(v.get("verdict"), 0) + 1
    print(f"  趋势健康度: {len(out)} 只 " +
          " / ".join(f"{k} {v}" for k, v in sorted(dist.items(), key=lambda x: -x[1])))
    return out


def compute_news_scores(codes: list) -> dict:
    """批量消息面情绪分（东财 7×24 快讯）——与后端 _batch_news_scores 同口径。

    ★ 只存非 0 的：绝大多数股票没有快讯，把 0 全存进包纯属浪费；前端模板用
      `item.news_score != null` 判断，没存就显示 '-'，与"无消息"语义一致。
    ★ 语义注意：这是「打包时刻（收盘后）」的快照。快讯 24h 滚动且分数带时间
      衰减（decay_weight），本地模式不会实时刷新——所以它反映的是收盘时的
      消息面，不是盘中实时值。不参与综合评分，仅榜单参考列。
    """
    try:
        from app.eastmoney_news import get_global_news
        from app.news_sentiment import score_stock_news
    except Exception as e:
        print(f"  ⚠️ 消息面模块导入失败，跳过: {e}")
        return {}
    try:
        items = get_global_news()
        by_code = {}
        for it in items or []:
            for c in (it.get("stocks") or []):
                by_code.setdefault(c, []).append(it)
        out = {}
        for code in codes:
            its = by_code.get(code)
            if not its:
                continue
            try:
                s = score_stock_news(its).get("score", 0)
                if s:
                    out[code] = s
            except Exception:
                continue
        print(f"  消息面: 快讯 {len(items or [])} 条 → 非 0 分覆盖 {len(out)} 只")
        return out
    except Exception as e:
        print(f"  ⚠️ 消息分计算失败（跳过，不影响其它字段）: {e}")
        return {}


def compute_mainforce_batch(klines_raw: dict, quotes: dict) -> dict:
    """批量计算主力行为叠加（与后端 mainforce_state 日批同一 overlay 函数）。

    ★ 时序根治：不再等 Render 17:30 的日批，而是打包时就地算——复用刚拉到的
      当日 K 线 + 库里当日资金流，产出的就是「当日收盘口径」，与后端日批同源。
    ★ regime 传 None：乘数闸门本就在后端读端判定（MAINFORCE_MODE=auto +
      regime 命中才 ×0.85），这里只出标签，不影响排序口径。
    """
    try:
        from app.mainforce.overlay import mainforce_overlay
    except Exception as e:
        print(f"  ⚠️ mainforce 引擎导入失败，跳过（排行榜将无出货/吸筹标签）: {e}")
        return {}

    flow_map = _load_recent_flow(list(klines_raw.keys()))
    out = {}
    t0 = time.time()
    for i, (code, bars) in enumerate(klines_raw.items()):
        q = quotes.get(code) or {}
        # 流通股本：quotes 的 float_cap 单位是「亿元」（腾讯 fields[44]）
        fs = None
        try:
            cap_yi = float(q.get("float_cap") or 0)
            price = float(q.get("price") or 0)
            if cap_yi > 0 and price > 0:
                fs = cap_yi * 1e8 / price
        except (TypeError, ValueError):
            fs = None
        try:
            dict_bars = [{"date": b[0], "open": b[1], "high": b[2],
                          "low": b[3], "close": b[4], "volume": b[5]} for b in bars]
            ov = mainforce_overlay(dict_bars, flow_rows=flow_map.get(code),
                                   float_shares=fs, regime=None)
            if not ov:
                continue
            chip = ov.get("chip") or {}
            out[code] = {
                "phase": ov.get("phase"),
                "phase_cn": ov.get("phase_cn"),
                "signal": ov.get("signal"),
                "signal_cn": ov.get("signal_cn"),
                "reason": ov.get("reason"),
                "flow5_amt": ov.get("flow5_amt"),
                "mult": ov.get("mult") or 1.0,
                "active": bool(ov.get("active")),
                "chip": chip,
                # ★ 前端 ScoreRank/StockDetail 直接读的扁平字段（避免嵌套取值的空判断）
                "price_pos": chip.get("price_pos"),
                "winner_ratio": chip.get("winner_ratio"),
            }
        except Exception as e:
            print(f"  mainforce 计算失败 {code}: {e}")
        if (i + 1) % 200 == 0:
            print(f"  mainforce 进度: {i + 1}/{len(klines_raw)}")

    n_dist = sum(1 for v in out.values() if v.get("signal") == "distribution")
    n_acc = sum(1 for v in out.values() if v.get("signal") == "accum")
    n_flow = sum(1 for v in out.values() if v.get("flow5_amt") is not None)
    print(f"  主力行为: {len(out)} 只（出货嫌疑 {n_dist} / 吸筹区 {n_acc}；"
          f"含资金流强口径 {n_flow} 只，其余弱口径），耗时 {time.time() - t0:.0f}s")
    return out


def supabase_kline_codes() -> list:
    """读 Supabase kline_cache 的代码清单（只取 code 列 ~11KB）。失败返回 []。"""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("  未配置 DATABASE_URL，跳过 Supabase 代码清单（仅用市值池）")
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT code FROM kline_cache")
        out = [r[0] for r in cur.fetchall() if r[0] and len(r[0]) == 6]
        conn.close()
        print(f"  Supabase kline_cache 代码清单: {len(out)} 只")
        return out
    except Exception as e:
        print(f"  ⚠️ 读取 Supabase 代码清单失败（忽略，仅用市值池）: {e}")
        return []


def write_sqlite(path: str, date_str: str, quotes: dict, klines_raw: dict,
                 ind_out: dict) -> None:
    """K 线 + 指标 + 代码清单写 SQLite（PK 自带索引）。"""
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE klines (
            code TEXT NOT NULL, date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (code, date)
        )""")
    conn.execute("CREATE TABLE codes (code TEXT PRIMARY KEY, name TEXT, market_cap REAL)")
    conn.execute("CREATE TABLE indicators (code TEXT PRIMARY KEY, json TEXT)")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    rows = []
    for code, bars in klines_raw.items():
        for b in bars:
            rows.append((code, b[0], b[1], b[2], b[3], b[4], b[5]))
    conn.executemany("INSERT OR REPLACE INTO klines VALUES (?,?,?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT OR REPLACE INTO codes VALUES (?,?,?)",
        [(c, (quotes.get(c) or {}).get("name", ""),
          (quotes.get(c) or {}).get("market_cap", 0) or 0) for c in klines_raw])
    conn.executemany(
        "INSERT OR REPLACE INTO indicators VALUES (?,?)",
        [(c, json.dumps(ind, ensure_ascii=False)) for c, ind in ind_out.items()])
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('pack_date', ?)", (date_str,))
    conn.commit()
    conn.close()
    print(f"  SQLite: {path} ({os.path.getsize(path) / 1048576:.1f} MB, "
          f"bars={len(rows)}, indicators={len(ind_out)})")


def write_indicators_pack(path: str, date_str: str, ind_out: dict) -> None:
    """前端评分 Worker 用的小包（只含指标，~几 MB）。"""
    pack = {"version": 1, "date": date_str, "indicators": ind_out}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  指标包: {path} ({os.path.getsize(path) / 1048576:.1f} MB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="./data/kline")
    ap.add_argument("--cap-top", type=int, default=CAP_TOP_N)
    ap.add_argument("--quotes-file", default="./data/kline/realtime-quotes.json",
                    help="复用前端步骤已拉取的全市场行情（避免全市场拉两遍）")
    ap.add_argument("--quotes-url", default=os.environ.get("PACK_QUOTES_URL", ""),
                    help="行情来源 URL（与前端 job 拆到不同 workflow 后从 Pages 取）")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("PACK_WORKERS", 5)),
                    help="K 线拉取并发数（默认 5；串行会顶穿 Actions 90 分钟上限）")
    ap.add_argument("--no-mainforce", action="store_true",
                    help="跳过主力行为计算（快速跑通 K 线+指标时用）")
    args = ap.parse_args()

    print("=== 后端数据包生成（backend-pack.db，SQLite）===")
    gkp = load_gkp()

    # 1. 实时行情（拿名称/市值 + 市值池）
    #    ★ 优先复用前端包步骤落盘的共享行情文件（0 请求）；过期/缺失才自拉
    quotes = None
    if args.quotes_file and os.path.exists(args.quotes_file):
        try:
            if time.time() - os.path.getmtime(args.quotes_file) < 6 * 3600:
                with open(args.quotes_file, encoding="utf-8") as f:
                    quotes = json.load(f)
                print(f"\n[1/4] 实时行情：复用共享文件（{len(quotes)} 只，0 请求）")
        except Exception as e:
            print(f"\n[1/4] 共享行情读取失败，自拉: {e}")
    # ★ 与前端 job 拆到不同 workflow 后没有本地文件 → 从 Pages 拉共享行情
    #   （~150KB，1 个请求；比 1564 只重新批量拉便宜得多）
    if quotes is None and args.quotes_url:
        try:
            import requests
            r = requests.get(args.quotes_url, timeout=20)
            r.raise_for_status()
            quotes = r.json()
            print(f"\n[1/4] 实时行情：复用 Pages 共享行情（{len(quotes)} 只，1 请求）")
        except Exception as e:
            print(f"\n[1/4] Pages 行情读取失败，自拉: {e}")
    if quotes is None:
        print("\n[1/4] 拉取实时行情...")
        quotes = gkp.fetch_realtime_batch(gkp.build_stock_pool())
    by_cap = sorted(quotes.items(),
                    key=lambda kv: kv[1].get("market_cap", 0) or 0, reverse=True)
    cap_codes = [c for c, _ in by_cap[:args.cap_top]]

    # 2. 股票池 = Supabase 现有清单 ∪ 市值前 N ∪ 指数基准/宏观 ETF
    #    ★ 指数与 ETF 必须在包里：DATA_SOURCE=pack 模式下 regime 判定
    #      （sh000300）与宏观回测（sh510300 等）都从包读——漏了会报
    #      "沪深300 历史数据不足"（2026-09-06 实测，读取层已有 DB 兜底双保险）
    try:
        from app.signals.tracker import HOLDINGS_MAP
        etf_codes = list(HOLDINGS_MAP.values())
    except Exception:
        etf_codes = []
    sb_codes = [c for c in supabase_kline_codes() if c in quotes]
    pool_all = list(dict.fromkeys(sb_codes + cap_codes + ["sh000300"] + etf_codes))

    # ★ 质量减法（2026-09-06，Actions 2h 超时对策）：剔除
    #   科创/创业（688/689/300/301/302，评分与战法池本就排除）、
    #   ST/亏损（PE<=0）、总市值<50亿（与战法扫描 50亿门槛对齐）。
    #   这些股票在评分/排行/战法全链路都不会被消费，拉 750 根日线纯属
    #   浪费腾讯配额与 Actions 时长（实测 1394 只 → 预计 ~700 只）。
    #   指数/ETF（非 6 位码）不受过滤。
    def _pack_quality(code: str, q: dict) -> bool:
        if len(code) != 6 or not code.isdigit():
            return True                      # 指数/ETF 保留
        name = (q.get("name") or "").replace(" ", "").upper()
        if name.startswith(("ST", "*ST", "SST")):
            return False
        if code.startswith(("688", "689", "300", "301", "302")):
            return False
        if (q.get("pe") or 0) <= 0:          # 亏损或无盈利数据
            return False
        if (q.get("market_cap") or 0) < 50:  # 亿元（gkp 实时行情口径）
            return False
        return True

    kept_stocks = [c for c in pool_all
                   if len(c) == 6 and c not in ("sh000300",)
                   and _pack_quality(c, quotes.get(c) or {})]
    # 市值降序拉取：即使超时中断，质量池（大市值优先）已完整落包
    kept_stocks.sort(key=lambda c: (quotes.get(c) or {}).get("market_cap") or 0,
                     reverse=True)
    pool = ["sh000300"] + [c for c in etf_codes] + kept_stocks
    dropped = len(pool_all) - len(pool)
    print(f"\n[2/4] 股票池: 全量 {len(pool_all)} → 质量过滤后 {len(pool)} 只"
          f"（剔除科创创业/ST/亏损/<50亿 共 {dropped} 只），市值降序拉取")

    # 3. 拉 ~750 根交易日线（复用两轮重试 + WAF 退避）
    #    ★ fetch 的 days 参数是"日历天"（起点 now-(days+30)），500 会被起点卡成
    #      ~356 根交易日 —— 传 1100 日历天才能拿到与 DB backtest_prices 对齐的
    #      ~750 根（手册回测 2 年目标 ≈ 500 交易日，留足余量）
    print(f"\n[3/4] 拉取 K 线（~750 根/只，并发 {args.workers}，含 WAF 退避）...")
    klines_raw = gkp.fetch_all_klines(pool, BACKEND_KLINE_CAL_DAYS, workers=args.workers)

    # 4. 计算指标（复用后端引擎，与 indicator_cache 同口径）
    print("\n[4/4] 计算预计算指标...")
    try:
        from app.scoring.indicator_cache import compute_latest_indicators
    except Exception as e:
        print("::error::指标引擎导入失败（%s）—— 依赖缺失，检查工作流 pip install "
              "（需含 fastapi 等完整后端依赖）" % e)
        sys.exit(6)

    ind_out = {}
    for i, code in enumerate(klines_raw):
        try:
            dict_bars = [{"date": b[0], "open": b[1], "high": b[2],
                          "low": b[3], "close": b[4], "volume": b[5]}
                         for b in klines_raw[code]]
            ind = compute_latest_indicators(dict_bars)
            if ind and ind.get("ma5") is not None:
                ind_out[code] = ind
        except Exception as e:
            print(f"  指标计算失败 {code}: {e}")
        if (i + 1) % 200 == 0:
            print(f"  指标进度: {i + 1}/{len(klines_raw)}")

    # ★ 护栏基准必须先取：下面 mainforce 会给无指标的股票补挂标签条目，
    #   若用合并后的 ind_out 判断，会在「指标全挂」时误判为成功而发出残缺包。
    ind_count_raw = len(ind_out)

    # 4.2 趋势健康度（5 维度诊断）——纯指标计算，零外部数据；入包后前端详情页
    #     本地模式也能显示「趋势健康 4/5」，不再依赖 /api/score/{code}
    print("\n[4.2/4] 计算趋势健康度...")
    th_out = compute_trend_health_batch(ind_out)
    for code, th in th_out.items():
        ind_out[code]["trend_health"] = th

    # 4.3 消息面情绪分（东财快讯，1 次全局请求）——只存非 0 的，失败静默跳过。
    #     不参与综合评分，仅榜单参考列；值为打包时刻（收盘后）快照。
    print("\n[4.3/4] 计算消息面情绪分...")
    news_out = compute_news_scores(list(klines_raw.keys()))
    for code, s in news_out.items():
        if code in ind_out:
            ind_out[code]["news_score"] = s
        else:
            ind_out[code] = {"news_score": s}

    # 4.5 主力行为叠加（出货嫌疑/吸筹区标签）——就地算，不再依赖 Render 17:30 日批。
    #    结果并入每只股票的指标对象：前端 indicators-pack 与后端 sqlite indicators
    #    同一份数据，排行榜/详情页本地模式直接读，零后端请求。
    mf_out = {}
    if args.no_mainforce:
        print("\n[4.5/4] 主力行为：已跳过（--no-mainforce）")
    else:
        print("\n[4.5/4] 计算主力行为叠加（筹码×资金流）...")
        mf_out = compute_mainforce_batch(klines_raw, quotes)
        merged = 0
        for code, mf in mf_out.items():
            if code in ind_out:
                ind_out[code]["mainforce"] = mf
                merged += 1
            else:
                # 指标缺失（<30 根等）但该股仍在包里 → 单独兜一个只含 mainforce 的对象，
                # 保证排行榜能拿到标签（评分会走现算兜底路径）
                ind_out[code] = {"mainforce": mf}
        print(f"  并入指标包: {merged} 只（另有 {len(mf_out) - merged} 只无指标、仅挂标签）")

    # 完整性护栏：K 线不足池的 80% 或指标启用却 0 只成功 → 报错中止（不发出残缺包）
    if len(klines_raw) < len(pool) * 0.8:
        print(f"::error::K线仅拉到 {len(klines_raw)}/{len(pool)} 只（<80%）"
              f"—— 可能被腾讯限流，中止不发包")
        sys.exit(5)
    if klines_raw and ind_count_raw == 0:
        print("::error::K线拉取成功但指标计算 0 只成功 —— 通常是依赖缺失，"
              "检查工作流 pip install（需含 fastapi 等后端依赖）")
        sys.exit(6)

    date_str = datetime.now().strftime("%Y%m%d")
    os.makedirs(args.output_dir, exist_ok=True)
    db_path = os.path.join(args.output_dir, "backend-pack.db")
    write_sqlite(db_path, date_str, quotes, klines_raw, ind_out)

    gz_path = db_path + ".gz"
    with open(db_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        f_out.write(f_in.read())
    os.remove(db_path)
    print(f"  压缩包: {gz_path} ({os.path.getsize(gz_path) / 1048576:.1f} MB)")

    write_indicators_pack(os.path.join(args.output_dir, "indicators-pack.json.gz"),
                          date_str, ind_out)
    print(f"\n=== 完成: K线 {len(klines_raw)} 只 / 指标 {len(ind_out)} 只 ===")


if __name__ == "__main__":
    main()

