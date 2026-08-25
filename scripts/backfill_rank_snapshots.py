#!/usr/bin/env python3
"""
================================================================================
【文件作用】将前端 localStorage 的胜率回查快照迁移回填到数据库 ranking_history
================================================================================

背景：
  改造前"胜率回查"面板的快照存在浏览器 localStorage（key: score_snapshots），
  格式为 {date: {ts, stocks: [...], verified, ...}}，含 price/dimensions。
  改造后快照统一入库（ranking_history），但历史本地数据仍在浏览器里。
  本脚本把这些本地快照一次性迁移到数据库，收益由后端按当前现价实时计算。

用法：
  python scripts/backfill_rank_snapshots.py <snapshots.json> [--dry-run]

输入 JSON 格式（与前端 ScoreRank.vue 保存到 localStorage 的结构一致）：
{
  "2026-08-20": {
    "ts": 1787242291273,
    "stocks": [
      {"code": "001367", "name": "海森药业", "score": 70.5, "signal": "买入",
       "price": 19.97, "dimensions": {"技术面": 69.8, "资金面": 69.4, "基本面": 72},
       "currentPrice": 20.61, "returnPct": 3.2},
      ...
    ],
    "verified": true, "winRate": 46, "avgReturn": -1.23
  },
  ...
}

转换映射：
  date        -> rank_date
  code/name   -> code/name
  score       -> total_score
  signal      -> signal
  数组下标+1  -> rank_pos
  dimensions  -> dimensions_json（JSON 字符串；缺失则 NULL）
  price       -> price（缺失则 0）
  其余字段（currentPrice/returnPct/ts/verified/winRate/avgReturn）忽略：
    收益由后端 get_daily_rankings 按当前现价实时计算，无需落库。

写入策略：
  每个日期先 DELETE 当天已有记录，再按快照顺序 INSERT —— 保证该日期
  与本地快照完全一致（等价于每日权威快照的 replace_day 语义）。
================================================================================
"""

import argparse
import json
import os
import sys

# ── 路径配置 ──
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env():
    """加载 backend/.env 到环境变量（不覆盖已存在的值）"""
    if not os.path.exists(ENV_PATH):
        print(f"[warn] 未找到 {ENV_PATH}，将使用系统环境变量（SQLite 默认）")
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip())


def convert_snapshot_to_rows(date: str, stocks: list) -> list:
    """
    将某日快照的 stocks 列表转成 ranking_history 行格式。
    顺序即排名：数组下标 + 1 = rank_pos。
    """
    rows = []
    for i, s in enumerate(stocks):
        code = str(s.get("code", "")).strip()
        if not code:
            continue
        dims = s.get("dimensions") or {}
        rows.append({
            "rank_date": date,
            "code": code,
            "name": s.get("name"),
            "rank_pos": i + 1,
            "total_score": s.get("score"),
            "signal": s.get("signal"),
            "dimensions_json": json.dumps(dims, ensure_ascii=False) if dims else None,
            "price": s.get("price") or 0,
        })
    return rows


def upload_rows(rows: list, dry_run: bool = False) -> int:
    """写入数据库：先清空该日期已有记录，再按顺序插入。返回写入条数。"""
    if dry_run or not rows:
        return 0

    sys.path.insert(0, BACKEND_DIR)
    from app.database import db

    date = rows[0]["rank_date"]
    db.execute("DELETE FROM ranking_history WHERE rank_date = %s", (date,))
    count = 0
    for row in rows:
        db.execute("""
            INSERT INTO ranking_history
            (rank_date, code, name, rank_pos, total_score, signal, dimensions_json, price)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            row["rank_date"], row["code"], row["name"], row["rank_pos"],
            row["total_score"], row["signal"], row["dimensions_json"], row["price"],
        ))
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="迁移 localStorage 快照到 ranking_history")
    parser.add_argument("json_file", help="localStorage 导出的 score_snapshots JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="只解析转换并打印统计，不写数据库")
    args = parser.parse_args()

    if not os.path.exists(args.json_file):
        print(f"[error] 文件不存在: {args.json_file}")
        sys.exit(1)

    load_env()

    with open(args.json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print("[error] JSON 根节点必须是 {date: {stocks: [...]}} 结构")
        sys.exit(1)

    total = 0
    for date in sorted(data.keys()):
        snap = data[date]
        stocks = (snap or {}).get("stocks") or []
        if not stocks:
            print(f"[skip] {date}: 无 stocks 数据")
            continue

        rows = convert_snapshot_to_rows(date, stocks)
        if not rows:
            print(f"[skip] {date}: 转换后无有效行")
            continue

        if args.dry_run:
            sample = rows[0]
            print(f"[dry-run] {date}: {len(rows)} 条 | 首条: "
                  f"code={sample['code']} rank={sample['rank_pos']} "
                  f"score={sample['total_score']} price={sample['price']} "
                  f"dims={'有' if sample['dimensions_json'] else '无'}")
            total += len(rows)
            continue

        count = upload_rows(rows)
        total += count
        print(f"[ok] {date}: 回填 {count} 条")

    if args.dry_run:
        print(f"\ndry-run 结束，共 {total} 条待写入。确认无误后去掉 --dry-run 执行。")
    else:
        print(f"\n完成，共回填 {total} 条。")


if __name__ == "__main__":
    main()
