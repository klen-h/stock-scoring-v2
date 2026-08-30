# -*- coding: utf-8 -*-
"""临时验证：新浪是否提供美债 2Y/30Y，以及字段布局是否与 bd 族一致"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import requests

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0",
                  "Referer": "https://finance.sina.com.cn"})

codes = [
    "globalbd_us10yt",   # 已知可用（现网在用）
    "globalbd_us2yt",    # 待测
    "globalbd_us30yt",   # 待测
    "globalbd_us5yt",    # 待测（备用）
    "globalbd_cn10yt",   # 已知可用
]
r = s.get("https://hq.sinajs.cn/list=" + ",".join(codes), timeout=10)
text = r.content.decode("gbk", errors="replace")
for line in text.strip().split("\n"):
    line = line.strip().rstrip(";")
    if '="' not in line:
        continue
    key = line.split("=")[0].replace("var hq_str_", "")
    val = line.split('"')[1] if '"' in line else ""
    fields = val.split(",") if val else []
    print(f"\n=== {key} === 字段数={len(fields)}")
    print("  原始:", val[:200])
    if len(fields) >= 6:
        # bd 族布局：[3]最新 [2]昨收 [4]高 [5]低 [12]日期 [13]时间
        print(f"  [2]昨收={fields[2]}  [3]最新={fields[3]}  [4]高={fields[4]}  [5]低={fields[5]}")
