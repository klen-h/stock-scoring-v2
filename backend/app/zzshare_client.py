"""
================================================================================
【文件作用】zzshare 数据源接入封装（单点隔离）
================================================================================
说明：
  - zzshare 仅作低频补充数据源（匿名限流/慢，内部自带超时重试）。
  - token 通过环境变量 ZZSHARE_TOKEN 提供（可选，官网免费获取；缺省匿名）。
  - 若 zzshare 库升级导致接口签名变化，只需改本模块，不影响上层。

实测（0.4.11，2026-09-03）：
  ✅ finance_latest/finance_stock 等 9 个财务接口可用
  ✅ uplimit_*/plates_*/ths_*/lhb_*/sentiment_*/trade_days 可用
  ❌ stock_moneyflow / rt_k / market_mf 此版本不存在（README 声明超前）
================================================================================
"""

import os

_client = None


def get_api():
    """懒加载 DataApi 单例（token 缺省匿名）。"""
    global _client
    if _client is None:
        from zzshare.client import DataApi
        token = os.environ.get("ZZSHARE_TOKEN") or None
        _client = DataApi(token=token)
    return _client


def to_zz_code(code: str) -> str:
    """内部 6 位代码 → zzshare ts_code（000001→000001.SZ，600000→600000.SH）。"""
    c = str(code).lower()
    if c.endswith((".sh", ".sz", ".bj")):
        return str(code).upper()
    if len(c) == 6:
        if c[0] in ("0", "3"):
            return f"{c}.SZ"
        if c[0] == "6":
            return f"{c}.SH"
        if c[0] in ("4", "8", "9"):
            return f"{c}.BJ"
    return str(code)
