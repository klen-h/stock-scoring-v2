# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import requests

url = ("https://mp-api.jin10.com/api/dynamic-data/child"
       "?tb_name=_vir_16&order=date%2Cdesc&page=1&limit=5")
headers = {
    "x-app-id": "fiXF2nOnDycGutVA",
    "x-version": "1.0",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"),
    "Referer": "https://www.jin10.com/",
    "Origin": "https://www.jin10.com",
    "accept": "application/json, text/plain, */*",
}
try:
    r = requests.get(url, headers=headers, timeout=20)
    print('HTTP:', r.status_code)
    if r.status_code == 200:
        d = r.json()
        rows = d.get('data') or []
        print('条数:', len(rows))
        for it in rows[:3]:
            print(f"  {it.get('date')} weight_total={it.get('weight_total')} "
                  f"weight_change={it.get('weight_change')} "
                  f"value_total={it.get('value_total')} value_change={it.get('value_change')}")
    else:
        print('响应体:', r.text[:300])
except Exception as e:
    print('请求失败:', e)
