# -*- coding: utf-8 -*-
"""今天（10-19）三个 workflow 的运行情况。"""
import json
import urllib.request
from datetime import datetime, timedelta, timezone


def api(path):
    req = urllib.request.Request(
        "https://api.github.com/repos/klen-h/stock-scoring-v2" + path,
        headers={"User-Agent": "curl/8", "Accept": "application/vnd.github+json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore"))


def bj(s):
    if not s:
        return "—"
    dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (dt + timedelta(hours=8)).strftime("%m-%d %H:%M")


for wf in ("kline-data.yml", "backend-pack.yml", "daily-batch.yml"):
    print(f"\n=== {wf} ===")
    try:
        for r in api(f"/actions/workflows/{wf}/runs?per_page=3").get("workflow_runs") or []:
            print(f"  created={bj(r['created_at'])} start={bj(r.get('run_started_at'))} "
                  f"end={bj(r['updated_at'])} {r['status']}/{r['conclusion']} ev={r['event']} "
                  f"id={r['id']}")
    except Exception as e:
        print("  查询失败:", type(e).__name__, str(e)[:60])
