# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】生成「批量 LLM 任务」的文件清单（manifest.json）
================================================================================
为什么需要：5 亿 token 的批量任务不能把整个仓库一次性塞给模型（上下文窗口不够、
也会重复计费）。执行方需要一份**可分批、可统计**的清单：

  reports/llm_batch/manifest.json
    ├─ generated_at / root / totals
    └─ entries: [{path, kind, loc, chars, bytes, bucket, priority, est_tokens}]
         · kind      = backend_app | backend_other | scripts | frontend | docs
         · bucket     = 建议批次（如 backend/app/coach → "backend:coach"）
         · priority   = P0 核心链路 / P1 观察器与工具 / P2 其余（用于先跑重要的）
         · est_tokens = 粗估（中文按 1 字≈1 token、英文按 4 字符≈1 token 的保守值）

用法：
    python scripts/build_llm_manifest.py                # 写 reports/llm_batch/manifest.json
    python scripts/build_llm_manifest.py --max-loc 800  # 超过该行数的文件标 chunked=True
    python scripts/build_llm_manifest.py --json-only    # 只打印摘要不写文件

★ 本脚本**不调用任何 LLM**、不改业务代码，只读文件系统。
================================================================================
"""
import argparse
import json
import os
import time
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "reports", "llm_batch")

SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".history",
             "vendor_reference", "data", ".venv", "venv", "build", ".pytest_cache"}
INCLUDE_EXT = {".py", ".vue", ".js", ".ts", ".md", ".yml", ".yaml", ".sql", ".sh"}

# 优先级：核心链路先跑（P0），观察器/工具次之（P1）
P0_PREFIX = (
    "backend/app/scoring/", "backend/app/mainforce/", "backend/app/backtest/",
    "backend/app/routers/", "backend/app/database.py", "backend/app/tencent.py",
    "backend/app/market.py", "backend/app/flash/llm.py", "backend/app/flash/store.py",
    "scripts/daily_batch.py", "frontend/src/api/", "frontend/src/views/Workbench.vue",
)
P1_PREFIX = (
    "backend/app/flash/", "backend/app/coach/", "backend/app/routers/stock.py",
    "backend/app/flash_brief.py", "backend/app/observer_review.py",
    "backend/app/policy_bottom_watch.py", "backend/app/scenario_watch.py",
    "frontend/src/components/", "scripts/",
)


def _kind(rel: str) -> str:
    if rel.startswith("backend/app/"):
        return "backend_app"
    if rel.startswith("backend/"):
        return "backend_other"
    if rel.startswith("scripts/"):
        return "scripts"
    if rel.startswith("frontend/src/"):
        return "frontend"
    if rel.endswith(".md"):
        return "docs"
    return "other"


def _bucket(rel: str) -> str:
    parts = rel.split("/")
    if rel.startswith("backend/app/"):
        return "backend:" + (parts[2] if len(parts) > 2 else "app")
    if rel.startswith("frontend/src/"):
        return "frontend:" + (parts[2] if len(parts) > 2 else "src")
    return parts[0]


def _priority(rel: str) -> str:
    if any(rel.startswith(p) for p in P0_PREFIX):
        return "P0"
    if any(rel.startswith(p) for p in P1_PREFIX):
        return "P1"
    return "P2"


def _est_tokens(text: str) -> int:
    """保守估算：CJK 记 1 token/字，其它按 4 字符/token。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = max(len(text) - cjk, 0)
    return int(cjk + other / 4)


def scan(max_loc: int) -> Dict:
    entries: List[Dict] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in INCLUDE_EXT:
                continue
            abspath = os.path.join(dirpath, fn)
            rel = os.path.relpath(abspath, ROOT).replace("\\", "/")
            if rel.startswith(("reports/", "docs/")):
                pass  # docs 也要进清单（文档生成任务要用）
            try:
                with open(abspath, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            loc = text.count("\n") + 1
            entries.append({
                "path": rel,
                "kind": _kind(rel),
                "bucket": _bucket(rel),
                "priority": _priority(rel),
                "loc": loc,
                "chars": len(text),
                "bytes": os.path.getsize(abspath),
                "est_tokens": _est_tokens(text),
                "chunked": loc > max_loc,
            })

    entries.sort(key=lambda x: (x["kind"], x["bucket"], x["path"]))
    by_kind: Dict[str, Dict] = {}
    for e in entries:
        k = by_kind.setdefault(e["kind"], {"files": 0, "loc": 0, "est_tokens": 0})
        k["files"] += 1
        k["loc"] += e["loc"]
        k["est_tokens"] += e["est_tokens"]
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "root": ROOT.replace("\\", "/"),
        "totals": {
            "files": len(entries),
            "loc": sum(e["loc"] for e in entries),
            "chars": sum(e["chars"] for e in entries),
            "est_tokens": sum(e["est_tokens"] for e in entries),
            "chunked_files": sum(1 for e in entries if e["chunked"]),
        },
        "by_kind": by_kind,
        "buckets": sorted({e["bucket"] for e in entries}),
        "entries": entries,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-loc", type=int, default=800,
                    help="超过该行数的文件标记 chunked=True（需要切片喂给模型）")
    ap.add_argument("--json-only", action="store_true", help="只打印摘要，不写文件")
    args = ap.parse_args()

    data = scan(args.max_loc)
    t = data["totals"]
    print(f"files={t['files']} loc={t['loc']} est_tokens={t['est_tokens']} "
          f"chunked={t['chunked_files']}")
    for k, v in sorted(data["by_kind"].items()):
        print(f"  {k:14s} files={v['files']:4d} loc={v['loc']:7d} "
              f"est_tokens={v['est_tokens']}")
    if args.json_only:
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"written: {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
