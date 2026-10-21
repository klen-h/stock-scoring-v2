#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】后端包（backend-pack.db.gz + indicators-pack.json.gz）增量更新

与 `update_pack_incremental.py` 同一套思路（历史留腾讯、只追加新 bar），差别是：
  ① 上一份包是 SQLite（klines/indicators/codes/meta）；
  ② 追加完要**重算指标**（compute_latest_indicators）+ 趋势健康度；
  ③ mainforce / news_score 从上一份 indicators **原样继承**（避免再连 Supabase
     拉 10 天资金流，也避免把标签清空；它们是标签列，不参与排序）。

用法：
  python scripts/update_backend_pack_incremental.py
  python scripts/update_backend_pack_incremental.py --dry-run
  # 完成后：python scripts/publish_packs_local.py
================================================================================
"""
import argparse
import gzip
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

BACKEND_URL = ("https://klen-h.github.io/stock-scoring-v2/data/"
               "backend-pack.db.gz")
DEFAULT_DIR = os.path.join(ROOT, "backend", "data", "kline")
DEFAULT_ZZ = os.path.join(ROOT, "data", "zzshare_daily.db")
BARS_MAX = 750


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _download(url, dest):
    import requests
    print(f"  下载上一份后端包: {url}")
    import shutil
    gz_tmp = dest + ".download.gz"
    # ★ 流式下载：66MB 一次性进内存/单次 read 容易被卡死（本次实测 12 分钟 0.3s CPU）。
    r = requests.get(url, timeout=(20, 60), stream=True)
    r.raise_for_status()
    got = 0
    with open(gz_tmp, "wb") as f:
        for chunk in r.iter_content(1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            got += len(chunk)
            if got % (10 * 1024 * 1024) < 1024 * 1024:
                print(f"    下载 {got / 1048576:.0f} MB …")
    print(f"  下载完成 {got / 1048576:.1f} MB，解压中…")
    # ★ Pages 上是**真 gzip 文件**（HTTP 不透明传输），requests 不会自动解压。
    with gzip.open(gz_tmp, "rb") as fi, open(dest, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1024 * 1024)
    os.remove(gz_tmp)
    print(f"  已解压: {dest} ({os.path.getsize(dest) / 1048576:.1f} MB)")
    return dest


def _merge_exdiv(prev_bars, zrows, days):
    """除权股：按 factor 比例把旧历史整体缩放，再追加新 bar（近似腾讯 qfq 重算）。

    为什么不用腾讯：本机 IP 正被 WAF 罚（冷却 120→240→300s，单只重试 3 次≈11 分钟），
    62 只回退会把任务拖到小时级且可能触发熔断。除权只影响**缺口内的分红比例**，
    用 zzshare 的 factor 比例缩放旧序列，误差限于该次分红的口径差（~1%），
    远好于"末根停在 2 天前"。返回 (bars, ok)。
    """
    if not prev_bars or not zrows:
        return prev_bars, False
    last_d = str(prev_bars[-1][0])[:10]
    by_d = {str(r[0])[:10]: r for r in zrows}
    if last_d not in by_d:
        return prev_bars, False
    f_old = by_d[last_d][5]
    f_new = zrows[-1][5]
    try:
        k = float(f_old) / float(f_new)
    except (TypeError, ValueError, ZeroDivisionError):
        return prev_bars, False
    if not (0.5 < k < 2.0):
        return prev_bars, False
    out = [[b[0], round(b[1] * k, 2), round(b[2] * k, 2),
            round(b[3] * k, 2), round(b[4] * k, 2), b[5]] for b in prev_bars]
    added = 0
    for d, o, h, l, c, v, f, paused in zrows:
        d = str(d)[:10]
        if d <= last_d or paused or not v:
            continue
        out.append([d, round(float(o), 2), round(float(h), 2),
                    round(float(l), 2), round(float(c), 2), float(v) / 100.0])
        added += 1
    return (out[-days:], added > 0)


def _load_prev_db(pack_dir):
    """本地包足够新（klines 行数正常）就用本地；否则从 Pages 下。"""
    gz = os.path.join(pack_dir, "backend-pack.db.gz")
    tmp = os.path.join(pack_dir, "_backend_prev.db")
    # 复用已解压的缓存（261MB）：避免每次重下 66MB / 重解压。
    if os.path.exists(tmp):
        try:
            c = sqlite3.connect(tmp)
            n = c.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
            d = c.execute("SELECT value FROM meta WHERE key='pack_date'").fetchone()
            c.close()
            if n > 500000:
                print(f"  上一份后端包: 缓存（{n} 行，pack_date={d[0] if d else '?'}）")
                return tmp
        except Exception:
            pass
        os.remove(tmp)
    if os.path.exists(gz) and os.path.getsize(gz) > 5 * 1024 * 1024:
        try:
            with gzip.open(gz, "rb") as f, open(tmp, "wb") as o:
                o.write(f.read())
            c = sqlite3.connect(tmp)
            n = c.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
            d = c.execute("SELECT value FROM meta WHERE key='pack_date'").fetchone()
            c.close()
            if n > 500000:
                print(f"  上一份后端包: 本地（{n} 行，pack_date={d[0] if d else '?'}）")
                return tmp
            print(f"  本地后端包只有 {n} 行（疑似旧/残缺）→ 改用 Pages")
        except Exception as e:
            print(f"  本地后端包不可用（{e}）→ 改用 Pages")
        os.remove(tmp)
    return _download(BACKEND_URL, tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack-dir", default=DEFAULT_DIR)
    ap.add_argument("--zz-db", default=DEFAULT_ZZ)
    ap.add_argument("--url", default=BACKEND_URL)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--tencent", action="store_true",
                    help="除权/缺数据时回退腾讯（默认关：当前 IP 被 WAF 罚，会拖到小时级）")
    args = ap.parse_args()

    print("=== 后端包增量更新（zzshare 只补新 bar）===")
    db_path = _load_prev_db(args.pack_dir)
    conn = sqlite3.connect(db_path)
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    prev_date = str(meta.get("pack_date") or "")
    codes_info = {c: (n, cap) for c, n, cap in
                  conn.execute("SELECT code, name, market_cap FROM codes")}
    print(f"  上一份包 pack_date={prev_date} / {len(codes_info)} 只")

    # 目标日
    from app.flash.rules import beijing_now, latest_completed_trading_day
    from update_pack_incremental import _zz_code, _zz_max_date, _merge_one
    expected = latest_completed_trading_day(beijing_now())
    target = _zz_max_date(args.zz_db)
    target_ymd = target.replace("-", "")
    if args.backfill and target_ymd < expected.replace("-", ""):
        print(f"  回填 zzshare {target or '2005-01-01'} ~ {expected} …")
        subprocess.run([sys.executable, os.path.join(
            ROOT, "scripts", "backfill_zzshare_daily.py"),
            "--start", target or "2005-01-01", "--end", expected], check=False)
        target = _zz_max_date(args.zz_db)
        target_ymd = target.replace("-", "")
    print(f"  zzshare 最新={target} / 应处理交易日={expected}")
    if not target or target_ymd <= prev_date:
        print("  已是最新，无需更新。")
        return 0

    # 读上一份 klines / indicators
    prev_bars = {}
    for code, date, o, h, l, c, v in conn.execute(
            "SELECT code, date, open, high, low, close, volume FROM klines "
            "ORDER BY code, date"):
        prev_bars.setdefault(code, []).append([date, o, h, l, c, v])
    prev_ind = {}
    for code, js in conn.execute("SELECT code, json FROM indicators"):
        try:
            prev_ind[code] = json.loads(js)
        except Exception:
            pass
    conn.close()
    print(f"  上一份 K 线: {len(prev_bars)} 只 / 指标 {len(prev_ind)} 只")

    # 合并
    zconn = sqlite3.connect(args.zz_db)
    merged, skipped = {}, []
    n_up = n_new = n_rebase = 0
    for code, bars in prev_bars.items():
        ts = _zz_code(code)
        rows = []
        if ts and bars:
            rows = zconn.execute(
                "SELECT date, open, high, low, close, volume, factor, is_paused "
                "FROM daily WHERE code=? AND date>=? AND date<=? ORDER BY date",
                (ts, str(bars[-1][0])[:10], target)).fetchall()
        m, st = _merge_one(bars, rows, BARS_MAX)
        if st == "ok":
            merged[code] = m
            n_up += 1
        elif st == "no_new":
            merged[code] = bars
            n_new += 1
        elif st == "exdiv":
            # 除权：factor 比例缩放旧历史 + 追加新 bar（腾讯当前被 WAF 罚，不回退）
            m2, ok = _merge_exdiv(bars, rows, BARS_MAX)
            if ok:
                merged[code] = m2
                n_rebase += 1
            else:
                merged[code] = bars
                skipped.append(code)
        else:
            merged[code] = bars
            skipped.append(code)
    zconn.close()
    print(f"  增量: 更新 {n_up} / 无新 bar {n_new} / 除权重算 {n_rebase} "
          f"/ 跳过 {len(skipped)}（多为 ETF/指数，zzshare 无数据）")

    # 可选：腾讯回退（默认关；当前 IP 被 WAF 罚，62 只会拖到小时级）
    if args.tencent and skipped:
        gkp = _load_mod("gkp", os.path.join(ROOT, "scripts", "generate-kline-pack.py"))
        gkp.PACK_QPS = 0.6
        for i, code in enumerate(skipped, 1):
            try:
                kl = gkp.fetch_kline(code, 1100)
            except Exception as e:
                kl = None
                print(f"  腾讯回退异常 {code}: {e}")
            if kl:
                merged[code] = kl[-BARS_MAX:]
            if i % 10 == 0:
                print(f"  回退腾讯 {i}/{len(skipped)} …")

    # 兜底：prev_bars 里没进 merged 的（不该有）
    for code, bars in prev_bars.items():
        merged.setdefault(code, bars)

    if args.dry_run:
        b = merged.get("000001") or []
        print(f"  [dry-run] 不写文件；000001 末根={b[-1] if b else None}")
        return 0

    # 重算指标
    from app.scoring.indicator_cache import compute_latest_indicators
    gbp = _load_mod("gbp", os.path.join(ROOT, "scripts", "generate_backend_pack.py"))
    ind_out = {}
    for i, (code, bars) in enumerate(merged.items()):
        if not bars:
            continue
        try:
            dict_bars = [{"date": x[0], "open": x[1], "high": x[2],
                          "low": x[3], "close": x[4], "volume": x[5]} for x in bars]
            ind = compute_latest_indicators(dict_bars)
            if not ind or ind.get("ma5") is None:
                continue
            p = prev_ind.get(code) or {}
            if p.get("mainforce"):
                ind["mainforce"] = p["mainforce"]      # 标签继承（不重连 Supabase）
            if p.get("news_score"):
                ind["news_score"] = p["news_score"]
            ind_out[code] = ind
        except Exception as e:
            print(f"  指标失败 {code}: {e}")
        if (i + 1) % 400 == 0:
            print(f"  指标进度: {i + 1}/{len(merged)}")

    if len(merged) < len(prev_bars) * 0.8 or not ind_out:
        print(f"::error::护栏：K线 {len(merged)}/{len(prev_bars)}、指标 {len(ind_out)}"
              f" —— 中止，不写残缺包")
        return 5
    print(f"  指标: {len(ind_out)} 只，计算趋势健康度…")
    th = gbp.compute_trend_health_batch(ind_out)
    for code, v in th.items():
        ind_out[code]["trend_health"] = v

    # 写包
    date_str = beijing_now().strftime("%Y%m%d")
    quotes = {c: {"name": v[0], "market_cap": v[1]} for c, v in codes_info.items()}
    out_db = os.path.join(args.pack_dir, "backend-pack.db")
    gbp.write_sqlite(out_db, date_str, quotes, merged, ind_out)
    gz = out_db + ".gz"
    with open(out_db, "rb") as fi, gzip.open(gz, "wb") as fo:
        fo.write(fi.read())
    os.remove(out_db)
    print(f"  压缩包: {gz} ({os.path.getsize(gz) / 1048576:.1f} MB)")
    gbp.write_indicators_pack(
        os.path.join(args.pack_dir, "indicators-pack.json.gz"), date_str, ind_out)
    print(f"=== 完成: K线 {len(merged)} 只 / 指标 {len(ind_out)} 只 ===")
    print("  下一步: python scripts/publish_packs_local.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
