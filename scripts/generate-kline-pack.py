#!/usr/bin/env python3
"""
================================================================================
【文件作用】生成 K 线数据包（供前端 IndexedDB 使用）
================================================================================

每天收盘后由 GitHub Actions 调用，生成：
  - kline-pack-YYYYMMDD.json.gz  完整数据包（~5-10MB）
  - kline-delta-YYYYMMDD.json    增量数据包（~500KB，仅当日新增/更新）
  - kline-pack-latest.json.gz    指向最新完整包的软链接/副本

数据格式：
{
  "version": 2,
  "date": "20260822",
  "stocks": {
    "000001": {
      "name": "平安银行",
      "market_cap": 1234.5,
      "klines": [
        ["2026-08-21", 12.5, 12.8, 12.3, 12.6, 1234567],
        ...
      ]
    }
  }
}

使用方式：
  python scripts/generate-kline-pack.py [--output-dir OUTPUT_DIR] [--days 60] [--top 3000]
================================================================================
"""

import argparse
import gzip
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

# ── 配置 ──
DEFAULT_DAYS = 150      # K 线天数（需足够长让 EMA26/DEA 系列指标收敛，与后端 500 天历史对齐）
BATCH_SIZE = 50         # 批量请求行情每批数量
KLINE_BATCH_SIZE = 10   # K 线请求每批数量（仅用于节流节奏，仍是串行请求）

# ★ 以下四项可用环境变量覆盖（2026-09-06 后端包超时对策）：
#   后端包 ~700 只×750 根，串行 + 单只最多 3 次重试(1+3+6s 退避 + 3×10s 超时)
#   = 坏股票单只惩罚可达 40s，几百只堆积直接顶穿 90 分钟 job 上限。
#   后端 workflow 用 PACK_REQUEST_TIMEOUT=6 / PACK_KLINE_RETRIES=2 /
#   PACK_RETRY_BACKOFF=1,2 / PACK_CONSECUTIVE_COOLDOWN=6 收紧，
#   前端包（150 根、失败率低）保持原默认值不变。
def _env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default

REQUEST_TIMEOUT = _env_int("PACK_REQUEST_TIMEOUT", 10)   # 请求超时（秒）
WAF_COOLDOWN = _env_int("PACK_WAF_COOLDOWN", 120)        # WAF 触发后全局冷却基准（秒）
# ★ 2026-09-17：自适应退避上限 —— 冷却期内再次 501 就 ×2 加码，最多到此值。
#   实测教训：固定 45s 时当天 [WAF] 拦截从注释记载的「40+ 次」升到 ~117 次、耗时
#   反而更长 —— 腾讯按「滑动窗口速率」判定，45s 的静默排不空窗口，IP 一直停在
#   惩罚区（冷却结束→立刻又被拦→再冷却，连续惩罚）。结论：「冷却该多长」应由
#   WAF 自己回答，不再人工猜固定值。见 _waf_record_501 / _waf_wait_until_clear。
WAF_MAX_COOLDOWN = _env_int("PACK_WAF_MAX_COOLDOWN", 300)
# ★ 单只股票在冷却上的最长累计等待（秒）。超限就放行该股（记为失败、交下一轮），
#   避免一个线程被一支股票无限占住 —— 见 _waf_wait_until_clear 的返回语义。
WAF_WAIT_MAX = _env_int("PACK_WAF_WAIT_MAX", 900)
KLINE_RETRIES = _env_int("PACK_KLINE_RETRIES", 3)        # 单只股票重试次数（含首次）
RETRY_BACKOFF = [int(x) for x in
                 (os.environ.get("PACK_RETRY_BACKOFF") or "1,3,6").split(",") if x]
CONSECUTIVE_COOLDOWN = _env_int("PACK_CONSECUTIVE_COOLDOWN", 10)  # 连续失败暂停阈值
# ★ K 线 count 上限（2026-09-07 实测，见 fetch_kline 注释）：超 800 会被腾讯拒绝/截断
KLINE_COUNT_CAP = _env_int("PACK_KLINE_COUNT_CAP", 800)
# ★ 中途熔断：K 线前 N 只零成功即判定「参数被接口拒绝 / IP 被封」，立刻报错退出，
#   不再空耗到 job 超时（原来 100% 失败要熬到 90-150 分钟上限才发现）
MIDWAY_FAIL_FAST = _env_int("PACK_MIDWAY_FAIL_FAST", 80)

# A 股代码池（与 backend/app/tencent.py 保持一致）
DISABLED_PREFIXES = {"688", "300", "301"}

# ── 股票池质量门槛（与后端/前端一致）──
MIN_FLOAT_CAP_YI = 50     # 流通市值 > 50 亿（腾讯 fields[44]，单位亿元）
MIN_PRICE = 3.0           # 股价 > 3 元

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Referer": "https://gu.qq.com/",
})

# ── ★ 2026-09-17 主动限速（根治方向）──
# 被动罚站（撞 501 才冷却）只是事后止血：只要**发出的请求速率**高于腾讯阈值，就必然
# 反复撞墙；而 WAF 按滑动窗口判定，撞得越勤窗口越排不空 → 自锁。
#   实测佐证：8-17 那次 5 线程 × 每线程 0.5~0.9s sleep ≈ 峰值 7 req/s，且 5 个请求
#   可同时飞出，日志 [WAF] 拦截 ~117 次。
#   ★ 另一条必须纠正的读数：当天「累计冷却等待 29572s」是**跨线程累加**（5 个线程各
#   等各算），不是墙钟 —— 折算墙钟约 1/5。把它当墙钟会得出「8.2 小时全在冷却」的
#   错误结论（指标已修，见 _waf_report）。
# 这里加**全局令牌桶**：不管开几个线程，全进程的请求**发出速率**硬压到 PACK_QPS 内。
# 这是与「并发数」解耦的正确旋钮 —— 并发只决定在途请求的重叠度，不决定速率。
PACK_QPS = float(os.environ.get("PACK_QPS", "2") or 2)
_qps_lock = threading.Lock()
_qps_next = 0.0            # 下一个允许发出请求的时刻（无突发的匀速限流）


def _rate_limit() -> None:
    """全局令牌桶：保证任意两次请求的发出间隔 ≥ 1/PACK_QPS 秒。

    PACK_QPS<=0 表示关闭（回退到旧的「每线程自己 sleep」行为）。
    线程在这里排队，而不是各自 sleep —— 所以「并发 5」仍然有效（5 个在途请求重叠
    等待响应），但发出速率恒定。这正是打不破 WAF 阈值的关键。
    """
    global _qps_next
    if PACK_QPS <= 0:
        return
    interval = 1.0 / PACK_QPS
    with _qps_lock:
        now = time.time()
        start = _qps_next if _qps_next > now else now
        _qps_next = start + interval
        wait = start - now
    if wait > 0:
        time.sleep(wait)

# ── WAF 全局限流状态（与后端 tencent.py 同策略）──
# 腾讯 WAF 触发后（HTTP 501）需要暂停所有 K 线请求，避免被持续封禁。
# ★ 并发拉取（后端包 workers>1）下这两个全局量必须加锁，否则多线程会
#   同时读到过期状态、把冷却期内的请求全打出去（反而加剧封禁）。
_waf_blocked_until = 0.0
_consecutive_failures = 0
_waf_lock = threading.Lock()

# ★ 2026-09-17：退避自适应 + 观测指标（不再人工猜冷却时长，见 WAF_MAX_COOLDOWN 注释）
_waf_backoff = 1.0          # 当前退避倍数（× WAF_COOLDOWN）
_waf_events = 0             # 501 触发次数（进程内累计；仅"非冷却期"首次开窗才 +1）
_waf_wait_total = 0.0       # 累计冷却等待（★ 线程·秒，跨线程累加，不是墙钟）
_proc_t0 = 0.0              # 进程内首次 K 线请求时刻（墙钟口径的观测定点）


def _waf_wait_until_clear() -> bool:
    """冷却期内循环等待到真正解除。True=已解除、可发请求；False=等待超上限。

    ★ 2026-09-17 修正（关键）：原实现在 fetch_kline 开头睡一轮（≤冷却+5s），醒来若
      发现窗口被其它线程续期，就在重试循环开头 `return None`。长封禁期里这退化成
      「每股白睡一整个冷却周期 + 该股被误记为失败」的空转 —— 8-17 日志 967/2057 处
      连续 150 只零成功正是这么来的；这些「假失败」再进下一轮重试，又把请求打到
      正烫的 IP 上，正反馈放大封禁。故改为睡到解除（期间不发请求 —— 这正是让 WAF
      滑动窗口排空所需要的）。
    ★ 但「死等」也有反效果：窗口若被反复续期，线程会被一支股票无限占住（退避升到
      300s 时，吞吐可跌到 5 只/300s）。故加累计上限 WAF_WAIT_MAX，超限返回 False，
      由调用方记为失败交给下一轮 —— 既不无限空转，也不制造大批假失败。
    """
    global _waf_wait_total
    spent = 0.0
    while True:
        with _waf_lock:
            remain = _waf_blocked_until - time.time()
            cur = WAF_COOLDOWN * _waf_backoff
        if remain <= 0:
            return True
        if spent >= WAF_WAIT_MAX:
            print(f"\n  [WAF] 单只累计等待达上限 {WAF_WAIT_MAX}s → 放行该股，交下一轮重试")
            return False
        # 抖动：★ 2026-09-17 收紧 —— 「5 线程同刻醒来齐发」这个当初要抖动的理由，
        #   现在已由**全局令牌桶**接管（放行间隔恒为 1/PACK_QPS），长抖动纯属白等：
        #   实测一次 3s 的阻塞被原抖动（最多 30s）拖到 19.5s。故降到 1~5s 仅作微错峰。
        # ★ 抖动必须**夹在预算内**：否则会把 WAF_WAIT_MAX 上限冲掉（实测 cap=2s 却等 8.5s）
        budget = max(1.0, WAF_WAIT_MAX - spent)
        base = min(remain + 1.0, cur + 5, budget)
        jitter = random.random() * max(1.0, min(5.0, cur * 0.1))
        nap = min(base + jitter, budget)
        with _waf_lock:
            _waf_wait_total += nap
        spent += nap
        time.sleep(nap)


def _waf_record_501(symbol: str) -> None:
    """记录一次 501：非冷却期触发 → 开新窗口；冷却期内仍被拦 → 退避 ×2 加码。"""
    global _waf_blocked_until, _consecutive_failures, _waf_backoff, _waf_events
    with _waf_lock:
        now = time.time()
        if now >= _waf_blocked_until:
            _waf_events += 1
        else:
            _waf_backoff = min(WAF_MAX_COOLDOWN / WAF_COOLDOWN, _waf_backoff * 2)
        _waf_blocked_until = now + WAF_COOLDOWN * _waf_backoff
        _consecutive_failures = 0
        n, mult, waited = _waf_events, _waf_backoff, _waf_wait_total
    print(f"\n  [WAF] 第 {n} 次拦截 {symbol} → 本次冷却 {WAF_COOLDOWN * mult:.0f}s"
          f"（累计线程等待 {waited:.0f}s，非墙钟）")


def _waf_report() -> str:
    """WAF 观测汇总：跑完打印，便于不同参数做硬对比（而非靠感觉）。

    ★ 口径（2026-09-17 修正，重要）：`_waf_wait_total` 是**跨线程累加**
      （每个线程各等各算）→ 它**不是墙钟**；5 线程并行冷却时约等于墙钟的 5 倍。
      这个坑真的踩了：复盘时把 29572 线程·秒读成「8.2 小时全在冷却」，据此得出
      「97% 时间在冷却」的错误结论（按墙钟折算只有约 1/5）。
      这里同时给出墙钟与「平均并行等待线程数」，杜绝再次误读。
    """
    with _waf_lock:
        n, mult, waited = _waf_events, _waf_backoff, _waf_wait_total
    wall = (time.time() - _proc_t0) if _proc_t0 else 0.0
    para = (waited / wall) if wall > 0 else 0.0
    return (f"WAF 汇总: 拦截 {n} 次 / 累计线程等待 {waited:.0f} 线程·秒（非墙钟）"
            f" / 进程运行 {wall:.0f}s → 平均 {para:.1f} 个线程在冷却"
            f"（占并发 {100 * para / max(1, 5):.0f}%）"
            f" / 末次退避 ×{mult:.0f}（冷却 {WAF_COOLDOWN * mult:.0f}s）"
            f" / 限速 {PACK_QPS:g} QPS")


def _is_valid_stock(name: str, pe: float = 0) -> bool:
    """
    过滤无效股票：
    - ST/*ST 风险警示股
    - 亏损股（PE <= 0）
    """
    clean = name.replace(' ', '').upper()
    if clean.startswith('ST') or clean.startswith('*ST') or clean.startswith('SST'):
        return False
    # 亏损股（PE <= 0 表示亏损或无数据）
    if pe <= 0:
        return False
    return True


def _pass_quality_filter(price: float, float_cap_yi: float) -> bool:
    """质量门槛：流通市值 > 50 亿、股价 > 3 元（成交额门槛已移除：盘中早盘时段会误杀大量股票）"""
    if float_cap_yi < MIN_FLOAT_CAP_YI:
        return False
    if price < MIN_PRICE:
        return False
    return True


def build_stock_pool() -> List[Tuple[str, str]]:
    """生成全 A 股代码列表"""
    codes = []
    # 深市主板 000001-005999
    for i in range(1, 6000):
        code = f"{i:06d}"
        if not any(code.startswith(p) for p in DISABLED_PREFIXES):
            codes.append(("sz", code))
    # 创业板 300001-301999
    for i in range(300001, 302000):
        code = f"{i:06d}"
        if not any(code.startswith(p) for p in DISABLED_PREFIXES):
            codes.append(("sz", code))
    # 沪市主板 600000-605999
    for i in range(600000, 606000):
        code = f"{i:06d}"
        if not any(code.startswith(p) for p in DISABLED_PREFIXES):
            codes.append(("sh", code))
    # 科创板 688001-688999
    for i in range(688001, 689000):
        code = f"{i:06d}"
        if not any(code.startswith(p) for p in DISABLED_PREFIXES):
            codes.append(("sh", code))
    return codes


def fetch_realtime_batch(codes: List[Tuple[str, str]]) -> Dict:
    """
    批量拉取实时行情，返回 {code: {name, price, market_cap, ...}}
    """
    result = {}
    total = len(codes)
    
    for i in range(0, total, BATCH_SIZE):
        batch = codes[i:i + BATCH_SIZE]
        symbols = ",".join(f"{prefix}{code}" for prefix, code in batch)
        
        try:
            url = f"https://qt.gtimg.cn/q={symbols}"
            resp = _session.get(url, timeout=REQUEST_TIMEOUT)
            resp.encoding = "gbk"
            text = resp.text
            
            for line in text.strip().split("\n"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                try:
                    var_name, data_str = line.split("=", 1)
                    data_str = data_str.strip('"').strip(";").strip('"')
                    fields = data_str.split("~")
                    if len(fields) < 50:
                        continue
                    
                    code = fields[2]
                    name = fields[1]
                    price = float(fields[3]) if fields[3] else 0
                    market_cap = float(fields[45]) if len(fields) > 45 and fields[45] else 0   # 总市值（亿元）
                    float_cap = float(fields[44]) if len(fields) > 44 and fields[44] else 0    # 流通市值（亿元）
                    pe = float(fields[39]) if len(fields) > 39 and fields[39] else 0
                    pb = float(fields[46]) if len(fields) > 46 and fields[46] else 0
                    turnover_rate = float(fields[38]) if len(fields) > 38 and fields[38] else 0
                    change_pct = float(fields[32]) if len(fields) > 32 and fields[32] else 0
                    
                    if price > 0 and name and _is_valid_stock(name, pe) and \
                            _pass_quality_filter(price, float_cap):
                        result[code] = {
                            "name": name,
                            "price": price,
                            "market_cap": market_cap,
                            "float_cap": float_cap,
                            "pe": pe,
                            "pb": pb,
                            "turnover_rate": turnover_rate,
                            "change_pct": change_pct,
                        }
                except (ValueError, IndexError) as e:
                    continue
            
            # 进度显示
            done = min(i + BATCH_SIZE, total)
            print(f"  行情: {done}/{total} ({len(result)} 有效)", end="\r")
            
            # 避免触发 WAF
            if (i // BATCH_SIZE) % 20 == 0 and i > 0:
                time.sleep(0.5)
                
        except Exception as e:
            print(f"\n  批次 {i//BATCH_SIZE + 1} 失败: {e}")
            time.sleep(WAF_COOLDOWN)
    
    print(f"  行情完成: {len(result)} 只有效股票")
    return result


def fetch_kline(code: str, days: int = 60) -> Optional[List]:
    """
    获取单只股票 K 线数据（带重试 + WAF 检测 + 全局熔断）
    返回: [[date, open, high, low, close, volume], ...] 或 None
    """
    global _waf_blocked_until, _consecutive_failures, _proc_t0
    if not _proc_t0:
        _proc_t0 = time.time()      # 进程内首次 K 线请求时刻（墙钟口径观测点）

    # WAF 冷却中：睡到真正解除再继续请求，而不是跳过。
    # ★ 2026-09-07 实测教训：并发拉 1442 只时腾讯在 ~889 只处触发 501 → 全局冷却。
    #   若直接 return None，冷却期间的股票全被计失败（553 只×两轮全丢，
    #   889/1442=61.7% 触发 80% 护栏中止）。
    # ★ 2026-09-17：改为「循环睡到解除」（见 _waf_wait_until_clear）——原「睡一轮
    #   就放弃」在长封禁期会把股票批量误记为失败，反噬成更多请求、放大封禁。
    #   该函数有累计上限：真封死时返回 False，放行该股交下一轮，不无限占住线程。
    if not _waf_wait_until_clear():
        return None

    # 指数/ETF 代码自带前缀（sh000300 / sz159915 / sh510300）→ 不能再拼一次
    # ★ 2026-09-07 实测 bug：原代码无条件拼前缀，把 sh000300 变成 szsh000300 →
    #   腾讯对畸形 symbol 直接 501（第 2 轮 ETF/指数全被 WAF 拦截的根因）
    if code.startswith(("sh", "sz", "bj")):
        symbol = code
    else:
        prefix = "sh" if code.startswith("6") else "sz"
        symbol = f"{prefix}{code}"

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y-%m-%d")  # 多取一些，确保够用

    # ★ count 硬上限（2026-09-07 实测腾讯 fqkline）：
    #   count=2200 → 0 根（服务端拒绝，0.1s 快速失败）
    #   count=1600 → 仅 640 根（被截断）｜count=800 + 区间 1100 天 → 728 根（≈2.9 年）
    #   ← 后端包 days=1100 时 days*2=2200 100% 拿不到数据，正是「后端一直超时」的根因
    #     （单只 3 次重试×超时 ≈ 40s，千只必然顶穿 job 上限）。
    #   前端包 days=150 → count=300，未触及上限，行为不变。
    count = min(days * 2, KLINE_COUNT_CAP)
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        "param": f"{symbol},day,{start_date},{end_date},{count},qfq",
    }

    last_err = None
    for attempt in range(KLINE_RETRIES):
        # 冷却中则等解除（有上限），不放弃该股（否则「假失败」会灌满重试轮）
        if not _waf_wait_until_clear():
            return None
        # ★ 主动限速：全进程的请求**发出速率**硬压到 PACK_QPS 以内。
        #   这才是"打不破阈值"的关键 —— 被动冷却只能事后止血（见 _rate_limit 注释）。
        _rate_limit()
        try:
            resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)

            # WAF 检测：腾讯返回 501 表示被防火墙拦截 → 全局冷却
            if resp.status_code == 501:
                _waf_record_501(symbol)
                return None

            resp.raise_for_status()
            data = resp.json()

            klines_raw = data.get("data", {}).get(symbol, {})
            day_data = klines_raw.get("day") or klines_raw.get("qfqday") or []

            if not day_data:
                last_err = "空数据"
                time.sleep(RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else 6)
                continue

            # 解析并格式化
            result = []
            for item in day_data[-days:]:  # 只取最近 N 天
                if len(item) >= 6:
                    date = item[0]
                    open_p = float(item[1])
                    close = float(item[2])
                    high = float(item[3])
                    low = float(item[4])
                    volume = float(item[5])
                    result.append([date, open_p, high, low, close, volume])

            if len(result) >= 30:
                with _waf_lock:
                    _consecutive_failures = 0
                    _waf_backoff = 1.0        # 通了 → 退避回落到基准
                return result
            last_err = f"K线不足({len(result)}根)"
            time.sleep(RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else 6)

        except Exception as e:
            last_err = str(e)
            # 连续失败 → 退避冷却（避免触发更严格的封禁）
            # ★ 加锁只保护计数，sleep 必须放在锁外（否则并发线程全被串成串行）
            with _waf_lock:
                _consecutive_failures += 1
                _cf = _consecutive_failures
                if _cf >= CONSECUTIVE_COOLDOWN:
                    wait = min(60, _cf * 5)
                    _consecutive_failures = 0
                else:
                    wait = 0
            if wait:
                print(f"\n  连续失败 {_cf} 次，暂停 {wait}s")
                time.sleep(wait)
            elif attempt < len(RETRY_BACKOFF):
                time.sleep(RETRY_BACKOFF[attempt])

    return None


def _throttle(index: int) -> None:
    """节流：随机间隔避免固定节奏被识别为爬虫；每 50 只额外停顿让 WAF 松弛。

    并发模式（workers>1）下线程本身已分摊节奏，这里只保留轻微抖动，
    否则 N 个线程各睡 0.3~0.8s 会把并发收益又抵消掉。
    """
    time.sleep(0.3 + random.random() * 0.5)
    if index % 50 == 0 and index > 0:
        time.sleep(1.5)


def _throttle_concurrent(index: int) -> None:
    """并发模式下的节流。

    ★ 2026-09-07 实测教训：0~0.15s 轻节流（≈50 req/s）在 ~889 只处触发腾讯 501；
      前端包串行 ≈1.6 req/s 从不触发。并发必须配节流——否则只是把超时换成封禁。
    ★ 2026-09-17：限速已由**全局令牌桶** `_rate_limit`（PACK_QPS）统一负责 —— 这里
      不再 sleep，否则与令牌桶叠加成"双重节流"，会让 PACK_QPS 这个旋钮失去意义
      （设 PACK_QPS=5 也上不去）。只留极小抖动打散请求指纹；PACK_QPS<=0 才回退旧行为。
    """
    if PACK_QPS > 0:
        time.sleep(random.random() * 0.05)
        return
    time.sleep(0.5 + random.random() * 0.4)


def refetch_stale_lastbar(klines_data: Dict, days: int, workers: int = 1,
                          max_rounds: int = 2) -> Dict:
    """末根落后于池内最新交易日的股票，补拉（腾讯复权序列收盘后是"逐只"补齐的）。

    ★ 2026-09-11 实测：18:00 生成的前端包里 739/1534 只末根停在上一交易日
      （000567 海德股份 前端包 09-09 / 后端包 09-10），而 20:30 生成的后端包
      1425/1431 都是当日 —— 同一接口、同一交易日，差异只是拉取时刻。
      后果：本地 K 线少一根 → 本地评分与后端对不上（实测 000567 本地 68.8 /
      后端 72.6），详情页图形也停在昨天。这里对落后者补拉一轮（复用 WAF 冷却
      与退避），仍缺的保留原数据，不做假 bar。
    """
    if not klines_data:
        return klines_data
    for rnd in range(max_rounds):
        last = [v[-1][0] for v in klines_data.values() if v]
        if not last:
            break
        newest = max(last)
        lag = [c for c, v in klines_data.items() if v and v[-1][0] < newest]
        if not lag:
            break
        # 全池几乎都落后 = 当日数据尚未发布（而非个别滞后），补拉没意义
        if len(lag) > len(klines_data) * 0.9:
            print(f"  末根补齐: {len(lag)}/{len(klines_data)} 只落后 —— 当日数据疑似"
                  f"尚未发布，跳过补拉")
            break
        print(f"  末根补齐第 {rnd + 1} 轮: {len(lag)} 只停在 {newest} 之前，补拉…")
        again = fetch_all_klines(lag, days, workers=workers)
        fixed = 0
        for c, bars in (again or {}).items():
            cur = klines_data.get(c) or []
            if bars and (not cur or bars[-1][0] > cur[-1][0]):
                klines_data[c] = bars
                fixed += 1
        print(f"  末根补齐第 {rnd + 1} 轮: 修好 {fixed} 只（腾讯数据仍未更新的保留原样）")
        if fixed == 0:
            break
    return klines_data


def fetch_all_klines(codes: List[str], days: int, workers: int = 1) -> Dict:
    """
    批量获取 K 线数据（最多两轮：首轮 + 失败重试，重试前整体停顿让 WAF 冷却）

    workers: 并发线程数。默认 1 = 原串行行为（前端包 1564 只×150 天，稳定优先）。
             后端包传 5~6：~700 只×750 根，串行实测顶穿 90 分钟 job 上限，
             并发后压到 1/5 时长；WAF 冷却状态已加锁，触发时所有线程一起让路。
             ★ 不建议 >8：腾讯是 IP 级限流，过高并发会直接撞 501 全局冷却。
    """
    result = {}
    total = len(codes)
    pending = list(codes)
    workers = max(1, int(workers or 1))

    for round_no in range(3):
        if not pending:
            break
        failed = []
        done_n = 0
        streak = 0  # 连续失败数（中途熔断用：零成功 + 连败过多 = 参数被拒/封禁）

        def _fail_fast():
            # ★ 中途熔断：前 MIDWAY_FAIL_FAST 只仍 0 成功 → 配置错误或 IP 被封。
            #   直接报错，避免像旧版那样 100% 失败还空耗到 90-150 分钟 job 上限。
            if streak >= MIDWAY_FAIL_FAST and not result:
                raise RuntimeError(
                    f"K线前 {done_n} 只全部失败（连续 {streak} 只）——大概率 count/区间参数"
                    f"被腾讯拒绝，或出口 IP 被限流封禁。已中止，避免空耗到 job 超时。")

        def _one(idx_code):
            idx, code = idx_code
            kl = fetch_kline(code, days)
            if workers > 1:
                _throttle_concurrent(idx)
            else:
                _throttle(idx)
            return code, kl

        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for code, kl in pool.map(_one, enumerate(pending)):
                    done_n += 1
                    if kl:
                        result[code] = kl
                        streak = 0
                    else:
                        failed.append(code)
                        streak += 1
                    _fail_fast()
                    if done_n % 50 == 0 or done_n == len(pending):
                        print(f"  K线: {len(result)}/{total} 成功 "
                              f"(第{round_no+1}轮 {done_n}/{len(pending)})", end="\r")
        else:
            for i, code in enumerate(pending):
                _, kl = _one((i, code))
                done_n += 1
                if kl:
                    result[code] = kl
                    streak = 0
                else:
                    failed.append(code)
                    streak += 1
                _fail_fast()
                if (done_n) % 50 == 0 or done_n == len(pending):
                    print(f"  K线: {len(result)}/{total} 成功 "
                          f"(第{round_no+1}轮 {done_n}/{len(pending)})", end="\r")

        print(f"\n  第{round_no + 1}轮完成: {len(result)}/{total} 成功，"
              f"{len(failed)} 只待重试")
        if round_no == 0 and failed:
            # 重试前停顿，让限流窗口恢复。★ 2026-09-17：本轮撞过 WAF 就按当前退避
            # 倍数给足 —— 否则 15s 的停顿在真封禁下等于没停，重试又立刻撞墙。
            with _waf_lock:
                mult, hit = _waf_backoff, _waf_events > 0
            wait_s = min(180, int(WAF_COOLDOWN * mult)) if hit \
                else (15 if workers > 1 else 8)
            print(f"  等待 {wait_s}s 后重试失败股票...")
            time.sleep(wait_s)
        pending = failed

    print(f"  K线完成: {len(result)}/{total} 成功")
    print(f"  {_waf_report()}")
    return result


def generate_packs(
    stocks_data: Dict,
    output_dir: str,
    date_str: str,
    prev_data: Optional[Dict] = None,
):
    """
    生成完整数据包和增量数据包
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 完整数据包（version 2：K 线从 60 天升级到 150 天，前端检测版本不匹配会强制重新下载）
    full_pack = {
        "version": 2,
        "date": date_str,
        "stocks": stocks_data,
    }
    
    full_path = os.path.join(output_dir, f"kline-pack-{date_str}.json.gz")
    with gzip.open(full_path, "wt", encoding="utf-8") as f:
        json.dump(full_pack, f, ensure_ascii=False, separators=(",", ":"))
    
    full_size = os.path.getsize(full_path) / 1024 / 1024
    print(f"  完整包: {full_path} ({full_size:.1f} MB)")
    
    # 更新 latest 链接/副本
    latest_path = os.path.join(output_dir, "kline-pack-latest.json.gz")
    if os.path.exists(latest_path):
        os.remove(latest_path)
    try:
        os.symlink(os.path.basename(full_path), latest_path)
    except OSError:
        # Windows 不支持 symlink 时，直接复制
        import shutil
        shutil.copy2(full_path, latest_path)
    
    # 增量数据包
    if prev_data:
        delta = compute_delta(prev_data, stocks_data)
        if delta:
            delta_pack = {
                "version": 2,
                "date": date_str,
                "stocks": delta,
            }
            delta_path = os.path.join(output_dir, f"kline-delta-{date_str}.json")
            with open(delta_path, "w", encoding="utf-8") as f:
                json.dump(delta_pack, f, ensure_ascii=False, separators=(",", ":"))
            
            delta_size = os.path.getsize(delta_path) / 1024
            print(f"  增量包: {delta_path} ({delta_size:.1f} KB)")
    
    # 清理旧文件（保留最近 7 天）
    cleanup_old_packs(output_dir, keep_days=7)


def compute_delta(prev: Dict, current: Dict) -> Dict:
    """
    计算增量数据：只包含新增或更新的股票
    """
    delta = {}
    for code, data in current.items():
        prev_data = prev.get(code)
        if not prev_data:
            # 新增股票
            delta[code] = data
        else:
            # 检查是否有更新（比较最后一天 K 线）
            prev_last = prev_data.get("klines", [[]])[-1] if prev_data.get("klines") else None
            curr_last = data.get("klines", [[]])[-1] if data.get("klines") else None
            if prev_last != curr_last:
                delta[code] = data
    return delta


def cleanup_old_packs(output_dir: str, keep_days: int = 7):
    """清理旧的数据包文件"""
    cutoff = datetime.now() - timedelta(days=keep_days)
    
    for filename in os.listdir(output_dir):
        if not filename.startswith("kline-"):
            continue
        filepath = os.path.join(output_dir, filename)
        if os.path.isfile(filepath):
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            if mtime < cutoff:
                os.remove(filepath)
                print(f"  清理: {filename}")


def load_previous_pack(output_dir: str) -> Optional[Dict]:
    """加载上一次的数据包（用于生成增量）"""
    latest_path = os.path.join(output_dir, "kline-pack-latest.json.gz")
    if not os.path.exists(latest_path):
        return None
    
    try:
        with gzip.open(latest_path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  加载旧数据包失败: {e}")
        return None


# ── 失败黑名单：连续拉取失败的代码持久化记录，达到阈值直接硬过滤 ──────────────
# 背景（2026-09-08）：池子里混着退市/被合并的历史代码（中国北车、美的电器…），
#   腾讯行情接口还留着僵尸快照（假市值），但 K 线接口永远返回空 → 每天固定
#   失败 27 只、白白消耗 3 轮重试。记录到 kline-failures.json，连续失败达到
#   阈值后从池中剔除；某天成功拉到则自动"洗白"移除（防误杀复牌股）。

FAILURES_FILENAME = "kline-failures.json"


def _failures_path(output_dir: str) -> str:
    return os.path.join(output_dir, FAILURES_FILENAME)


def _load_failures(output_dir: str) -> dict:
    p = _failures_path(output_dir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_failures(output_dir: str, fails: dict) -> None:
    os.makedirs(output_dir, exist_ok=True)
    with open(_failures_path(output_dir), "w", encoding="utf-8") as f:
        json.dump(fails, f, ensure_ascii=False, indent=1)


def _update_failures(output_dir: str, klines_data: dict, attempted: list,
                     quotes: dict) -> None:
    """本次最终失败的代码计数 +1；本次成功的代码洗白移除。"""
    fails = _load_failures(output_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    new_fails = []
    for code in attempted:
        if code in klines_data:
            fails.pop(code, None)                      # 成功 → 洗白
        else:
            rec = fails.setdefault(code, {"fail_count": 0, "first_failed": today})
            rec["fail_count"] = rec.get("fail_count", 0) + 1
            rec["last_failed"] = today
            rec["name"] = (quotes.get(code) or {}).get("name", "")
            new_fails.append(code)
    _save_failures(output_dir, fails)
    if new_fails:
        print(f"  失败记录更新: {len(new_fails)} 只 → {FAILURES_FILENAME}")


def main():
    parser = argparse.ArgumentParser(description="生成 K 线数据包")
    parser.add_argument("--output-dir", default="./data/kline", help="输出目录")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="K 线天数")
    parser.add_argument("--top", type=int, default=0, help="取市值前 N 只（0=不限制，包含全部）")
    parser.add_argument("--quotes-file", default="./data/kline/realtime-quotes.json",
                        help="实时行情落盘/复用路径（与后端包共享，避免全市场拉两遍）")
    parser.add_argument("--workers", type=int, default=1,
                        help="K 线拉取并发数（默认 1=串行，Actions 行为不变；本地可设 6+ 提速）")
    parser.add_argument("--fail-threshold", type=int, default=3,
                        help="连续失败达该次数的代码硬过滤（默认 3；退市/合并股约 1 天即中）")
    args = parser.parse_args()
    
    print(f"=== K 线数据包生成 ===")
    print(f"  输出目录: {args.output_dir}")
    print(f"  K 线天数: {args.days}")
    print(f"  取市值前: {args.top if args.top > 0 else '不限制'}")
    
    # 1. 生成代码池
    print("\n[1/4] 生成代码池...")
    all_codes = build_stock_pool()
    print(f"  共 {len(all_codes)} 个代码")
    
    # 2. 拉取实时行情（获取市值排序）
    #    ★ --quotes-file：拉取后落盘，同工作流的后端包步骤直接复用
    #      （此前前后端各拉一遍全市场 12000 只，白白翻倍且触发限流）
    quotes = None
    if args.quotes_file and os.path.exists(args.quotes_file):
        try:
            if time.time() - os.path.getmtime(args.quotes_file) < 6 * 3600:
                with open(args.quotes_file, encoding="utf-8") as f:
                    quotes = json.load(f)
                print(f"\n[2/4] 实时行情：复用共享文件（{len(quotes)} 只，0 请求）")
        except Exception as e:
            print(f"\n[2/4] 共享行情文件读取失败，重新拉取: {e}")
    if quotes is None:
        print("\n[2/4] 拉取实时行情...")
        quotes = fetch_realtime_batch(all_codes)
    if args.quotes_file and quotes:
        try:
            os.makedirs(os.path.dirname(args.quotes_file) or ".", exist_ok=True)
            with open(args.quotes_file, "w", encoding="utf-8") as f:
                json.dump(quotes, f, ensure_ascii=False)
            print(f"  实时行情已落盘: {args.quotes_file}（{len(quotes)} 只）")
        except Exception as e:
            print(f"  行情落盘失败（不影响本步骤）: {e}")
    
    # 按市值排序（可选取前 N 只，默认不限制）
    sorted_stocks = sorted(
        quotes.items(),
        key=lambda x: x[1].get("market_cap", 0),
        reverse=True,
    )
    if args.top > 0:
        sorted_stocks = sorted_stocks[:args.top]
    
    top_codes = [code for code, _ in sorted_stocks]

    # ★ 失败黑名单过滤：连续拉取失败达阈值的代码（退市/合并/僵尸）直接剔除
    _fails = _load_failures(args.output_dir)
    _black = [c for c in top_codes
              if (_fails.get(c) or {}).get("fail_count", 0) >= args.fail_threshold]
    if _black:
        print(f"  硬过滤 {len(_black)} 只连续失败≥{args.fail_threshold} 次的代码: "
              f"{', '.join(_black[:12])}{'…' if len(_black) > 12 else ''}")
        top_codes = [c for c in top_codes if c not in _black]
    print(f"  最终股票池: {len(top_codes)} 只")
    
    # 3. 加载上一次的数据包（用于生成增量）
    print("\n[3/4] 加载历史数据...")
    prev_data = load_previous_pack(args.output_dir)
    if prev_data:
        print(f"  找到历史数据: {prev_data.get('date', 'unknown')}")
    
    # 4. 拉取 K 线数据
    print("\n[4/4] 拉取 K 线数据...")
    date_str = datetime.now().strftime("%Y%m%d")

    # 防御：当日定时任务（16:00）已生成过完整包时，直接复用，避免盘中手动触发
    # 把完整包覆盖成不完整包（盘中拉取易被腾讯限流且当日K线未收盘）
    if (prev_data and prev_data.get("date") == date_str
            and prev_data.get("stocks")):
        print(f"  检测到今日 {date_str} 已生成完整包（{len(prev_data['stocks'])} 只），"
              f"直接复用，跳过拉取")
        stocks_data = prev_data["stocks"]
    else:
        klines_data = fetch_all_klines(top_codes, args.days, workers=args.workers)
        # ★ 末根补齐：18:00 生成时腾讯复权数据对近半股票还没补完（见函数说明），
        #   不补就会产出"半数股票少一天"的包 → 本地评分/详情图与后端不一致
        klines_data = refetch_stale_lastbar(klines_data, args.days,
                                            workers=args.workers)
        # ★ 失败记录：最终失败的计数+1（达阈值下次硬过滤），成功的洗白移除
        _update_failures(args.output_dir, klines_data, top_codes, quotes)

        # 组装最终数据
        stocks_data = {}
        for code in top_codes:
            if code in klines_data and code in quotes:
                stocks_data[code] = {
                    "name": quotes[code]["name"],
                    "market_cap": quotes[code]["market_cap"],
                    "klines": klines_data[code],
                }

    # 完整性校验：本次显著少于历史包时醒目警告（防止不完整包静默覆盖线上数据）
    if prev_data and prev_data.get("stocks") and stocks_data:
        prev_n = len(prev_data["stocks"])
        cur_n = len(stocks_data)
        if cur_n < prev_n * 0.8:
            print(f"\n  ⚠️ 警告: 本次仅 {cur_n} 只，历史包有 {prev_n} 只"
                  f"（{cur_n / prev_n:.0%}）")
            print("    > 可能被腾讯限流导致不完整。若在盘中运行，"
                  "请等 16:00 后定时任务重新生成完整包。")

    # 生成数据包
    generate_packs(stocks_data, args.output_dir, date_str, prev_data)
    
    print("\n=== 完成 ===")
    print(f"  成功: {len(stocks_data)} 只股票")


if __name__ == "__main__":
    main()
