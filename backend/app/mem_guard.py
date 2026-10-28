# -*- coding: utf-8 -*-
"""内存护栏 —— 接近上限时**主动跳过会新增全市场副本的操作**，避免 OOM 重启循环。

================================================================================
【为什么需要】（2026-10-28 用户报："今天早上内存爆满，服务器多次重启，即使回退旧
  版本一样触发重启，只好暂停服务一会再打开才正常"）
  · `memory_probe` 在 08:30-12:00 **一条记录都没有**（30 分钟一采、且 `periodic_loop`
    启动即采一条）⇒ 进程在那段时间反复被杀，连采样都写不进去；
  · "回退旧版本也一样" 印证**不是某次代码回归**，而是"启动/开盘后反复回灌"这类与环境
    同频的因素（行情全量刷新、快照整份恢复、前端并发拉取叠加）。
  · 这类场景最怕的是**恶性循环**：内存紧 → GC 变慢 → 下一轮刷新又构造新 dict（瞬时
    双份）→ 更紧 → 被 kill → 重启后重新恢复快照 → 再紧。
  ⇒ 本模块只做一件事：**压力大时让"重活"这一轮先别做**，把资源留给读路径，
    等内存回落再继续。这是降级，但比进程被杀（所有缓存清零、前端全红）好。

【纪律】
  1. 只"跳过 + 打日志"，**绝不主动 gc / 释放别人的缓存** —— 那会把热路径变冷路径，
     用户侧表现为"页面忽然很慢/数据消失"，比 OOM 更难解释。
  2. 阈值默认 70%（`MEM_SOFT_PCT` 可调）：留 30% 给一次请求的瞬时峰值。
  3. 读不到 RSS（本地非 Linux 且无 resource）⇒ 一律"不高压"（fail-open，不误伤）。
  4. 护栏必须**只影响"新增副本"的动作**（刷新/整份恢复），不能挡读接口。
================================================================================
"""
import os
import sys
from typing import Optional

# 与 `routers/system._MEM_LIMIT_MB` 同口径（Render 实例 500MB）
_LIMIT_MB = float(os.environ.get("MEM_LIMIT_MB") or 500)
# 软阈值：超过就跳重活（低于告警线 80%，留出反应余量）
_SOFT_PCT = float(os.environ.get("MEM_SOFT_PCT") or 70)


def rss_mb() -> Optional[float]:
    """当前进程 RSS（MB）。优先 `/proc/self/status`（Render = Linux，最准）。"""
    try:
        with open("/proc/self/status", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)      # kB → MB
    except Exception:
        pass
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        div = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
        return round(v / div, 1)
    except Exception:
        return None


def used_pct() -> Optional[float]:
    """RSS 占实例上限的百分比；读不到返回 None。"""
    r = rss_mb()
    if r is None or _LIMIT_MB <= 0:
        return None
    return round(r / _LIMIT_MB * 100, 1)


def over_soft() -> bool:
    """是否已达软阈值（默认 70%）。读不到 RSS ⇒ False（fail-open）。"""
    p = used_pct()
    return bool(p is not None and p >= _SOFT_PCT)


def guard(tag: str, pct: float = None) -> bool:
    """重活入口的统一检查：高压则打一行 WARN 并返回 True（调用方据此跳过）。

    `tag` 只用于日志定位（ASCII，铁律⑥）。
    """
    p = used_pct()
    if p is None or p < (pct if pct is not None else _SOFT_PCT):
        return False
    print(f"[mem_guard] SKIP {tag}: rss={rss_mb()}MB / {_LIMIT_MB:.0f}MB "
          f"({p}% >= {pct if pct is not None else _SOFT_PCT}%) "
          f"--> defer heavy work to avoid OOM restart loop")
    return True
