#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】本地打包发布：把本地生成的数据包直接推到 GitHub Pages（gh-pages 分支）
================================================================================

用途：本地跑完 generate_backend_pack.py / generate-kline-pack.py 后，
  不等 Actions 排队，直接发布——前端"更新数据"立即拿到新包。

机制：git worktree 方式更新 gh-pages 分支（与 peaceiris action 同语义：
  只替换 data/ 下同名文件，其余保留），走本地 git 的 SSH 推送权限，
  不需要 GH_PAGES_TOKEN。

安全设计：
  - 只碰 gh-pages 分支的 data/ 目录，主分支不受影响
  - --dry-run 只列出将发布的文件
  - 远端无 gh-pages 分支时自动 orphan 创建（首次发布）

用法：
  python scripts/publish_packs_local.py            # 发布 data/kline 下全部包
  python scripts/publish_packs_local.py --dry-run  # 只看将发布什么
================================================================================
"""

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.join(SCRIPTS_DIR, "..")
DATA_DIR = os.path.join(REPO_DIR, "backend", "data", "kline")
PACK_FILES = [
    "kline-pack-latest.json.gz",
    "backend-pack.db.gz",
    "indicators-pack.json.gz",
    # ★ 后端包 Actions 从 Pages 拉行情复用（缺了会 404 → 自拉兜底多花 3 分钟）
    "realtime-quotes.json",
]

# ── 发布前护栏（2026-09-08 事故对策）────────────────────────────────────────
# 事故：本地跑 export_browser_packs_from_local.py 做端到端验收（默认只导市值前
#   120 只，实际 118 只），产出落在 backend/data/kline/ 且与正式包同名；随后
#   本地发布把「新生成的 backend-pack.db.gz（1427 只）+ 陈旧的 118 只浏览器包」
#   一起推上 Pages → 前端"更新数据"只导入 118 只，评分池从 1564 缩到 118。
# 护栏：① 条目数低于下限（= 本地小样本包）拒绝发布；② 同批包日期不一致
#   （混入陈旧包）拒绝发布。确有特殊需求用 --force 绕过。
KIND_MAP = {
    "kline-pack-latest.json.gz": "kline",
    "indicators-pack.json.gz": "indicators",
    "backend-pack.db.gz": "backend_db",
    "realtime-quotes.json": "quotes",
}
MIN_COUNTS = {
    "kline-pack-latest.json.gz": 1000,   # 正式包 ~1564 只
    "indicators-pack.json.gz": 500,      # 正式包 ~700+ 只
    "backend-pack.db.gz": 500,
}

# ★ gh-pages 上的历史垃圾文件：发布时顺手删除。
#   backend-pack-20260905.json.gz / backend-pack-latest.json.gz 是被 SQLite 版
#   取代前的旧 JSON 格式后端包，且后者名字极易与真正在用的 backend-pack.db.gz
#   混淆（2026-09-08 对账发现）。
STALE_FILES = [
    "backend-pack-20260905.json.gz",
    "backend-pack-latest.json.gz",
]


def _git(args, cwd=None, check=True):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:200]}")
    return r


def _pack_info(path: str, kind: str):
    """读取包元信息 (date, 条目数)——发布前护栏用。

    只解析最小必要信息：json.gz 解 gz 读两个字段；backend-pack.db.gz 需落临时
    文件才能用 sqlite 查（表很小，count(*) 是 O(1)）。
    """
    if kind in ("kline", "indicators"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            j = json.load(f)
        if kind == "kline":
            return j.get("date"), len(j.get("stocks") or {})
        return j.get("date"), len(j.get("indicators") or {})

    if kind == "backend_db":
        tmp = tempfile.mkdtemp(prefix="packchk-")
        try:
            p = os.path.join(tmp, "b.db")
            with gzip.open(path, "rb") as fin, open(p, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            c = sqlite3.connect(p)
            n = c.execute("SELECT count(*) FROM codes").fetchone()[0]
            row = c.execute("SELECT value FROM meta WHERE key=?",
                            ("pack_date",)).fetchone()
            c.close()
            return (row[0] if row else None), n
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if kind == "quotes":
        with open(path, encoding="utf-8") as f:
            return None, len(json.load(f) or {})

    return None, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DATA_DIR, help="包所在目录")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="跳过包规模/日期一致性护栏（仅在明确知道自己在发什么时用）")
    args = ap.parse_args()

    url = _git(["remote", "get-url", "origin"], cwd=REPO_DIR).stdout.strip()
    print(f"远端: {url}")

    tmp = tempfile.mkdtemp(prefix="ghpages-")
    try:
        # gh-pages 分支存在 → 浅克隆；不存在（首次）→ orphan 创建
        probe = _git(["ls-remote", "--heads", url, "gh-pages"], check=False)
        if probe.stdout.strip():
            _git(["clone", "--depth", "1", "--branch", "gh-pages", url, tmp])
        else:
            print("[publish] 远端无 gh-pages 分支，orphan 创建")
            _git(["clone", "--depth", "1", url, tmp])
            _git(["checkout", "--orphan", "gh-pages"], cwd=tmp)
            _git(["rm", "-rf", "."], cwd=tmp, check=False)

        data_dir = os.path.join(tmp, "data")
        os.makedirs(data_dir, exist_ok=True)

        prepared = []
        for fn in PACK_FILES:
            src = os.path.join(args.dir, fn)
            if not os.path.exists(src):
                print(f"  [跳过] {fn}（本地不存在）")
                continue
            prepared.append((fn, src))
        if not prepared:
            print("没有可发布的包")
            return

        # ── 护栏 1/2：规模下限（本地验收小包）──────────────────────────────
        problems = []
        infos = {}
        for fn, src in prepared:
            kind = KIND_MAP.get(fn)
            date, count = _pack_info(src, kind)
            infos[fn] = (date, count)
            extra = f", date={date}, 条目={count}" if kind else ""
            print(f"  [就绪] {fn} ({os.path.getsize(src) // 1024} KB{extra})")
            floor = MIN_COUNTS.get(fn)
            if floor and count < floor:
                problems.append(
                    f"{fn} 只有 {count} 条（下限 {floor}）——"
                    f"疑似 export_browser_packs_from_local.py 的本地验收小包")

        # ── 护栏 2/2：同批包日期必须一致（混入陈旧包立即暴露）──────────────
        dates = {fn: d for fn, (d, _c) in infos.items() if d}
        if len(set(dates.values())) > 1:
            problems.append(
                "包日期不一致（可能混入了陈旧包）："
                + "、".join(f"{fn}={d}" for fn, d in dates.items()))

        if problems:
            print("\n⚠️  发布前护栏拦截：")
            for p in problems:
                print(f"   - {p}")
            if not args.force:
                print("\n  线上前端/后端都读这些包，误发会直接缩小评分股票池。"
                      "确认无误请加 --force。")
                return
            print("  已指定 --force，继续发布\n")

        published = []
        for fn, src in prepared:
            shutil.copy2(src, os.path.join(data_dir, fn))
            published.append(fn)

        if args.dry_run:
            print("[dry-run] 不推送")
            return

        # 顺手清理历史垃圾文件
        for fn in STALE_FILES:
            p = os.path.join(data_dir, fn)
            if os.path.exists(p):
                os.remove(p)
                print(f"  [清理] gh-pages 上的旧格式文件: {fn}")

        _git(["add", "data"], cwd=tmp)
        # 内容无变化则跳过 commit/push（否则 git commit 因空提交报错）
        if not _git(["status", "--porcelain"], cwd=tmp).stdout.strip():
            print("[publish] 内容无变化，跳过 commit/push")
            return
        _git(["-c", "user.name=github-actions[bot]",
              "-c", "user.email=github-actions[bot]@users.noreply.github.com",
              "commit", "-m", "chore: update data package (local publish)"], cwd=tmp)
        _git(["fetch", "origin", "gh-pages"], cwd=tmp)
        _git(["rebase", "origin/gh-pages"], cwd=tmp, check=False)
        _git(["push", "origin", "gh-pages"], cwd=tmp)
        print(f"✅ 已发布 {len(published)} 个包到 GitHub Pages"
              f"（Pages 部署约 1 分钟后生效）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
