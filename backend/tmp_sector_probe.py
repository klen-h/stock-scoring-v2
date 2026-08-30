# -*- coding: utf-8 -*-
"""探针：验证 clist 分页能力（pn 翻页）与真实板块规模。"""
import sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
import app  # noqa: F401
from app.eastmoney import get_sectors, _CLIST, _session

def page(fs, fields="f12,f14", pn=1, pz=100):
    """直接分页请求（绕过 _fetch_clist 的单页限制）。"""
    params = {"pn": pn, "pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2,
              "fs": fs, "fields": fields}
    r = _session.get(_CLIST, params=params, timeout=15)
    d = r.json().get("data") or {}
    return d.get("total") or 0, (d.get("diff") or [])

inds = get_sectors("industry", limit=200)
name2code = {s["name"]: s["code"] for s in inds}

# 1) 分页验证：拿"基础化工"的真实总数
for nm in ("基础化工", "氮肥"):
    code = name2code.get(nm)
    if not code:
        continue
    total, rows = page(f"b:{code}", pn=1, pz=100)
    print(f"{nm}({code}): 接口声明 total={total}, 第1页返回 {len(rows)} 条")
    # 翻到第 2/3 页看是否还有数据
    got = list(rows)
    for pn in (2, 3):
        _, r2 = page(f"b:{code}", pn=pn, pz=100)
        print(f"   第{pn}页返回 {len(r2)} 条")
        got += r2
        if len(r2) < 100:
            break
    codes = {x.get("f12") for x in got if x.get("f12")}
    print(f"   累计去重 {len(codes)} 只\n")

# 2) 全市场分页能力（验证能否拉全 5500 只）
FS_ALL = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
total, rows = page(FS_ALL, pn=1, pz=100)
print(f"全市场: 接口声明 total={total}")
allc = {x.get("f12") for x in rows if x.get("f12")}
t0 = time.time()
for pn in range(2, 8):
    _, r2 = page(FS_ALL, pn=pn, pz=100)
    allc |= {x.get("f12") for x in r2 if x.get("f12")}
    if len(r2) < 100:
        break
print(f"   翻 7 页累计去重 {len(allc)} 只, 耗时 {time.time()-t0:.1f}s")
print(f"   → 拉全 {total} 只需约 {total//100 + 1} 次请求")
