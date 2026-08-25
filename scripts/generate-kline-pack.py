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
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

# ── 配置 ──
DEFAULT_DAYS = 150      # K 线天数（需足够长让 EMA26/DEA 系列指标收敛，与后端 500 天历史对齐）
BATCH_SIZE = 50         # 批量请求行情每批数量
KLINE_BATCH_SIZE = 10   # K 线请求每批数量（仅用于节流节奏，仍是串行请求）
REQUEST_TIMEOUT = 10    # 请求超时（秒）
WAF_COOLDOWN = 120      # WAF 触发后全局冷却（秒）——与后端 tencent.py 保持一致
KLINE_RETRIES = 3       # 单只股票 K 线请求重试次数（含首次）
RETRY_BACKOFF = [1, 3, 6]  # 重试退避（秒）
CONSECUTIVE_COOLDOWN = 10  # 连续失败达到该次数后暂停（秒级退避）

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

# ── WAF 全局限流状态（与后端 tencent.py 同策略）──
# 腾讯 WAF 触发后（HTTP 501）需要暂停所有 K 线请求，避免被持续封禁。
_waf_blocked_until = 0.0
_consecutive_failures = 0


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
    global _waf_blocked_until, _consecutive_failures

    # WAF 全局冷却中：直接跳过（避免继续撞墙浪费请求）
    if time.time() < _waf_blocked_until:
        return None

    prefix = "sh" if code.startswith("6") else "sz"
    symbol = f"{prefix}{code}"

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y-%m-%d")  # 多取一些，确保够用

    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        "param": f"{symbol},day,{start_date},{end_date},{days * 2},qfq",
    }

    last_err = None
    for attempt in range(KLINE_RETRIES):
        # 冷却中则中止本轮重试
        if time.time() < _waf_blocked_until:
            return None
        try:
            resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)

            # WAF 检测：腾讯返回 501 表示被防火墙拦截 → 全局冷却
            if resp.status_code == 501:
                _waf_blocked_until = time.time() + WAF_COOLDOWN
                _consecutive_failures = 0
                print(f"\n  [WAF] K线请求被拦截 {symbol}，全局冷却 {WAF_COOLDOWN}s")
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
                _consecutive_failures = 0
                return result
            last_err = f"K线不足({len(result)}根)"
            time.sleep(RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else 6)

        except Exception as e:
            last_err = str(e)
            _consecutive_failures += 1
            # 连续失败 → 退避冷却（避免触发更严格的封禁）
            if _consecutive_failures >= CONSECUTIVE_COOLDOWN:
                wait = min(60, _consecutive_failures * 5)
                print(f"\n  连续失败 {_consecutive_failures} 次，暂停 {wait}s")
                time.sleep(wait)
                _consecutive_failures = 0
            elif attempt < len(RETRY_BACKOFF):
                time.sleep(RETRY_BACKOFF[attempt])

    return None


def _throttle(index: int) -> None:
    """节流：随机间隔避免固定节奏被识别为爬虫；每 50 只额外停顿让 WAF 松弛。"""
    time.sleep(0.3 + random.random() * 0.5)
    if index % 50 == 0 and index > 0:
        time.sleep(1.5)


def fetch_all_klines(codes: List[str], days: int) -> Dict:
    """
    批量获取 K 线数据（最多两轮：首轮 + 失败重试，重试前整体停顿让 WAF 冷却）
    """
    result = {}
    total = len(codes)
    pending = list(codes)

    for round_no in range(2):
        if not pending:
            break
        failed = []
        for i, code in enumerate(pending):
            klines = fetch_kline(code, days)
            if klines:
                result[code] = klines
            else:
                failed.append(code)

            # 进度显示（WAF 冷却中也会快速跳过，计数不撒谎）
            if (i + 1) % 50 == 0 or i == len(pending) - 1:
                print(f"  K线: {len(result)}/{total} 成功 (第{round_no+1}轮 {i+1}/{len(pending)})", end="\r")

            _throttle(i)

        print(f"\n  第{round_no + 1}轮完成: {len(result)}/{total} 成功，"
              f"{len(failed)} 只待重试")
        if round_no == 0 and failed:
            # 重试前停顿，让限流窗口恢复
            print(f"  等待 8s 后重试失败股票...")
            time.sleep(8)
        pending = failed

    print(f"  K线完成: {len(result)}/{total} 成功")
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


def main():
    parser = argparse.ArgumentParser(description="生成 K 线数据包")
    parser.add_argument("--output-dir", default="./data/kline", help="输出目录")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="K 线天数")
    parser.add_argument("--top", type=int, default=0, help="取市值前 N 只（0=不限制，包含全部）")
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
    print("\n[2/4] 拉取实时行情...")
    quotes = fetch_realtime_batch(all_codes)
    
    # 按市值排序（可选取前 N 只，默认不限制）
    sorted_stocks = sorted(
        quotes.items(),
        key=lambda x: x[1].get("market_cap", 0),
        reverse=True,
    )
    if args.top > 0:
        sorted_stocks = sorted_stocks[:args.top]
    
    top_codes = [code for code, _ in sorted_stocks]
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
        klines_data = fetch_all_klines(top_codes, args.days)

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
