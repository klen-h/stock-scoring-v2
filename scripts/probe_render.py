#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】Render 后端探针：绕过浏览器直连接口，分辨"CORS 错误"的真假
================================================================================

为什么需要它：浏览器对"响应没有 CORS 头"的失败统一报 CORS 错，但真实原因往往是：
  ① 后端 500（异常响应不走 CORS 中间件，天然无 CORS 头）
  ② Render 免费版冷启动（睡 15 分钟，唤醒要 50s+，请求超时/被重置）
  ③ 正在部署重启
三种都不是真的跨域配置问题。

用法：
  python scripts/probe_render.py                  # 探 health + 示例接口
  python scripts/probe_render.py /api/xxx         # 探指定接口
================================================================================
"""

import sys

import requests

BASE = "https://stock-scoring-v2.onrender.com"
ORIGIN = "https://klen-h.github.io"


def probe(path: str) -> None:
    try:
        r = requests.get(BASE + path, timeout=95)
        cors = r.headers.get("access-control-allow-origin", "<无 CORS 头>")
        body = (r.text or "")[:200].replace("\n", " ")
        print(f"{path}\n  HTTP {r.status_code} | Allow-Origin: {cors}\n  body: {body}\n")
    except Exception as e:
        print(f"{path}\n  请求异常: {type(e).__name__}: {str(e)[:200]}\n")


def main():
    paths = ["/api/health"]
    if len(sys.argv) > 1:
        paths.append(sys.argv[1] if sys.argv[1].startswith("/") else "/" + sys.argv[1])
    else:
        paths.append("/api/strategies/000567/rsi?period=14")

    for p in paths:
        probe(p)

    # 模拟浏览器跨域请求（带 Origin），看 CORS 头是否正常回显
    try:
        r = requests.get(BASE + paths[0], headers={"Origin": ORIGIN}, timeout=95)
        allow = r.headers.get("access-control-allow-origin", "<无>")
        verdict = "OK" if allow == ORIGIN else "!! 异常"
        print(f"模拟浏览器跨域: HTTP {r.status_code} | Allow-Origin: {allow} [{verdict}]")
        print("解读: 接口 200 且 Allow-Origin 正确 -> 后端/CORS 无问题;")
        print("      浏览器报 CORS 多为当时 500/冷启动/部署的\"症状\", 重试即可。")
    except Exception as e:
        print(f"模拟浏览器跨域异常: {str(e)[:200]}")


if __name__ == "__main__":
    main()
