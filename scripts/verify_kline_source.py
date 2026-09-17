#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】验证「能否把 K 线数据源从腾讯换成东方财富」
================================================================================

为什么需要它（2026-09-17 实测结论）：
  后端包耗时 ~2h 的根因是腾讯 WAF 冷却（见 generate-kline-pack.py 的
  _waf_record_501 / _waf_wait_until_clear）。有人提议改用东方财富
  push2his（"一次请求拿整段历史，对量宽容"）。实测部分否证：

  · ✅ 确实"一次拿全"—— `lmt` 参数被忽略，永远返回全历史（600519 = 6007 根）
       字段顺序与腾讯兼容：[日期,开,收,高,低,量,额,振幅,涨跌幅,涨跌额,换手率]
  · ❌ 但代价是**每次响应 ~500KB**（腾讯 750 根 ≈ 60KB，8 倍带宽）
  · ❌ "宽容"不成立：约 10~24 个无节流请求后，`push2his.eastmoney.com` 直接
       RemoteDisconnected；换子域（1./82.）、加 `ut` token、换 http 均无效；
       等 150s **未恢复**。而 `push2.eastmoney.com` / `quote.eastmoney.com`
       同时正常 → 是 push2his 这个服务对本 IP 的针对性封禁
  · ⚠️ 项目自己的经验（backend/app/eastmoney.py:126）：**东财封 IP = 24~48h**
       → 一次误触发的代价是**两天没有 K 线**，远大于腾讯 501 的软冷却

  ★ 因此东财**不能做 K 线主源**。唯一可行的用法是「备用源」：腾讯撞 WAF 时，
    只对那批失败股票走东财（请求量小 + 1s 节流），补完即退。
  ★ 但换任何源之前，**必须先过本脚本的核心校验：前复权口径是否与腾讯一致**。
    后端包/前端包（`kline-pack`）都必须同源——否则两套价格序列会让
    「本地评分 vs 后端评分」全序列对不上（项目已有"末根不一致 → 本地 68.8 /
    后端 72.6"的前例，换源会把这个问题从末根放大到全序列）。

用法（★ 务必节流，别把 IP 打死）：
  python scripts/verify_kline_source.py --mode compare      # 复权口径对比（核心）
  python scripts/verify_kline_source.py --mode beg          # beg 能否裁剪响应体
  python scripts/verify_kline_source.py --mode size         # 单请求体积对比
  python scripts/verify_kline_source.py --mode stress -n 20 # 并发容忍度（会撞墙，谨慎）

若出现 RemoteDisconnected：说明该 IP 已被封，**立刻停止**，等数小时~48h 再试
（或改在 GitHub Actions/另一台机器上跑）。
================================================================================
"""
import argparse
import statistics
import sys
import time
from datetime import datetime, timedelta

import requests

EM = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TX = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# 覆盖不同分红/除权历史，最容易暴露复权口径差异
SAMPLE = [("600519", "sh600519", "1.600519"), ("000001", "sz000001", "0.000001"),
          ("000651", "sz000651", "0.000651"), ("601988", "sh601988", "1.601988"),
          ("600276", "sh600276", "1.600276"), ("002452", "sz002452", "0.002452"),
          ("000567", "sz000567", "0.000567"), ("600036", "sh600036", "1.600036")]


def _session(referer):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": referer, "Accept": "*/*"})
    return s


def em_klines(secid, beg="0", lmt="750", fqt="1"):
    """东方财富：返回原始行（字符串，逗号分隔）。"""
    p = {"secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
         "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
         "klt": "101", "fqt": fqt, "beg": beg, "end": "20500101", "lmt": lmt}
    r = _session("https://quote.eastmoney.com/").get(EM, params=p, timeout=20)
    return (r.json().get("data") or {}).get("klines") or [], len(r.content)


def tx_klines(sym, days=1100, count=800):
    """腾讯（与 generate-kline-pack.fetch_kline 同参数），返回 [date, open, close, ...]。"""
    today = datetime.now()
    st = (today - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    en = today.strftime("%Y-%m-%d")
    r = _session("https://gu.qq.com/").get(
        TX, params={"param": f"{sym},day,{st},{en},{count},qfq"}, timeout=20)
    d = r.json().get("data", {}).get(sym, {})
    return d.get("day") or d.get("qfqday") or [], len(r.content)


def _guard(exc):
    """被封时给出明确指引并退出（继续打只会把封禁拖更久）。"""
    msg = str(exc)
    if "RemoteDisconnected" in msg or "Connection aborted" in msg:
        print("\n!! 该 IP 已被 push2his 封禁（RemoteDisconnected）。")
        print("!! 立刻停止 — 继续请求会延长封禁（项目经验：24~48h）。")
        print("!! 请在数小时~48h 后、或换一台机器/CI 再跑。")
        sys.exit(2)
    raise exc


def mode_compare(gap, n):
    """★ 核心：东财 fqt=1（前复权）与腾讯 qfq 的收盘价是否逐日一致。"""
    print("=" * 78)
    print("[复权口径] 东财 fqt=1 vs 腾讯 qfq —— 前复权收盘价逐日对比")
    print("  判据：max 相对差 < 0.1% 且无 >0.5% 的离群日 → 口径一致、可换源")
    print("=" * 78)
    verdict = True
    for code, sym, secid in SAMPLE[:n]:
        try:
            ek, _ = em_klines(secid)
            tk, _ = tx_klines(sym)
        except Exception as e:
            _guard(e)
        E = {r.split(",")[0]: float(r.split(",")[2]) for r in ek}
        T = {r[0]: float(r[2]) for r in tk}
        ov = sorted(set(E) & set(T))[-750:]
        if not ov:
            print(f"  {code}  无重叠日期 —— 无法比较")
            verdict = False
            continue
        ds = [abs(E[d] - T[d]) / T[d] for d in ov if T[d] > 0]
        big = sum(1 for x in ds if x > 0.005)
        bad = max(ds) > 0.001 or big > 0
        verdict &= (not bad)
        print(f"  {code}  n={len(ov):>4} {ov[0]}~{ov[-1]}  max={max(ds)*100:7.3f}%  "
              f"mean={statistics.mean(ds)*100:.4f}%  >0.5%天数={big}  "
              f"{'✗ 不一致' if bad else '✓ 一致'}")
        if bad:
            print(f"        样例日期={ov[-3:]} 东财={[E[d] for d in ov[-3:]]} "
                  f"腾讯={[T[d] for d in ov[-3:]]}")
        time.sleep(gap)
    print("-" * 78)
    print("结论：" + ("前复权口径一致 → 换源在数据层面可行（仍须评估限流）"
                    if verdict else
                    "★ 口径不一致 → 不能换源（会让本地评分与后端对不上）"))


def mode_beg(gap):
    """beg 能否裁剪响应体：决定"一次拿全"的带宽代价。"""
    print("=" * 78)
    print("[beg 裁剪] 传 beg=<日期> 能否只返回该日期之后的数据")
    print("=" * 78)
    for beg in ("0", "20230101", "20250101", "20250901"):
        try:
            k, size = em_klines("1.600519", beg=beg)
        except Exception as e:
            _guard(e)
        if k:
            print(f"  beg={beg:<10} n={len(k):>5}  first={k[0].split(',')[0]}  "
                  f"last={k[-1].split(',')[0]}  响应={size/1024:.0f}KB")
        else:
            print(f"  beg={beg:<10} 空响应")
        time.sleep(gap)


def mode_size(gap):
    """单请求体积对比：外推 2057 只的日下载量。"""
    print("=" * 78)
    print("[体积] 同一只股票，东财 vs 腾讯 的单次响应大小")
    print("=" * 78)
    try:
        ek, esize = em_klines("1.600519")
        print(f"  东财  600519 n={len(ek):>5}  {esize/1024:>7.0f} KB")
    except Exception as e:
        _guard(e)
    time.sleep(gap)
    tk, tsize = tx_klines("sh600519")
    print(f"  腾讯  600519 n={len(tk):>5}  {tsize/1024:>7.0f} KB")
    print(f"  → 东财/腾讯 = {(esize / max(tsize, 1)):.1f} 倍；"
          f"2057 只外推：东财 {2057 * esize / 1024 / 1024:.0f} MB / "
          f"腾讯 {2057 * tsize / 1024 / 1024:.0f} MB")


def mode_stress(gap, n):
    """并发容忍度（会撞墙，谨慎）。串行节流版：测可持续速率。"""
    from concurrent.futures import ThreadPoolExecutor
    codes = [r[2] for r in SAMPLE] * ((n // len(SAMPLE)) + 1)
    codes = codes[:n]
    print("=" * 78)
    print(f"[并发容忍度] {n} 只 / 5 线程 / 不节流 —— 观察多久撞墙")
    print("=" * 78)

    def one(c):
        try:
            k, _ = em_klines(c)
            return len(k) > 0
        except Exception:
            return False

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=5) as ex:
        res = list(ex.map(one, codes))
    dt = time.time() - t0
    ok = sum(1 for x in res if x)
    rate = n / dt if dt else 0
    print(f"  ok={ok}/{n}  耗时={dt:.1f}s  → {rate:.1f} req/s")
    if ok < n:
        print("  ★ 已触发限流 —— 说明「对量宽容」不成立")
    elif rate > 1:
        print(f"  外推 2057 只（同速）≈ {2057 / rate / 60:.1f} 分钟")
    print("  注意：即便本次全成功，也只说明低频可行；东财封禁是 24~48h 级别，"
          "长期高频需压到 ~1 req/s 并配合 push2delay 兜底")


def main():
    ap = argparse.ArgumentParser(description="验证 K 线备用数据源（东财）能否替代腾讯")
    ap.add_argument("--mode", default="compare",
                    choices=["compare", "beg", "size", "stress"])
    ap.add_argument("--gap", type=float, default=1.0, help="请求间隔秒数（默认 1.0，勿调小）")
    ap.add_argument("-n", type=int, default=8, help="样本数（compare/默认 8）")
    args = ap.parse_args()

    if args.mode == "compare":
        mode_compare(args.gap, args.n)
    elif args.mode == "beg":
        mode_beg(args.gap)
    elif args.mode == "size":
        mode_size(args.gap)
    else:
        mode_stress(args.gap, args.n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
