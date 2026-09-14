#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【文件作用】本地开发数据同步：下载 backend-pack（SQLite）到本地（零 Supabase 流量）

做什么：从 GitHub Pages 下载 backend-pack.db.gz → 解压到
  backend/data/pack/backend-pack.db

然后本地开发零 Supabase 流量的用法：backend/.env 里加一行 DATA_SOURCE=local
（K线/指标/backtest_prices 读取层自动改读本地 SQLite；业务小表如模拟盘/自选
 走本地 SQLite——DATABASE_URL 留空即可，与生产数据分叉）
更新数据：隔几天重跑本脚本（**每个交易日**由 GitHub Actions 重新生成：
K 线包 18:00 触发、后端包 19:00 触发（均走 cron-job.org，北京时间），
后端包约 1h43m → **~20:43 完成**。详见 `.github/workflows/kline-data.yml` /
`backend-pack.yml`）。

★ 2026-09-13 血泪教训：**包陈旧时不更新会导致本地所有 K 线读取静默回退查
  Supabase**（本地开发一天能拉出近 600MB egress）。包日期落后于"此刻本应可用的
  最新交易日"即判陈旧，此时 `pack_source` 会打印 [WARN] 提示——看到就立刻重跑本脚本。
  周一/长假后第一个交易日尤其容易踩到（"应可用日"已前移，旧包必然判陈旧）。

★ 2026-09-15 下载加速（实测数据驱动）：GitHub Pages（Fastly）对**单连接**是
  "首段突发 + 随后强限速"——实测 15s 窗口下单线程仅 0.10 MB/s（60 秒才 1.5MB），
  而 4 并发分片达 0.82 MB/s（**8.2x**）；6s 窗口单线程却有 1.19 MB/s，正是突发段。
  故改为**并发分片下载**（每片独立 Range，天然支持断点续传 → 断了不用从头下）。
  另实测排除的两个"看起来更快的源"：jsDelivr 对 44.9MB 文件直接 **403**
  （单文件限 20MB）；cdn.statically.io **连接超时**不可达。GitHub Pages 仍是唯一可用源。
"""

import argparse
import concurrent.futures
import gzip
import os
import shutil
import sys
import threading
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPTS_DIR, "..", "backend")
PACK_DIR = os.path.join(BACKEND_DIR, "data", "pack")
PACK_DB = os.path.join(PACK_DIR, "backend-pack.db")
PACK_GZ = PACK_DB + ".gz"
DEFAULT_URL = ("https://klen-h.github.io/stock-scoring-v2/data"
               "/backend-pack.db.gz")

# 并发数：实测 4 片已有 8x 加速；再高收益递减且易触发 CDN 限流
DEFAULT_THREADS = int(os.environ.get("PACK_DOWNLOAD_THREADS", "6"))
_PART_RETRIES = 5
_READ_CHUNK = 1 << 16


def _probe(url):
    """探测 (总字节数, 是否支持 Range)。失败返回 (0, False)。"""
    import requests
    for use_head in (True, False):
        try:
            if use_head:
                r = requests.head(url, timeout=30, allow_redirects=True)
            else:
                r = requests.get(url, timeout=30, stream=True)
            total = int(r.headers.get("Content-Length") or 0)
            ranges = (r.headers.get("Accept-Ranges") or "").lower() == "bytes"
            r.close()
            if total > 0:
                return total, ranges
        except Exception:
            continue
    return 0, False


def _download_single(url, dest):
    """单连接顺序下载（源不支持 Range 时的兜底）。"""
    import requests
    r = requests.get(url, timeout=180, stream=True)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(_READ_CHUNK):
            f.write(chunk)


def _fetch_part(url, idx, start, end, part_path, stop_evt):
    """下载/续传单个分片（每片独立 Range，重试时从未完成处继续）。"""
    import requests
    expect = end - start + 1
    for attempt in range(_PART_RETRIES):
        if stop_evt.is_set():
            return False
        done = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        if done >= expect:
            return True
        try:
            r = requests.get(url, headers={"Range": f"bytes={start + done}-{end}"},
                             timeout=(15, 60), stream=True)
            if r.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {r.status_code}")
            with open(part_path, "ab" if done else "wb") as f:
                for chunk in r.iter_content(_READ_CHUNK):
                    if stop_evt.is_set():
                        r.close()
                        return False
                    f.write(chunk)
            r.close()
        except Exception as e:
            if attempt == _PART_RETRIES - 1:
                print(f"  [分片{idx}] 放弃: {e}")
                return False
            time.sleep(1 + attempt)   # 退避后从未完成处续传
    return os.path.getsize(part_path) >= expect


def _download_gz(url, dest_gz, threads=DEFAULT_THREADS):
    """并发分片下载到 dest_gz（支持断点续传；已完成的分片重跑时直接复用）。"""
    total, ranges = _probe(url)
    if total <= 0 or not ranges or total < 4 * 1048576:
        if total > 0 and ranges:
            print(f"  文件较小，单线程下载")
        else:
            print("  源不支持 Range/长度未知 → 单线程下载")
        _download_single(url, dest_gz)
        return

    n = max(1, min(threads, total // (4 * 1048576) + 1))
    span = (total + n - 1) // n
    parts = [os.path.join(PACK_DIR, f"{os.path.basename(dest_gz)}.part{i}") for i in range(n)]
    ranges_list = [(i * span, min(total - 1, (i + 1) * span - 1)) for i in range(n)]
    resumed = sum(os.path.getsize(p) for p in parts if os.path.exists(p))
    print(f"  共 {total / 1048576:.1f} MB，{n} 并发分片"
          + (f"（续传已有 {resumed / 1048576:.1f} MB）" if resumed else ""))

    stop_evt = threading.Event()
    t0 = time.time()

    def _monitor():
        while not stop_evt.is_set():
            got = sum(os.path.getsize(p) for p in parts if os.path.exists(p))
            el = time.time() - t0
            sp = got / 1048576 / el if el > 0 else 0
            left = (total - got) / 1048576 / sp if sp > 0 else 0
            print(f"  {got / 1048576:6.1f}/{total / 1048576:.1f} MB  "
                  f"{sp:5.2f} MB/s  剩余≈{left / 60:.1f} 分钟", flush=True)
            stop_evt.wait(5)

    mon = threading.Thread(target=_monitor, daemon=True)
    mon.start()
    ok = True
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(_fetch_part, url, i, s, e, parts[i], stop_evt)
                    for i, (s, e) in enumerate(ranges_list)]
            for f in concurrent.futures.as_completed(futs):
                if not f.result():
                    ok = False
    except KeyboardInterrupt:
        stop_evt.set()
        print("\n  已中断：分片文件保留，重跑本脚本将从断点续传")
        raise
    finally:
        stop_evt.set()
        mon.join(timeout=1)

    if not ok:
        raise RuntimeError("部分分片下载失败（分片保留，重跑可续传）")
    el = time.time() - t0
    print(f"  下载完成: {total / 1048576:.1f} MB / {el:.1f}s "
          f"→ {total / 1048576 / el:.2f} MB/s")

    # 按分片顺序拼接（流式，内存恒定）
    tmp = dest_gz + ".merging"
    with open(tmp, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out, 1 << 20)
    got = os.path.getsize(tmp)
    if got != total:
        os.remove(tmp)
        raise RuntimeError(f"拼接后大小不符: {got} != {total}（分片保留，重跑可续传）")
    os.replace(tmp, dest_gz)
    for p in parts:
        os.remove(p)


def _download(url, threads=DEFAULT_THREADS):
    os.makedirs(PACK_DIR, exist_ok=True)
    print(f"下载数据包: {url}")
    gz_tmp = PACK_GZ + ".tmp"
    _download_gz(url, gz_tmp, threads=threads)
    print("解压中（流式）...")
    db_tmp = PACK_DB + ".tmp"
    # 流式解压（与 pack_source 口径一致）：原 f_in.read() 会把整个 db 一次性读进
    # 内存（实测 180MB+），分块拷贝后内存恒定 ≈1MB
    with gzip.open(gz_tmp, "rb") as f_in, open(db_tmp, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, 1 << 20)
    os.replace(db_tmp, PACK_DB)
    os.remove(gz_tmp)
    print(f"已保存: {PACK_DB} ({os.path.getsize(PACK_DB) / 1048576:.1f} MB)")


def _hint_env():
    env_path = os.path.join(BACKEND_DIR, ".env")
    has_flag = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            has_flag = any(line.strip().startswith("DATA_SOURCE=")
                           for line in f if not line.strip().startswith("#"))
    print("\n下一步：")
    if not has_flag:
        print(f"  在 {env_path} 加一行: DATA_SOURCE=local")
    else:
        print("  backend/.env 已有 DATA_SOURCE 配置，无需改动")
    print("  之后正常 python run.py 即可，读侧自动走本地数据包")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("PACK_URL", DEFAULT_URL))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--threads", type=int, default=DEFAULT_THREADS,
                    help=f"并发分片数（默认 {DEFAULT_THREADS}，实测 4 片已 ~8x 加速）")
    args = ap.parse_args()

    if (not args.force and os.path.exists(PACK_DB)
            and time.time() - os.path.getmtime(PACK_DB) < 20 * 3600):
        mtime = time.strftime("%m-%d %H:%M",
                              time.localtime(os.path.getmtime(PACK_DB)))
        print(f"本地包较新（{mtime}），跳过下载（--force 可强制）")
    else:
        _download(args.url, threads=max(1, args.threads))
    _hint_env()


if __name__ == "__main__":
    main()
