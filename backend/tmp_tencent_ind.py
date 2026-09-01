# -*- coding: utf-8 -*-
import io, sys, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

codes = ["sh600906", "sz002926", "sh603605", "bj920992", "sh688981", "sz300750"]
url = "http://qt.gtimg.cn/q=" + ",".join(codes)
r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
r.encoding = "gbk"
for line in r.text.strip().split(";"):
    if "=" not in line:
        continue
    var, val = line.split("=", 1)
    code = var.replace("v_", "").strip()
    fields = val.strip('"').split("~")
    # 打印前 45 个字段
    print(f"\n{code} 共{len(fields)}字段:")
    for i, f in enumerate(fields[:45]):
        print(f"  [{i}] {f}")
