# -*- coding: utf-8 -*-
"""重跑战法扫描：清除当日完成标记（此前用陈旧 K 线扫过），用最新交易日数据重扫并推送企微。"""
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app.database import db
db.execute("DELETE FROM schedule_state WHERE task = %s", ("strategy_scan",))
print("[rescan] 已清除 strategy_scan 当日完成标记", flush=True)

from app.flash.scheduler import scan_all_strategies, _latest_trading_day
print(f"[rescan] 期望数据日期: {_latest_trading_day()}", flush=True)
print(f"[rescan] 开始: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

stats = scan_all_strategies()
print(f"[rescan] 结束: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
print(f"[rescan] 结果: {stats}", flush=True)
