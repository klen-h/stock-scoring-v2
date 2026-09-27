"""日线近似的「日内弱势」预测力检验（零请求，用已回填的 zzshare 日线）。

判据：收盘位置 = (close-low)/(high-low)。低位收盘 ⇒ 日内弱势（先涨后跌/尾盘走弱）。
检验：低位收盘组 vs 高位收盘组的**次日收益**。
若日线近似也有预测力 ⇒ 不需要分钟数据（避开 zzshare 限流）。
"""
import sqlite3

conn = sqlite3.connect("data/zzshare_daily.db")
sql = """
SELECT close, high, low,
       LEAD(close) OVER (PARTITION BY code ORDER BY date) AS next_close
FROM daily
WHERE date >= '2020-01-01' AND high > low AND close > 0
"""
rows = conn.execute(sql).fetchall()
conn.close()
print(f"读到 {len(rows)} 行")

groups = {"低位收盘 <0.2": [], "低中位 0.2-0.4": [], "中位 0.4-0.6": [],
          "中高位 0.6-0.8": [], "高位收盘 >0.8": []}
for close, high, low, next_close in rows:
    if next_close is None:
        continue
    pos = (close - low) / (high - low)
    ret = (next_close / close - 1) * 100
    if pos < 0.2:
        groups["低位收盘 <0.2"].append(ret)
    elif pos < 0.4:
        groups["低中位 0.2-0.4"].append(ret)
    elif pos < 0.6:
        groups["中位 0.4-0.6"].append(ret)
    elif pos < 0.8:
        groups["中高位 0.6-0.8"].append(ret)
    else:
        groups["高位收盘 >0.8"].append(ret)

print(f"总样本 {len(rows)} 条\n")
for k, v in groups.items():
    if v:
        win = sum(1 for x in v if x > 0) / len(v) * 100
        print(f"{k:16s}: n={len(v):8d}, 次日均 {sum(v)/len(v):+6.3f}%, 胜率 {win:.1f}%")
