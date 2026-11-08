import sys, io, json
sys.path.insert(0, '.')
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timezone, timedelta
import app.edge_verify as ev

BJ = timezone(timedelta(hours=8))
out = ["# BK-33 后口径复跑报告", "",
       f"- 复跑时间：{datetime.now(BJ).strftime('%Y-%m-%d %H:%M')}",
       "- 背景：BK-33 一字板容差 0.001→0.006 修复后，全量 SCRIPTS 复跑（月度记录不受影响）",
       "- 判据：各脚本预登记判据未改；样本闸照常（INSUFFICIENT 如实输出）", "",
       "| 脚本 | verdict | 摘要 |", "|---|---|---|"]
for sid, cn, *_ in ev.SCRIPTS:
    try:
        r = ev.run_script(sid)
        v = (r.get("verdict") or "INSUFFICIENT")
        note = str(r.get("note") or r.get("detail") or "")[:120].replace("|", "\|")
        out.append(f"| {sid} | {v} | {note} |")
        print(f"[rerun] {sid}: {v}", flush=True)
    except Exception as e:
        out.append(f"| {sid} | ERROR | {str(e)[:100]} |")
        print(f"[rerun] {sid}: ERROR {str(e)[:60]}", flush=True)
out += ["", f"复跑批次完成 {datetime.now(BJ).strftime('%Y-%m-%d %H:%M')}；"
        "⚠️ 月度正式记录以 edge_verify.run_monthly 为准，本报告为 BK-33 对照快照"]
io.open("../docs/reports/20261108_BK33后口径复跑_结果.md", "w", encoding="utf-8", newline="\n").write("\n".join(out))
print("REPORT DONE")
