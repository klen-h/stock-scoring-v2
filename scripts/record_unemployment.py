#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】录入 / 查看美国月度失业率（萨姆规则的数据入口）
================================================================================
为什么需要人工录入（而不是自动抓）：
  · FRED `fredgraph.csv?id=UNRATE` 国内实测**连续 ReadTimeout**（各 20s）；
  · 东财宏观库 `RPT_ECONOMY_US_*` 5 个候选 reportName 全部 `data=0`，不猜。
  · 月度数据一年只有 **12 个点**，人工录入比接一个老超时的外呼更可靠。

用法：
  # ★ 主路径：从金十日历接口刷新（1 请求拿 3 年，已挂日批）
  python scripts/record_unemployment.py --jin10

  # 查看当前序列与判定
  python scripts/record_unemployment.py --show

  # 人工补录某月（接口缺月时用）
  python scripts/record_unemployment.py 2026-11 4.3

  # 一次录多个月（月份在前、值在后，空格分隔）
  python scripts/record_unemployment.py 2026-09 4.2 2026-10 4.3 2026-11 4.4

  # 备源：akshare（数据滞后，通常只到 2025-09）
  python scripts/record_unemployment.py --akshare

  # 备源：FRED（国内常超时，失败不影响已有数据）
  python scripts/record_unemployment.py --try-fred

★ 萨姆规则口径与"数据不足≠未触发"的纪律见 `backend/app/sahm_rule.py` 文件头。
★ 只写 `macro_unemployment` 表，不碰任何决策链。
================================================================================
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

# Windows GBK 控制台下打印中文不炸（reconfigure 是 3.7+ 方法，用 hasattr 判断）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_env():
    from dotenv import load_dotenv
    load_dotenv()


def usage():
    print(__doc__)


def main(argv):
    load_env()
    from app import sahm_rule as sr

    if not argv or argv[0] in ("-h", "--help"):
        usage()
        return 0

    if argv[0] == "--show":
        sr.ensure_table()
        series = sr.load_series(120)
        print(f"已有 {len(series)} 个月数据（需要 ≥ {sr.MIN_POINTS} 个月才能判定）")
        for d, v in series:
            print(f"  {d}  {v:.2f}%")
        v = sr.view()
        print("\n判定：" + v["text"])
        if v.get("detail"):
            print("说明：" + str(v["detail"]))
        return 0

    if argv[0] == "--jin10":
        print("正在从金十日历接口刷新（主源，1 请求）...")
        r = sr.refresh_from_jin10()
        print("结果：", r)
        if r.get("ok"):
            v = sr.view()
            print("判定：" + v["text"])
        return 0 if r.get("ok") else 1

    if argv[0] == "--akshare":
        print("正在从 akshare 回填（数据源滞后，通常只到 2025-09）...")
        r = sr.refresh_from_akshare()
        print("结果：", r)
        return 0 if r.get("ok") else 1

    if argv[0] == "--try-fred":
        print("正在尝试 FRED（国内常超时，最多等 12s）...")
        r = sr.refresh_from_fred()
        print("结果：", r)
        return 0 if r.get("ok") else 1

    # 成对录入
    if len(argv) % 2 != 0:
        print("参数必须成对：<月份> <失业率> ...\n")
        usage()
        return 2

    ok = 0
    for i in range(0, len(argv), 2):
        month, raw_rate = argv[i], argv[i + 1]
        try:
            rate = float(raw_rate)
        except ValueError:
            print(f"  跳过 {month}: 失业率不是数字（{raw_rate!r}）")
            continue
        if not (0 < rate < 30):
            print(f"  跳过 {month}: {rate}% 不像美国失业率，请确认单位（应为百分数，如 4.3）")
            continue
        d = sr.upsert_rate(month, rate)
        print(f"  ✓ {d}  {rate:.2f}%")
        ok += 1

    v = sr.view()
    print("\n判定：" + v["text"])
    if v["status"] == "insufficient":
        print(f"还需 {v['detail']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
