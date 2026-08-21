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
  "version": 1,
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
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

# ── 配置 ──
DEFAULT_DAYS = 60       # K 线天数
DEFAULT_TOP = 3000      # 取市值前 N 只
BATCH_SIZE = 50         # 批量请求行情每批数量
KLINE_BATCH_SIZE = 10   # K 线请求并发数（避免触发 WAF）
REQUEST_TIMEOUT = 10    # 请求超时（秒）
WAF_COOLDOWN = 5        # WAF 触发后冷却（秒）

# A 股代码池（与 backend/app/tencent.py 保持一致）
DISABLED_PREFIXES = {"688", "300", "301"}

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0"})


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
                    market_cap = float(fields[45]) if len(fields) > 45 and fields[45] else 0
                    pe = float(fields[39]) if len(fields) > 39 and fields[39] else 0
                    pb = float(fields[46]) if len(fields) > 46 and fields[46] else 0
                    turnover_rate = float(fields[38]) if len(fields) > 38 and fields[38] else 0
                    change_pct = float(fields[32]) if len(fields) > 32 and fields[32] else 0
                    
                    if price > 0 and name and not name.startswith("ST"):
                        result[code] = {
                            "name": name,
                            "price": price,
                            "market_cap": market_cap,
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
    获取单只股票 K 线数据
    返回: [[date, open, high, low, close, volume], ...] 或 None
    """
    prefix = "sh" if code.startswith("6") else "sz"
    symbol = f"{prefix}{code}"
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y-%m-%d")  # 多取一些，确保够用
    
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {
            "param": f"{symbol},day,{start_date},{end_date},{days * 2},qfq",
        }
        resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        
        klines_raw = data.get("data", {}).get(symbol, {})
        day_data = klines_raw.get("day") or klines_raw.get("qfqday") or []
        
        if not day_data:
            return None
        
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
        
        return result if len(result) >= 30 else None
        
    except Exception as e:
        return None


def fetch_all_klines(codes: List[str], days: int) -> Dict:
    """
    批量获取 K 线数据
    """
    result = {}
    total = len(codes)
    
    for i, code in enumerate(codes):
        klines = fetch_kline(code, days)
        if klines:
            result[code] = klines
        
        # 进度显示
        if (i + 1) % 50 == 0 or i == total - 1:
            print(f"  K线: {i+1}/{total} ({len(result)} 成功)", end="\r")
        
        # WAF 保护
        if (i + 1) % KLINE_BATCH_SIZE == 0:
            time.sleep(0.3)
    
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
    
    # 完整数据包
    full_pack = {
        "version": 1,
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
                "version": 1,
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
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="取市值前 N 只")
    args = parser.parse_args()
    
    print(f"=== K 线数据包生成 ===")
    print(f"  输出目录: {args.output_dir}")
    print(f"  K 线天数: {args.days}")
    print(f"  取市值前: {args.top}")
    
    # 1. 生成代码池
    print("\n[1/4] 生成代码池...")
    all_codes = build_stock_pool()
    print(f"  共 {len(all_codes)} 个代码")
    
    # 2. 拉取实时行情（获取市值排序）
    print("\n[2/4] 拉取实时行情...")
    quotes = fetch_realtime_batch(all_codes)
    
    # 按市值排序，取前 N 只
    sorted_stocks = sorted(
        quotes.items(),
        key=lambda x: x[1].get("market_cap", 0),
        reverse=True,
    )[:args.top]
    
    top_codes = [code for code, _ in sorted_stocks]
    print(f"  取市值前 {len(top_codes)} 只")
    
    # 3. 加载上一次的数据包（用于生成增量）
    print("\n[3/4] 加载历史数据...")
    prev_data = load_previous_pack(args.output_dir)
    if prev_data:
        print(f"  找到历史数据: {prev_data.get('date', 'unknown')}")
    
    # 4. 拉取 K 线数据
    print("\n[4/4] 拉取 K 线数据...")
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
    
    # 生成数据包
    date_str = datetime.now().strftime("%Y%m%d")
    generate_packs(stocks_data, args.output_dir, date_str, prev_data)
    
    print("\n=== 完成 ===")
    print(f"  成功: {len(stocks_data)} 只股票")


if __name__ == "__main__":
    main()
