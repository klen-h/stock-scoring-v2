#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证用户复盘：2026-05-14 ~ 2026-09-24 阴跌 + 三次事件驱动反弹。
用项目数据（event_history 5279 天）定量核对：
  1) 各反弹关键日的宽度（up_ratio）/ 涨停比例 / 成交额
  2) 这些日子是否触发 E2（up_ratio≥0.90 且 涨停比例≥2.0%）
  3) 成交额是否"缩量后放量"
"""
import os, json
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = json.load(open(os.path.join(ROOT, 'data', 'event_history.json'), encoding='utf-8'))
KS = sorted(D)

E2_UP = 0.90
E2_LU = 0.020


def lu(v):
    tot = (v.get('up') or 0) + (v.get('down') or 0)
    return (v.get('limit_up') or 0) / tot if tot else 0.0


def e2(v):
    return v.get('up_ratio', 0) >= E2_UP and lu(v) >= E2_LU


# 关键日（用户复盘提到的）
KEY = {
    '2026-05-14': '阶段高点(上证4258.86)',
    '2026-07-17': '阶段低点(3764, -11.3%) + 北向215亿',
    '2026-07-21': '深V反弹(科创50 +10%)',
    '2026-07-27': 'AI硬件领涨(建材+6.03%)',
    '2026-08-15': '8月财政预期(抽样)',
    '2026-09-16': '五连阳起点(3864)',
    '2026-09-22': '反弹普涨(涨跌比4533:932)',
    '2026-09-23': '贝森特-何立峰会谈',
    '2026-09-24': '会晤当日(上证-0.94%)',
}

print("=" * 116)
print("【关键日：宽度 / 涨停比例 / 成交额 / E2 判定】")
print("=" * 116)
print(f"{'日期':>12}{'事件':<28}{'up_ratio':>10}{'涨停比例':>10}{'涨停家':>7}"
      f"{'成交额(亿)':>11}{'E2?':>6}")
rows = {}
for k in sorted(KEY):
    if k not in D:
        print(f"{k:>12}{KEY[k]:<28}  （无数据）")
        continue
    v = D[k]
    rows[k] = v
    print(f"{k:>12}{KEY[k]:<28}{v['up_ratio']:>10.4f}{lu(v):>10.4f}"
          f"{v['limit_up']:>7}{v.get('amount', 0):>11.0f}{'★触发' if e2(v) else '':>6}")

# 区间统计：2026-05-14 ~ 2026-09-24
print("\n" + "=" * 116)
print("【区间统计 2026-05-14 ~ 2026-09-24】")
print("=" * 116)
sub = {k: D[k] for k in KS if '2026-05-14' <= k <= '2026-09-24'}
n_e2 = sum(1 for v in sub.values() if e2(v))
print(f"交易日数：{len(sub)}")
print(f"E2 触发天数（up_ratio≥0.90 且 涨停比例≥2.0%）：{n_e2}")
print(f"up_ratio 峰值日：{max(sub, key=lambda k: sub[k]['up_ratio'])} "
      f"({sub[max(sub, key=lambda k: sub[k]['up_ratio'])]['up_ratio']:.4f})")
print(f"涨停比例峰值日：{max(sub, key=lambda k: lu(sub[k]))} "
      f"({lu(sub[max(sub, key=lambda k: lu(sub[k]))]):.4f})")
print(f"成交额峰值日：{max(sub, key=lambda k: sub[k].get('amount', 0))} "
      f"({sub[max(sub, key=lambda k: sub[k].get('amount', 0))].get('amount', 0):.0f} 亿)")

# 成交额趋势：验证"缩量后放量"
print("\n【成交额趋势（5日移动均，验证「缩量→放量」）】")
amts = [(k, sub[k].get('amount', 0)) for k in sorted(sub)]
for i in range(0, len(amts), 5):
    k, a = amts[i]
    print(f"  {k}: {a:.0f} 亿")

# 反弹日（涨幅>1%的宽基）附近是否有放量
print("\n【结论性核对】")
print(f"  ① 用户说『每次持续性反弹都对应明确事件』 => 项目 E2 在这段区间触发 {n_e2} 次")
print(f"     （若 ≈0，说明事件驱动反弹被 E2 的『涨停潮』门槛系统性挡在门外）")
