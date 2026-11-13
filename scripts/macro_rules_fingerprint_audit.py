#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】宏观规则口径漂移审计 —— 抓「改了阈值但忘改 RULES_VERSION」
================================================================================
【为什么需要它】（2026-11-14，用户同意补规则指纹）

  `RULES_VERSION` 是**纯人工维护**的字符串（v1→v4），没有任何强制约束。
  有人调了阈值但忘了 bump 版本号，历史快照就会标着**同一个版本、实际口径不同**
  ⇒ **回测被污染，且从数据上完全看不出来**。

  这不是假设。2026-11-14 核查「v4 是否影响打钩期」时，唯一的办法是
  逐日翻 `rules_detail` 比对 `us10y` 实测值 —— 因为**版本号本身不构成证据**。

  ⇒ 现在每天的快照自带 `rules_fingerprint`（`macro.rules_fingerprint()`，
    对 GROUP_WEIGHTS + 每条规则的 id/group/metric/type/strength/bull/bear/band
    算 sha256 前 12 位）。**同版本号却指纹不同 = 口径漂移**。

【局限（必须知道）】
  · 指纹从 **2026-11-14** 起才有，**无法回填**（算指纹需要"当时那份 RULES"，
    代码里只有当前这一份）⇒ 早期数据只能靠 `rules_version` 粗粒度分段。
  · 当前指纹 ≠ 最近的快照指纹 **是正常的**：日批才写快照，部署后当天还没重算。
    真正的漂移判定看「**同一版本号下出现多个指纹**」，不是看"当前 vs 最新"。
  · 数据源**只看 `macro_daily`**（每日锁定的正式快照，也是回测唯一数据源）。
    `macro_signal_log`（盘中等级变化留痕）只存 `rules_version`、**不带指纹**，
    且它是 INSERT 型观察器、不用于回测 ⇒ 本审计不涉及它。
    ⚠️ 别把指纹再加一份到那张表：两处各写一份本身就是新的漂移源。

【⚠️ 副作用提醒】`get_macro_snapshot()` 末尾会调 `log_signal` **写 `macro_signal_log`**，
  不是纯读函数。本脚本只读 `macro_daily`，不会触发它。

【用法】
    python scripts/macro_rules_fingerprint_audit.py
    python scripts/macro_rules_fingerprint_audit.py --json    # 机器可读
================================================================================
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    args = ap.parse_args()

    from app import macro
    from app.database import db

    cur_ver = macro.RULES_VERSION
    cur_fp = macro.rules_fingerprint()

    rows = db.fetch("SELECT date, data_json FROM macro_daily ORDER BY date") or []
    snaps = []
    for r in rows:
        try:
            snap = json.loads(r["data_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        snaps.append((str(r["date"])[:10], snap))

    # ★ 检测逻辑放在 `macro.fingerprint_drift`（那边**可测**）——
    #   本脚本只负责读库与打印。理由：只验证"没漂移时返回空"等于没验证，
    #   必须能用构造数据证明"有漂移时抓得到"。
    rep = macro.fingerprint_drift(snaps)
    by_ver = rep["by_version"]
    no_fp = rep["no_fingerprint"]
    drifts = rep["drifts"]

    if args.json:
        print(json.dumps({
            "current": {"version": cur_ver, "fingerprint": cur_fp},
            "by_version": {v: {fp: len(ds) for fp, ds in fps.items()}
                           for v, fps in by_ver.items()},
            "no_fingerprint_days": len(no_fp),
            "drifts": list(drifts.keys()),
        }, ensure_ascii=False, indent=2))
        return 1 if drifts else 0

    print("=" * 74)
    print("当前规则")
    print("=" * 74)
    print("  rules_version      =", cur_ver)
    print("  rules_fingerprint  =", cur_fp)

    print()
    print("=" * 74)
    print("历史快照的指纹分布（共 %d 天）" % len(rows))
    print("=" * 74)
    if not by_ver:
        print("  没有任何带指纹的快照（机制刚上线，等下一次日批写入）")
    for ver, fps in by_ver.items():
        print("  %s" % ver)
        for fp, ds in fps.items():
            mark = "  ← 当前" if fp == cur_fp else ""
            print("    %s  %2d 天  %s ~ %s%s"
                  % (fp, len(ds), ds[0], ds[-1], mark))
    if no_fp:
        print("  (无指纹)  %2d 天  %s ~ %s   ← 指纹机制上线前，无法回填"
              % (len(no_fp), no_fp[0], no_fp[-1]))

    print()
    print("=" * 74)
    print("漂移检查（同一 rules_version 下出现多个指纹）")
    print("=" * 74)
    if not drifts:
        print("  ✓ 无漂移")
    else:
        for ver, fps in drifts.items():
            print("  ✗ %s 下有 %d 个不同指纹 —— **有人改了规则但没 bump 版本号**：" % (ver, len(fps)))
            for fp, ds in fps.items():
                print("      %s  %2d 天  %s ~ %s" % (fp, len(ds), ds[0], ds[-1]))
        print()
        print("  ⇒ 这些天的分数**不是同一口径**，做回测/统计必须按指纹分段，")
        print("    不能只按 rules_version 分组。")

    print()
    if cur_fp not in {fp for fps in by_ver.values() for fp in fps} and by_ver:
        print("补充：当前指纹尚未出现在任何快照里 ——")
        print("  若刚改过规则，这是**预期**的（日批才写快照）；")
        print("  若没改过，说明部署未生效或快照未重算。")

    return 1 if drifts else 0


if __name__ == "__main__":
    sys.exit(main())
