# -*- coding: utf-8 -*-
"""全量复跑 `edge_verify.SCRIPTS` 并生成对照报告（原 `backend/_tmp_bk33_rerun.py` 转正）。

用途：改了某个底层口径（如 BK-33 一字板容差、BK-41 判据）之后，
      需要把**所有预登记脚本**重跑一遍，出一份"当前 verdict 快照"。
      **月度正式记录仍以 `edge_verify.run_monthly` 为准**，本脚本只出对照报告。

用法（在项目根目录或任意位置均可）：
    python scripts/rerun_all_scripts.py                    # 出报告到 docs/reports/
    python scripts/rerun_all_scripts.py --no-write         # 只打印不落盘
    python scripts/rerun_all_scripts.py --scripts E1,BK-33 # 只跑指定脚本
    python scripts/rerun_all_scripts.py --out D:/x/y.md    # 指定输出路径

★ 与临时版的三处差别（2026-11-08 审查后转正）：
  1. **路径基于 `__file__`**，不再 `sys.path.insert(0, '.')` 依赖 cwd
     （原版换个目录跑就 import 失败）；
  2. **输出文件名带时间戳**，不再写死 `20261108_...`（否则第二次跑会悄悄覆盖）；
  3. 加 `--no-write` / `--scripts` / `--out`，并**回填退出码**（有 ERROR 时非 0）。
"""
import argparse
import io
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

try:  # Windows 控制台默认 GBK，中文/emoji 会炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BJ = timezone(timedelta(hours=8))


def _resolve_out(explicit: str) -> str:
    if explicit:
        return explicit
    stamp = datetime.now(BJ).strftime("%Y%m%d_%H%M")
    return os.path.join(ROOT, "docs", "reports", f"{stamp}_全量脚本复跑_结果.md")


def main() -> int:
    ap = argparse.ArgumentParser(description="全量复跑 edge_verify.SCRIPTS")
    ap.add_argument("--out", default="", help="报告输出路径（默认 docs/reports/<时间戳>_全量脚本复跑_结果.md）")
    ap.add_argument("--scripts", default="", help="只跑这些脚本 id（逗号分隔，如 E1,BK-33）")
    ap.add_argument("--no-write", action="store_true", help="只打印，不写文件")
    ap.add_argument("--title", default="全量脚本复跑", help="报告标题前缀")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, "backend", ".env"))

    import app.edge_verify as ev  # noqa: E402

    only = {x.strip() for x in args.scripts.split(",") if x.strip()}
    targets = [(sid, cn) for sid, cn, *_ in ev.SCRIPTS if not only or sid in only]
    missing = only - {sid for sid, _ in targets}
    if missing:
        print(f"⚠ 未知脚本 id（已忽略）：{sorted(missing)}")

    started = datetime.now(BJ)
    out = [f"# {args.title}", "",
           f"- 复跑时间：{started.strftime('%Y-%m-%d %H:%M')}",
           "- 判据：各脚本预登记判据未改；样本闸照常（INSUFFICIENT 如实输出）",
           f"- 脚本数：{len(targets)}", "",
           "| 脚本 | verdict | 摘要 |", "|---|---|---|"]

    n_err = 0
    for sid, _cn in targets:
        try:
            r = ev.run_script(sid)
            v = r.get("verdict") or "INSUFFICIENT"
            note = str(r.get("note") or r.get("detail") or "")[:120].replace("|", "\\|")
            out.append(f"| {sid} | {v} | {note} |")
            print(f"[rerun] {sid}: {v}", flush=True)
        except Exception as e:  # 单脚本失败不中断全量
            n_err += 1
            out.append(f"| {sid} | ERROR | {str(e)[:100].replace('|', chr(92) + '|')} |")
            print(f"[rerun] {sid}: ERROR {str(e)[:60]}", flush=True)

    out += ["",
            f"复跑结束 {datetime.now(BJ).strftime('%Y-%m-%d %H:%M')}"
            f"（{n_err} 个 ERROR）；⚠️ 月度正式记录以 `edge_verify.run_monthly` 为准，"
            "本报告仅作口径变更后的对照快照。"]

    text = "\n".join(out)
    if args.no_write:
        print("\n" + text)
    else:
        path = _resolve_out(args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        io.open(path, "w", encoding="utf-8", newline="\n").write(text + "\n")
        print(f"\n报告已写入 {path}")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
