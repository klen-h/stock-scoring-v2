# -*- coding: utf-8 -*-
import io, sys, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 1. 腾讯 45-88 字段
r = requests.get("http://qt.gtimg.cn/q=sh600906,sz002926,sh603605",
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
r.encoding = "gbk"
for line in r.text.strip().split(";"):
    if "=" not in line:
        continue
    var, val = line.split("=", 1)
    code = var.replace("v_", "").strip()
    fields = val.strip('"').split("~")
    print(f"\n{code} 字段45-88:")
    for i in range(45, min(88, len(fields))):
        if fields[i].strip():
            print(f"  [{i}] {fields[i]}")

# 2. 东财 clist 试 f100/f128/f127
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0",
                  "Referer": "https://data.eastmoney.com/"})
print("\n东财 clist 字段测试:")
for f in ("f100", "f128", "f127"):
    r = s.get("http://push2.eastmoney.com/api/qt/clist/get", params={
        "pn": 1, "pz": 3, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f12", "fs": "m:1+t:2,m:0+t:6", "fields": f"f12,f14,{f}",
    }, timeout=10)
    try:
        rows = (r.json().get("data") or {}).get("diff") or []
        print(f"  {f}: " + str([(x.get('f12'), x.get(f)) for x in rows]))
    except Exception as e:
        print(f"  {f}: 异常 {e}")
