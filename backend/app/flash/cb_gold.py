"""
================================================================================
【文件作用】全球央行黄金储备月度数据（金十 mp-api）
================================================================================

数据源：金十 mp-api `/api/dynamic-data/child?tb_name=_vir_16`（月度，全球央行黄金储备）
认证：x-app-id（用户提供，可用环境变量 JIN10_MP_APP_ID 覆盖——金十可能轮换）

★ 单位说明：接口 weight 字段单位是「万盎司」不是吨。
  换算：1 万盎司 = 10000 × 31.1034768 克 = 311034.768 克 ≈ 0.311034768 吨。
  例：weight_change=64（万盎司）≈ +19.9 吨，与金十页面展示一致。

字段（本模块统一换算为「吨」后输出）：
  weight_total_ton   总储备（吨）
  weight_change_ton  月度净增（吨）
  streak_months      连续净增月数（结构性买盘强度）
  value_total / value_change  价值（亿美元）/ 月度变化

用途：LLM 黄金分析的"货币轨"（央行购金结构性买盘），支撑"黄金双轨定价"。
月度数据 → 缓存 24h 足够；拉取失败返回 None（调用方静默降级）。
================================================================================
"""

import os
import time
import requests

_URL = "https://mp-api.jin10.com/api/dynamic-data/child"
_TB_NAME = "_vir_16"
_OZ_TO_TON = 0.311034768    # 1 万盎司 = 0.311 吨
_HEADERS = {
    "x-app-id": os.environ.get("JIN10_MP_APP_ID", "fiXF2nOnDycGutVA"),
    "x-version": "1.0",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"),
    "Referer": "https://www.jin10.com/",
    "Origin": "https://www.jin10.com",
    "accept": "application/json, text/plain, */*",
}

_CACHE = {"data": None, "ts": 0.0}
_TTL = 86400          # 月度数据，缓存 24 小时


def get_cb_gold() -> dict:
    """
    全球央行黄金储备月度数据（最新一期，单位已换算为吨）。

    返回：
      {month, weight_total_ton, weight_change_ton, streak_months,
       value_total, value_change}
      拉取/解析失败返回 None（调用方静默降级，不影响主流程）。
    """
    now = time.time()
    if _CACHE["data"] and now - _CACHE["ts"] < _TTL:
        return _CACHE["data"]
    try:
        r = requests.get(
            _URL,
            params={"tb_name": _TB_NAME, "order": "date,desc", "page": 1, "limit": 12},
            headers=_HEADERS, timeout=20)
        rows = (r.json().get("data") or []) if r.status_code == 200 else []
        if rows:
            latest = rows[0]
            # 连续净增月数（从最新往前数，直到首次负增/零增为止）
            streak = 0
            for it in rows:
                if (it.get("weight_change") or 0) > 0:
                    streak += 1
                else:
                    break
            data = {
                "month": latest.get("date", ""),
                "weight_total_ton": round((latest.get("weight_total") or 0) * _OZ_TO_TON, 1),
                "weight_change_ton": round((latest.get("weight_change") or 0) * _OZ_TO_TON, 2),
                "streak_months": streak,
                "value_total": latest.get("value_total"),
                "value_change": latest.get("value_change"),
            }
            _CACHE["data"] = data
            _CACHE["ts"] = now
            return data
        print(f"[cb_gold] 接口返回异常: HTTP {r.status_code}")
    except Exception as e:
        print(f"[cb_gold] 央行购金拉取失败: {e}")
    return None


def cb_gold_line() -> str:
    """格式化为 LLM prompt 的一行；失败返回空字符串（调用方跳过该段）。"""
    d = get_cb_gold()
    if not d or d.get("weight_change_ton") is None:
        return ""
    w = d["weight_change_ton"]
    chg = f"+{w}吨" if w >= 0 else f"{w}吨"
    streak = d.get("streak_months") or 0
    s = f"全球央行净购金({d.get('month')}): {chg}"
    if streak > 1:
        s += f"，连续{streak}个月增持"
    s += "（结构性购金买盘）"
    return s
