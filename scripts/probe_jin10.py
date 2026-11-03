# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】金十数据接口连通性探针（只读，不写库、不改缓存）
================================================================================

用途：换机器/换网络（Render → 自有服务器）后，确认 5 个金十接口哪些还通。
  阿里云 IDC 的 IP 与家宽/境外不同，**新浪已确认封 IDC 段**，金十是否也这样必须实测。

用法：
    cd /path/to/stock-scoring-v2
    backend/.venv/bin/python scripts/probe_jin10.py

判读：
  ✅ OK       —— 拿到数据（括号里是条数/字段数）
  ⚠️ EMPTY    —— HTTP 通但没数据（多半是当天无内容 / 参数窗口内无事件，可接受）
  ❌ HTTP 4xx/5xx —— 被封或鉴权失效（403=IP 被拒，502=app-id/version 写错）
  ❌ TIMEOUT  —— 网络层不通（DNS/连接/TLS）
================================================================================
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))


def _brief(v, n=60):
    s = repr(v)
    return s if len(s) <= n else s[:n] + "..."


def run(name, fn, pick=None):
    """跑一个探测项。`pick(obj)` 返回用于展示的摘要。"""
    t0 = time.time()
    try:
        r = fn()
        cost = time.time() - t0
        if pick:
            summary, empty = pick(r)
        else:
            empty = not r
            summary = _brief(r)
        flag = "⚠️ EMPTY" if empty else "✅ OK"
        print(f"  {flag:<10} {name:<22} {cost:6.2f}s  {summary}")
        return not empty
    except Exception as e:
        cost = time.time() - t0
        # 网络异常要区分开：被封 / 超时 / 其它
        et = type(e).__name__
        msg = str(e)
        if "timeout" in msg.lower() or et in ("ConnectTimeout", "ReadTimeout", "Timeout"):
            kind = "❌ TIMEOUT"
        elif "403" in msg or "Forbidden" in msg:
            kind = "❌ 403 被拒"
        elif "502" in msg or "Bad Gateway" in msg:
            kind = "❌ 502 网关"
        else:
            kind = "❌ " + et
        print(f"  {kind:<10} {name:<22} {cost:6.2f}s  {msg[:110]}")
        if os.environ.get("PROBE_TRACE"):
            traceback.print_exc()
        return False


def main():
    print("=" * 88)
    print("金十接口连通性探测（服务器上运行；只读，不写库）")
    print("=" * 88)

    results = {}

    # ① 快讯（页面/事件流的主源）
    def _flash():
        from app.flash import source
        items = source.fetch_jin10()
        return items

    results["flash"] = run(
        "快讯 flash", _flash,
        lambda r: (f"{len(r or [])} 条，首条 {str((r or [{}])[0].get('time'))[:16]}", not r))

    # ② 财经日历
    def _cal():
        from app.flash import calendar
        items = calendar.get_items()
        return items

    results["calendar"] = run(
        "财经日历 calendar", _cal,
        lambda r: (f"{len(r or [])} 条", not r))

    # ③ 两融（mp-api dynamic-data）
    def _margin():
        from app.flash import margin_sentiment as ms
        return ms.get_margin()

    results["margin"] = run(
        "两融 margin(_vir_96)", _margin,
        lambda r: (f"date={r.get('date')} 余额={r.get('fund_bal')}亿 src={r.get('src', 'jin10')}"
                   if r else "无", not r))

    # ④ 央行黄金储备（mp-api _vir_16）
    def _cbgold():
        from app.flash import cb_gold
        return cb_gold.get_cb_gold()

    # ⚠️ `get_cb_gold()` 返回的是**扁平结构**（month / weight_total_ton / streak_months），
    #    不是我一开始以为的 {items, latest} —— 首版展示字段取错，会显示"期数=0"误导。
    results["cb_gold"] = run(
        "央行黄金 cb_gold(_vir_16)", _cbgold,
        lambda r: (f"month={r.get('month')} 总量={r.get('weight_total_ton')}吨 "
                   f"连增{r.get('streak_months')}月" if r else "无", not r))

    # ⑤ 金十聚合稿（tidy-news，**需要 x-token**，没有就是空）
    def _tidy():
        from app import jin10_tidy
        return jin10_tidy.load()

    results["tidy"] = run(
        "聚合稿 tidy(需token)", _tidy,
        lambda r: (f"day={r.get('day')} items={len(r.get('items') or [])}" if r else "无",
                   not (r or {}).get("items")))

    print("-" * 88)
    ok = sum(1 for v in results.values() if v)
    print(f"结果：{ok} / {len(results)} 项可用")
    if not results["flash"] and not results["calendar"]:
        print("\n⚠️ 快讯与日历都失败 ⇒ 页面「快讯/事件流」会长期空白，需要换源或走代理。")
    print("\n提示：403 多为 IP 段被封（金十对 IDC 可能有策略）；502 多为 x-app-id/x-version 写错。")
    print("     环境变量可覆盖：JIN10_FLASH_URL / JIN10_CALENDAR_URL / JIN10_MP_APP_ID")


if __name__ == "__main__":
    main()
