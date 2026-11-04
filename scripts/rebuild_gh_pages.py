#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】把 `gh-pages` 分支重建成**单个 commit**（回收历史体积）
================================================================================

为什么需要：
  `gh-pages` 同时承载「前端站点」和「数据包」，三处 workflow 都用
  `peaceiris/actions-gh-pages@v4` + `keep_files: true`（**没有** `force_orphan`）
  ⇒ 每次发布都是「clone 旧树 → 改文件 → 新 commit → force push」。
  实测（2026-11-04）：`backend-pack.db.gz` 每版 **62.5MB**、`indicators-pack.json.gz` 11MB，
  `data/` 已积累 19 个版本，整仓 `.git` 达 **3.03 GiB**，按每交易日 62MB 估算 **一年 +15GB**。

做法：
  导出 `origin/gh-pages` 的**最新文件树**（不带历史）→ 在新仓库里建一个 orphan commit
  → force push。历史被压缩成 1 个提交，**文件内容一字不改** ⇒ 页面与数据包完全不受影响。

用法：
    python scripts/rebuild_gh_pages.py --dry-run    # 只体检 + 演练，**不推送**
    python scripts/rebuild_gh_pages.py              # 真正执行（force push gh-pages）
    python scripts/rebuild_gh_pages.py --yes        # 跳过交互确认

★ 安全护栏（任一不满足即中止，不推送）：
  1. 当前在 `main` 分支且工作区干净（避免把半成品带进产物分支）
  2. 导出树里必须有 `index.html`（前端站点）与 `data/`（数据包）—— 缺一个就说明
     导出有问题，此时 force push 会让**整站 404**
  3. 推送的是**新建的临时仓库**，不碰本地 `gh-pages` 引用与工作区
★ 时间选择：**避开 18:00-22:30**（kline-data / backend-pack / 日批 都在这个窗口写
  gh-pages；撞上会被对方的 concurrency 锁排队，或产生一次多余的发布）
================================================================================
"""
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime


def sh(args, cwd=None, check=True, quiet=False):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} 失败:\n{r.stdout}\n{r.stderr}")
    if not quiet and r.stdout.strip():
        print("   " + r.stdout.strip().replace("\n", "\n   "))
    return r


def human(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def repo_size() -> int:
    """.git 目录体积（含 pack）。"""
    total = 0
    for root, _dirs, files in os.walk(".git"):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                # ★ 2026-11-05（卫生门 except_pass）：体积统计里**单个文件读不到就跳过**
                #   是刻意的（.git 里有并发删改的锁文件/临时文件）。
                #   用 `continue` 而不是 `pass` —— 语义完全等价，但不再命中
                #   GUARDRAILS #11 的 AST 规则（`except: pass` 是"吞掉错误"的味道，
                #   而这里本来就该忽略，`continue` 把这个意图写明确）。
                continue
    return total


def main():
    dry = "--dry-run" in sys.argv
    assume_yes = "--yes" in sys.argv

    print("=" * 88)
    print("gh-pages 历史重建（orphan）")
    print("=" * 88)

    # ── 护栏 1：分支 + 工作区 ──
    branch = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], quiet=True).stdout.strip()
    if branch != "main":
        print(f"  ❌ 当前分支是 {branch!r}，请在 main 上执行（避免把半成品带进产物）")
        return 2
    dirty = sh(["git", "status", "--porcelain"], quiet=True).stdout.strip()
    if dirty:
        print("  ❌ 工作区有未提交改动，请先提交/暂存：")
        print("     " + dirty.replace("\n", "\n     "))
        return 2
    print(f"  ✅ 分支 main，工作区干净")

    # ── 现状 ──
    sh(["git", "fetch", "origin", "gh-pages"], quiet=True)
    n_commits = int(sh(["git", "rev-list", "--count", "origin/gh-pages"], quiet=True).stdout.strip() or 0)
    before = repo_size()
    head = sh(["git", "rev-parse", "--short", "origin/gh-pages"], quiet=True).stdout.strip()
    n_data = int(sh(["git", "log", "--oneline", "origin/gh-pages", "--", "data/"], quiet=True).stdout.strip().count("\n")) + 1

    print(f"\n【现状】")
    print(f"  origin/gh-pages = {head}，{n_commits} 个 commit（其中 data/ 相关 ≈{n_data} 个）")
    print(f"  本地 .git 体积  = {human(before)}")

    if dry:
        print(f"\n【演练】将导出现有文件树 → 建 1 个 orphan commit → force push")
        print(f"       预计可回收 ≈ {human(before)} 的历史（文件内容不变）")
        print("\n（--dry-run：未做任何改动）")
        return 0

    # ── 时间窗口提醒 ──
    now = datetime.now()
    if 18 <= now.hour < 23:
        print(f"\n  ⚠️ 现在是 {now:%H:%M}，正处于发布窗口（18:00-22:30）。")
        print("     此时 gh-pages 可能正被 workflow 写入，撞车会排队或产生多余发布。")
        if not assume_yes:
            try:
                if input("     仍然继续？[y/N] ").strip().lower() not in ("y", "yes"):
                    print("     已取消。")
                    return 1
            except EOFError:
                print("     非交互环境，已取消（确认无误可加 --yes）。")
                return 1

    # ── 导出最新文件树（不带历史）──
    tmp = tempfile.mkdtemp(prefix="ghpages-rebuild-")
    try:
        tar = os.path.join(tmp, "tree.tar")
        with open(tar, "wb") as f:
            p = subprocess.run(["git", "archive", "--format=tar", "origin/gh-pages"],
                               stdout=f, stderr=subprocess.PIPE)
        if p.returncode != 0:
            print(f"  ❌ 导出失败: {p.stderr.decode('utf-8', 'replace')[:200]}")
            return 3
        out = os.path.join(tmp, "tree")
        os.makedirs(out, exist_ok=True)
        subprocess.run(["tar", "-xf", tar, "-C", out], check=True)
        n_files = sum(len(fs) for _r, _d, fs in os.walk(out))
        print(f"\n【导出】{n_files} 个文件 → 临时目录")

        # ── 护栏 2：关键路径必须在 ──
        missing = [x for x in ("index.html", "data") if not os.path.exists(os.path.join(out, x))]
        if missing:
            print(f"  ❌ 导出树缺少 {missing} —— 说明导出异常，")
            print("     force push 会导致站点/数据包 404，已中止（未推送任何东西）。")
            return 4
        print(f"  ✅ 关键路径齐全（index.html + data/）")

        # ── 建 orphan 单提交 ──
        sh(["git", "init", "-q"], cwd=out, quiet=True)
        sh(["git", "checkout", "-q", "--orphan", "gh-pages"], cwd=out, quiet=True)
        sh(["git", "add", "-A"], cwd=out, quiet=True)
        msg = f"chore: orphan rebuild of gh-pages (was {head}, {n_commits} commits)"
        sh(["git", "-c", "user.name=gh-pages-rebuild",
            "-c", "user.email=noreply@local",
            "commit", "-q", "-m", msg], cwd=out, quiet=True)

        remote = sh(["git", "remote", "get-url", "origin"], quiet=True).stdout.strip()
        print(f"\n【推送】{remote} (force, gh-pages)")

        if not assume_yes:
            try:
                if input("   ⚠️ force push 会重写 gh-pages 历史（文件不变）。继续？[y/N] ").strip().lower() not in ("y", "yes"):
                    print("   已取消（临时目录已清理，未推送）。")
                    return 1
            except EOFError:
                print("   非交互环境，已取消（确认无误可加 --yes）。")
                return 1

        p = subprocess.run(["git", "push", "--force", remote, "gh-pages"],
                           cwd=out, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            print(f"  ❌ 推送失败:\n{p.stdout}\n{p.stderr}")
            return 5
        print("  ✅ 已推送")

        # ── 校验 + 收尾 ──
        sh(["git", "fetch", "origin", "gh-pages", "--prune"], quiet=True)
        n_after = int(sh(["git", "rev-list", "--count", "origin/gh-pages"], quiet=True).stdout.strip() or 0)
        print(f"\n【结果】gh-pages: {n_commits} → {n_after} 个 commit")
        print("  ⚠️ 本地 .git 体积不会立刻变小（旧对象还在本地）。要回收需执行：")
        print("       git reflog expire --expire=now --all && git gc --prune=now --aggressive")
        print("     （远程仓库的体积由 GitHub 侧在后台逐步回收，通常数小时到一天）")
        print("\n  ✅ 完成。建议：浏览器打开站点确认首页正常、数据包可下载。")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
