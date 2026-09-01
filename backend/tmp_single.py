# -*- coding: utf-8 -*-
import io, sys, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0",
                  "Referer": "https://data.eastmoney.com/"})
# 002961 瑞达期货（深）、600906 财达证券（沪）、920992 中科美菱（北交所）
for secid in ("0.002961", "1.600906", "0.920992", "0.001337"):
    for f in ("f100", "f127"):
        try:
            r = s.get("http://push2delay.eastmoney.com/api/qt/stock/get",
                      params={"secid": secid, "fields": f"f57,f58,{f}"}, timeout=8)
            d = r.json().get("data") or {}
            print(f"secid={secid} {f}: name={d.get('f58')} ind={d.get(f)}")
        except Exception as e:
            print(f"secid={secid} {f}: 异常 {e}")
