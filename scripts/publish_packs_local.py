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

# ★ Windows GBK 控制台打不出 ✅/⚠️ 会崩 print（2026-09-09 实测：push 都完成了
#   却在最后的成功提示上 UnicodeEncodeError）。reconfigure 只降级编码不换语义。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

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

# ★ orphan 重建模式（2026-09-08）：发布不再 clone 旧 gh-pages 树，历史日期包/
#   旧 JSON 垃圾随旧分支历史一起消失——旧 STALE_GLOBS 清理逻辑因此废弃删除。


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
        # ★ orphan 重建模式（2026-09-08）：彻底不再 clone 旧 gh-pages 树。
        #   旧树 ~200MB（45 个历史日期包 + backend 31MB），国内下载 5-15 分钟，
        #   是「发布卡很久」的根源；gh-pages 的 git 历史无保留价值（每天一个
        #   30MB 提交），直接每次重建「单 commit 全新树」force push：
        #   0 下载 + 只上传本次几个文件（~40MB），gh-pages 体积永不再膨胀。
        #   与 Actions 的 peaceiris(keep_files) 不冲突：它 clone 我们的孤儿树 →
        #   只更新 data/ → 保留其余文件；两写者靠 Concurrency 串行锁互斥。
        _git(["init", "-q", tmp])
        _git(["remote", "add", "origin", url], cwd=tmp)
        _git(["checkout", "-q", "--orphan", "gh-pages"], cwd=tmp)
        # GitHub Pages 关闭 Jekyll 处理（与 peaceiris 行为一致）
        open(os.path.join(tmp, ".nojekyll"), "w").close()

        # ★ 前端站点一起带上（2026-09-09 事故修复）：gh-pages 同时是前端站点
        #   托管（deploy-preview 发布 index.html/assets 到根目录），orphan 只放
        #   data/ 会把页面抹掉 → 网页 404。dist 存在则拷入（排除其 data/ 子
        #   目录——那是旧小样本包残留位置，数据一律用本次发布的正式包）。
        #   dist 不存在时提示先 pnpm build（或仅发数据、靠下次 push 补页面）。
        dist_dir = os.path.join(REPO_DIR, "frontend", "dist")
        if os.path.isdir(dist_dir):
            n = 0
            for root, _dirs, files in os.walk(dist_dir):
                rel = os.path.relpath(root, dist_dir)
                # dist/data/ 是 Vite 从 public/data 拷出的残留，绝不入树
                if rel == "data" or rel.startswith("data" + os.sep):
                    continue
                for f in files:
                    src_f = os.path.join(root, f)
                    dst_f = os.path.join(tmp, rel, f) if rel != "." else os.path.join(tmp, f)
                    os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                    shutil.copy2(src_f, dst_f)
                    n += 1
            print(f"  [站点] frontend/dist 并入 {n} 个文件（页面 + 数据一次发齐）")
        else:
            print("  ⚠️ frontend/dist 不存在——本次只发数据包，前端页面保持现状"
                  "（如需页面一起更新：cd frontend && pnpm build 后重跑）")

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

        # （orphan 重建模式：新树只含本次发布的文件，历史日期包/旧 JSON 垃圾
        #   随旧分支历史一起消失，无需再清理）

        _git(["add", "-A"], cwd=tmp)
        _git(["-c", "user.name=github-actions[bot]",
              "-c", "user.email=github-actions[bot]@users.noreply.github.com",
              "commit", "-q", "-m", "chore: update data package (local publish, orphan rebuild)"],
             cwd=tmp)
        _git(["push", "--force", "origin", "gh-pages"], cwd=tmp)
        print(f"✅ 已发布 {len(published)} 个包到 GitHub Pages"
              f"（Pages 部署约 1 分钟后生效）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
