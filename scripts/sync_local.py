#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】本地开发数据同步：把后端数据包下载到本地（零 Supabase 流量开发）
================================================================================

做什么：
  从 GitHub Pages 下载 backend-pack（K线+指标，全量）到
  backend/data/pack/backend-pack-latest.json.gz

然后本地开发零 Supabase 流量的用法：
  backend/.env 里加一行：DATA_SOURCE=local
  （读侧 get_cached_klines / 指标 / backtest_prices 会自动改读本地包；
    业务小表如模拟盘/自选走本地 SQLite——DATABASE_URL 留空即可，与生产数据分叉）

更新数据：隔几天重跑本脚本即可（包每天 16:00 由 GitHub Actions 重新生成）。
================================================================================
"""

import argparse
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPTS_DIR, "..", "backend")
PACK_DIR = os.path.join(BACKEND_DIR, "data", "pack")
PACK_FILE = os.path.join(PACK_DIR, "backend-pack-latest.json.gz")
DEFAULT_URL = ("https://klen-h.github.io/stock-scoring-v2/data"
               "/backend-pack-latest.json.gz")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("PACK_URL", DEFAULT_URL))
    ap.add_argument("--force", action="store_true", help="本地已是当天包也强制重下")
    args = ap.parse_args()

    import time
    if (not args.force and os.path.exists(PACK_FILE)
            and time.time() - os.path.getmtime(PACK_FILE) < 20 * 3600):
        print(f"本地包较新（{time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(PACK_FILE)))}），"
              f"跳过下载（--force 可强制）")
    else:
        import requests
        os.makedirs(PACK_DIR, exist_ok=True)
        print(f"下载数据包: {args.url}")
        r = requests.get(args.url, timeout=120, stream=True)
        r.raise_for_status()
        tmp = PACK_FILE + ".tmp"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        os.replace(tmp, PACK_FILE)
        print(f"已保存: {PACK_FILE} ({os.path.getsize(PACK_FILE) / 1048576:.1f} MB)")

    env_path = os.path.join(BACKEND_DIR, ".env")
    has_flag = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            has_flag = any(line.strip().startswith("DATA_SOURCE=")
                           for line in f if not line.strip().startswith("#"))
    print("\n下一步：")
    if not has_flag:
        print(f"  在 {env_path} 加一行（本地开发零 Supabase 流量）:")
        print("    DATA_SOURCE=local")
    else:
        print("  backend/.env 已有 DATA_SOURCE 配置，无需改动")
    print("  之后正常 python run.py 即可，读侧自动走本地数据包")


if __name__ == "__main__":
    main()
