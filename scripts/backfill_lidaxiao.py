# -*- coding: utf-8 -*-
"""一次性回填：本地 xiaopage videos.json → lidaxiao_views 表（幂等，可重跑）。

背景（.codebuddy/plans/李大霄视角卡片_xiaopage解读接入__3f8a2c61.md §七-7）：
  线上走事件推送（POST /api/market/lidaxiao/push）只管增量；484 条历史由本脚本
  本地灌库（参照 backfill_eq_anchor 先例），Actions/Render 零负担。
  回填与推送共用 lidaxiao_views.ingest_entries()，口径天然一致。

用法：
  python scripts/backfill_lidaxiao.py                       # 默认读 D:\\stock-scoring\\xiaopage
  python scripts/backfill_lidaxiao.py --file <videos.json 路径>

⚠️ 本地跑与线上 Render 共用同一个数据库与 egress 额度（EGRESS.md 核心原则一）；
  本脚本一次写 ~484 行 ≈ 1MB，一次性成本，可忽略。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

DEFAULT_FILE = r"D:\stock-scoring\xiaopage\public\data\videos.json"


def load_env():
    """加载 backend/.env（本地跑用；须与生产同库才有意义）。"""
    path = os.path.join(BACKEND, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    ap = argparse.ArgumentParser(description="回填李大霄解读历史到 lidaxiao_views")
    ap.add_argument("--file", default=DEFAULT_FILE, help="videos.json 路径")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"文件不存在: {args.file}")
        sys.exit(1)
    load_env()

    from app import lidaxiao_views as lv
    data = json.load(open(args.file, encoding="utf-8"))
    if not isinstance(data, list):
        print(f"不是 JSON 数组: {args.file}")
        sys.exit(1)
    r = lv.ingest_entries(data)
    latest = r.get("latest") or {}
    print(f"backfill done: total={len(data)} stored={r['stored']} skipped={r['skipped']}")
    for x in (r.get("reasons") or [])[:5]:
        print(f"  skip: {x}")
    print(f"latest in table: {latest.get('date')} | {latest.get('title')}")
    d = lv.load()
    print(f"GET 口径自检: latest.date={d.get('latest', {}).get('date')}"
          f" lag_days={d.get('lag_days')} trend={len(d.get('trend') or [])}")


if __name__ == "__main__":
    main()
