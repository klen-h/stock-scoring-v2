"""
================================================================================
【用法】构建「个股 → 行业板块」映射表（评分引擎板块分化因子的数据基础）
================================================================================

    python scripts/build_industry_map.py

做什么：
  遍历东财约 100 个行业板块 → 拉取每个板块的成分股 → 反建「个股 → 行业」映射
  → 写入数据库 stock_industry 表（schema.sql 定义）。

耗时：约 300-500 次请求，带间隔防限流，实测 2-5 分钟。

★ 东财反爬注意：
  连续高频请求会让东财直接断连，甚至临时封 IP（可持续数十分钟到数小时）。
  脚本内部已加请求间隔 + 失败重试，但如果已被封禁，会表现为"所有请求失败、
  降级新浪"。此时脚本会【安全中止】并保留旧数据，等封禁解除后重跑即可，
  不会产生半截的脏数据。

日常无需手动执行：调度器每月 1 号 03:00 自动重建（新股 IPO 会持续新增，
不刷新的话新股就一直没有行业归属）。手动跑这个脚本一般只在两种情况下：
  1. 首次部署，需要初始化映射表
  2. 怀疑映射表有问题，想立刻重建
================================================================================
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.sector_industry import build_map, get_stats   # noqa: E402


def main():
    print("开始构建「个股 → 行业板块」映射表，约需 2-5 分钟...\n")
    r = build_map(verbose=True)

    print("\n" + "=" * 60)
    if r.get("ok"):
        s = get_stats()
        print(f"✅ 构建成功")
        print(f"   覆盖股票：{s.get('stocks')} 只")
        print(f"   行业板块：{s.get('sectors')} 个")
        print(f"   多层归属：{s.get('multi_level')} 只（东财行业是嵌套的，如 氮肥⊂农化制品⊂基础化工）")
        print(f"   构建时间：{s.get('built_at')}")
        print(f"   耗时：{r.get('cost_sec')}s")
        if r.get("failed_sectors"):
            print(f"   ⚠️ 以下板块拉取失败（下月重建时会补上）：{r['failed_sectors'][:10]}")
        top = (s.get("top_industries") or [])[:5]
        if top:
            print("   最大的几个行业：" + "，".join(f"{t['name']}({t['count']})" for t in top))
    else:
        print(f"❌ 构建失败：{r.get('error')}")
        print("\n常见原因：东财临时封 IP（高频请求触发）。")
        print("处理：等数十分钟到数小时后重跑本脚本，或调用")
        print("      POST /api/sector/industry-map/build 触发。")
        sys.exit(1)


if __name__ == "__main__":
    main()
