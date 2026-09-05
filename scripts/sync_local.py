#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【文件作用】本地开发数据同步：下载 backend-pack（SQLite）到本地（零 Supabase 流量）

做什么：从 GitHub Pages 下载 backend-pack.db.gz → 解压到
  backend/data/pack/backend-pack.db

然后本地开发零 Supabase 流量的用法：backend/.env 里加一行 DATA_SOURCE=local
（K线/指标/backtest_prices 读取层自动改读本地 SQLite；业务小表如模拟盘/自选
 走本地 SQLite——DATABASE_URL 留空即可，与生产数据分叉）
更新数据：隔几天重跑本脚本（包每天 16:00 由 GitHub Actions 重新生成）。
"""

import argparse
import gzip
import os
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPTS_DIR, "..", "backend")
PACK_DIR = os.path.join(BACKEND_DIR, "data", "pack")
PACK_DB = os.path.join(PACK_DIR, "backend-pack.db")
PACK_GZ = PACK_DB + ".gz"
DEFAULT_URL = ("https://klen-h.github.io/stock-scoring-v2/data"
               "/backend-pack.db.gz")


def _download(url):
    import requests
    os.makedirs(PACK_DIR, exist_ok=True)
    print(f"下载数据包: {url}")
    r = requests.get(url, timeout=180, stream=True)
    r.raise_for_status()
    gz_tmp = PACK_GZ + ".tmp"
    with open(gz_tmp, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    print("解压中...")
    with gzip.open(gz_tmp, "rb") as f_in:
        data = f_in.read()
    db_tmp = PACK_DB + ".tmp"
    with open(db_tmp, "wb") as f:
        f.write(data)
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
    args = ap.parse_args()

    if (not args.force and os.path.exists(PACK_DB)
            and time.time() - os.path.getmtime(PACK_DB) < 20 * 3600):
        mtime = time.strftime("%m-%d %H:%M",
                              time.localtime(os.path.getmtime(PACK_DB)))
        print(f"本地包较新（{mtime}），跳过下载（--force 可强制）")
    else:
        _download(args.url)
    _hint_env()


if __name__ == "__main__":
    main()
