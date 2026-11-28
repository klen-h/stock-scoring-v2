# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】个股展示规范门禁 —— 2026-11-28
================================================================================
背景（用户）："股票名称出现一定要带上股票代码，股票名称跳转到详情，股票代码跳雪球。
  是不是写一个 skills 能完全约束到 LLM，因为这个出现好几次遗漏了。"

结论：skill/rule 只能提高遵循率，**不能保证不遗漏** ⇒ 必须有一道机器门禁。

规则源：` .codebuddy/rules/stock-display/RULE.mdc`（alwaysApply）
  · 名称必须带 6 位代码
  · 名称 → `#/stock/{code}`（用 `stockHref()` 或 `<StockName>`）
  · 代码 → `xueqiu.com/S/{SH|SZ|BJ}{code}`（用 `xqUrl()`）
  · 指数 → `xqIndexUrl(prefix, code)`，**不能复用 xqUrl()**（000001 撞码）

本脚本扫描**可静态判定的部分**（URL 硬编码、旧 helper、手写前缀）：
  "名称没带代码"属于动态渲染，静态扫不出来 ⇒ 靠 RULE + Skill + `StockName.vue` 组件收敛。

白名单（合法例外）：
  · `composables/displayMeta.js`：helper 本身在这里拼 URL
  · `composables/stockUtils.js`：兼容 re-export（新代码不该 import，但存量保留）

用法：
  python scripts/check_stock_display.py
================================================================================
"""
import os
import re
import sys
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "src")

# (名字, 正则, 允许出现的文件相对路径集合)
PATTERNS: List[Tuple[str, re.Pattern, set]] = [
    (
        "hardcoded_xueqiu",
        re.compile(r"https?://(?:www\.)?xueqiu\.com", re.I),
        {"composables/displayMeta.js"},
    ),
    (
        "hardcoded_detail_href",
        re.compile(r"href\s*=\s*['\"](?:#/|/)?stock/"),
        set(),
    ),
    (
        "legacy_helper",
        re.compile(r"\bgetXueqiuUrl\b"),
        {"composables/stockUtils.js"},   # 兼容 re-export 的定义处
    ),
    (
        "hand_written_prefix",
        re.compile(r"startsWith\(['\"](?:6|9)['\"]\)\s*\?\s*['\"]SH['\"]"),
        {"composables/displayMeta.js", "composables/stockUtils.js"},
    ),
]

SKIP_DIRS = {"node_modules", "dist", ".git"}


def _files() -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith((".vue", ".js", ".ts")):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def main() -> int:
    findings: Dict[str, List[str]] = {name: [] for name, _, _ in PATTERNS}
    for path in _files():
        rel = os.path.relpath(path, SRC).replace("\\", "/")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for name, rx, allow in PATTERNS:
            if rel in allow:
                continue
            for i, line in enumerate(lines, 1):
                if rx.search(line):
                    findings[name].append(f"{rel}:{i}: {line.strip()[:120]}")

    total = 0
    print("=== 个股展示规范门禁 ===")
    for name, _, _ in PATTERNS:
        hits = findings[name]
        total += len(hits)
        print(f"{'OK  ' if not hits else 'FAIL'} {name:22s} now={len(hits)}")
        for h in hits[:10]:
            print("       " + h)
        if len(hits) > 10:
            print(f"       ... 另有 {len(hits) - 10} 处")

    if total:
        print(f"\n检查不通过：{total} 处硬编码/旧写法。"
              "请改用 `StockName.vue` 或 `displayMeta.js` 的 stockHref/xqUrl/xqIndexUrl。")
        return 1
    print("\n检查通过：无硬编码 URL / 旧 helper / 手写前缀。")
    print("提示：本脚本只扫可静态判定的部分；'名称带代码'由 RULE + Skill + StockName 组件保证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
