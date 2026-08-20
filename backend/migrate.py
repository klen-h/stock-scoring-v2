"""
================================================================================
【文件作用】JSON 文件 → 数据库迁移脚本
================================================================================

将现有的 JSON 文件数据迁移到数据库。

使用方式：
  cd backend
  python migrate.py          # 执行迁移
  python migrate.py --dry    # 预览（不实际写入）

迁移的数据文件：
  - data/flash_state.json      → flash_state 表
  - data/flash.json            → flash_news 表
  - data/analyses.json         → flash_analyses 表
  - data/reviews.json          → flash_reviews 表
  - data/macro_history.json    → macro_history 表
  - data/etf_close.json        → etf_close 表
  - data/tracking.json         → etf_signals + etf_price_history 表
  - data/schedule_state.json   → schedule_state 表
  - data/strategies.json       → strategy_results + strategy_watch 表
================================================================================
"""

import json
import os
import sys
from datetime import datetime

# 确保能导入 app 模块
sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def load_json(filename: str, default=None):
    """加载 JSON 文件"""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        print(f"  [跳过] {filename} 不存在")
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [错误] {filename} 读取失败: {e}")
        return default


def migrate_flash_state(db, dry_run=False):
    """迁移快讯状态"""
    print("\n── 迁移 flash_state.json ──")
    data = load_json("flash_state.json", {"lastId": "", "pushedClusters": []})
    
    if dry_run:
        print(f"  [预览] 将写入 2 条状态记录")
        return
    
    db.execute("INSERT INTO flash_state (key, value) VALUES (%s, %s)",
               ("lastId", data.get("lastId", "")))
    db.execute("INSERT INTO flash_state (key, value) VALUES (%s, %s)",
               ("pushedClusters", json.dumps(data.get("pushedClusters", []))))
    print(f"  [完成] 写入 2 条状态记录")


def migrate_flash_news(db, dry_run=False):
    """迁移原始快讯"""
    print("\n── 迁移 flash.json ──")
    data = load_json("flash.json", {"items": []})
    items = data.get("items", [])
    
    if dry_run:
        print(f"  [预览] 将写入 {len(items)} 条快讯")
        return
    
    count = 0
    for item in items:
        try:
            db.execute(
                "INSERT INTO flash_news (id, content, time, cluster, is_pushed) "
                "VALUES (%s, %s, %s, %s, %s)",
                (item.get("id", ""), item.get("content", ""), item.get("time", ""),
                 item.get("cluster", ""), 1 if item.get("isPushed") else 0)
            )
            count += 1
        except Exception as e:
            print(f"  [跳过] id={item.get('id')}: {e}")
    print(f"  [完成] 写入 {count} 条快讯")


def migrate_analyses(db, dry_run=False):
    """迁移 LLM 诊断"""
    print("\n── 迁移 analyses.json ──")
    data = load_json("analyses.json", {"analyses": []})
    analyses = data.get("analyses", [])
    
    if dry_run:
        print(f"  [预览] 将写入 {len(analyses)} 条诊断记录")
        return
    
    count = 0
    for item in analyses:
        try:
            db.execute(
                "INSERT INTO flash_analyses (time, model, clusters_json, output_json) "
                "VALUES (%s, %s, %s, %s)",
                (item.get("time", ""), item.get("model", ""),
                 json.dumps(item.get("clusters", []), ensure_ascii=False),
                 json.dumps(item.get("output", {}), ensure_ascii=False))
            )
            count += 1
        except Exception as e:
            print(f"  [跳过] {e}")
    print(f"  [完成] 写入 {count} 条诊断记录")


def migrate_reviews(db, dry_run=False):
    """迁移复盘数据"""
    print("\n── 迁移 reviews.json ──")
    data = load_json("reviews.json", {})
    
    if dry_run:
        total = sum(len(v) for v in data.values() if isinstance(v, list))
        print(f"  [预览] 将写入 {total} 条复盘记录")
        return
    
    count = 0
    for phase, reviews in data.items():
        if not isinstance(reviews, list):
            continue
        for item in reviews:
            try:
                db.execute(
                    "INSERT INTO flash_reviews (phase, markdown, signals_json, time) "
                    "VALUES (%s, %s, %s, %s)",
                    (phase, item.get("markdown", ""),
                     json.dumps(item.get("signals", []), ensure_ascii=False),
                     item.get("time", ""))
                )
                count += 1
            except Exception as e:
                print(f"  [跳过] {e}")
    print(f"  [完成] 写入 {count} 条复盘记录")


def migrate_macro_history(db, dry_run=False):
    """迁移宏观历史"""
    print("\n── 迁移 macro_history.json ──")
    data = load_json("macro_history.json", [])
    
    if dry_run:
        print(f"  [预览] 将写入 {len(data)} 条宏观记录")
        return
    
    count = 0
    for item in data:
        try:
            db.execute(
                "INSERT INTO macro_history (time, data_json) VALUES (%s, %s)",
                (item.get("time", ""), json.dumps(item, ensure_ascii=False))
            )
            count += 1
        except Exception as e:
            print(f"  [跳过] {e}")
    print(f"  [完成] 写入 {count} 条宏观记录")


def migrate_etf_close(db, dry_run=False):
    """迁移 ETF 收盘数据"""
    print("\n── 迁移 etf_close.json ──")
    data = load_json("etf_close.json", [])
    
    if dry_run:
        print(f"  [预览] 将写入 {len(data)} 条 ETF 收盘记录")
        return
    
    count = 0
    for item in data:
        try:
            db.execute(
                "INSERT INTO etf_close (date, timestamp, holdings_json) "
                "VALUES (%s, %s, %s)",
                (item.get("date", ""), item.get("timestamp", ""),
                 json.dumps(item.get("holdings", []), ensure_ascii=False))
            )
            count += 1
        except Exception as e:
            print(f"  [跳过] {e}")
    print(f"  [完成] 写入 {count} 条 ETF 收盘记录")


def migrate_tracking(db, dry_run=False):
    """迁移信号跟踪数据"""
    print("\n── 迁移 tracking.json ──")
    data = load_json("tracking.json", {
        "activeSignals": [], "history": [], "priceHistory": {},
        "performance": {}
    })
    
    # 1. 保存完整状态到 tracking_state（tracker.py 运行时读取）
    if dry_run:
        print(f"  [预览] 将写入 tracking_state 1 条完整快照")
    else:
        try:
            db.upsert("tracking_state", {
                "id": 1,
                "data_json": json.dumps(data, ensure_ascii=False),
                "updated_at": datetime.now().isoformat()
            }, conflict_columns=["id"])
            print(f"  [完成] 写入 tracking_state 完整快照")
        except Exception as e:
            print(f"  [错误] tracking_state 写入失败: {e}")
    
    # 信号数据
    signals = data.get("activeSignals", []) + data.get("history", [])
    # 去重
    seen_ids = set()
    unique_signals = []
    for s in signals:
        sid = s.get("id")
        if sid and sid not in seen_ids:
            seen_ids.add(sid)
            unique_signals.append(s)
    
    if dry_run:
        print(f"  [预览] 将写入 {len(unique_signals)} 条信号记录")
    else:
        count = 0
        for s in unique_signals:
            try:
                db.execute(
                    "INSERT INTO etf_signals "
                    "(id, etf_name, direction, trend, support, resistance, status, "
                    "entry_condition_json, stop_loss, take_profit, entry_price, exit_price, "
                    "profit, is_win, source, reasoning, validation_json, tech_score, tech_grade, "
                    "position_json, entries_json, exits_json, expire_reason, last_checked_price, "
                    "created_at, closed_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (s.get("id", ""), s.get("etfName", ""), s.get("direction", ""),
                     s.get("trend", ""), s.get("support", ""), s.get("resistance", ""),
                     s.get("status", "waiting"),
                     json.dumps(s.get("entryCondition", {}), ensure_ascii=False),
                     s.get("stopLoss", ""), s.get("takeProfit", ""),
                     s.get("entryPrice"), s.get("exitPrice"),
                     s.get("profit"), 1 if s.get("isWin") else 0,
                     s.get("source", ""), s.get("reasoning", ""),
                     json.dumps(s.get("validation", {}), ensure_ascii=False),
                     s.get("techScore"), s.get("techGrade", ""),
                     json.dumps(s.get("positionSize", {}), ensure_ascii=False),
                     json.dumps(s.get("entries", []), ensure_ascii=False),
                     json.dumps(s.get("exits", []), ensure_ascii=False),
                     s.get("expireReason", ""), s.get("lastCheckedPrice"),
                     s.get("createdAt", ""), s.get("exitTime"))
                )
                count += 1
            except Exception as e:
                print(f"  [跳过信号] id={s.get('id')}: {e}")
        print(f"  [完成] 写入 {count} 条信号记录")
    
    # 价格历史
    price_history = data.get("priceHistory", {})
    total_prices = sum(len(v) for v in price_history.values())
    
    if dry_run:
        print(f"  [预览] 将写入 {total_prices} 条价格历史")
    else:
        count = 0
        for etf_name, prices in price_history.items():
            for p in prices:
                try:
                    db.execute(
                        "INSERT INTO etf_price_history (etf_name, date, price, timestamp) "
                        "VALUES (%s, %s, %s, %s)",
                        (etf_name, p.get("date", ""), p.get("price", 0), p.get("timestamp", ""))
                    )
                    count += 1
                except Exception as e:
                    pass
        print(f"  [完成] 写入 {count} 条价格历史")


def migrate_schedule_state(db, dry_run=False):
    """迁移调度状态"""
    print("\n── 迁移 schedule_state.json ──")
    data = load_json("schedule_state.json", {"done": {}})
    done = data.get("done", {})
    
    if dry_run:
        print(f"  [预览] 将写入 {len(done)} 条调度状态")
        return
    
    count = 0
    for task, date in done.items():
        try:
            db.upsert("schedule_state", {"task": task, "done_date": date}, conflict_columns=["task"])
            count += 1
        except Exception as e:
            print(f"  [跳过] {e}")
    print(f"  [完成] 写入 {count} 条调度状态")


def migrate_strategies(db, dry_run=False):
    """迁移战法数据"""
    print("\n── 迁移 strategies.json ──")
    data = load_json("strategies.json", {"scan_results": {}, "watch_pool": {}})
    
    # 扫描结果
    results = data.get("scan_results", {})
    if dry_run:
        print(f"  [预览] 将写入 {len(results)} 个战法的扫描结果")
    else:
        count = 0
        for name, info in results.items():
            try:
                db.execute(
                    "INSERT INTO strategy_results "
                    "(strategy_name, scan_date, count, results_json) VALUES (%s, %s, %s, %s)",
                    (name, info.get("date", ""), info.get("count", 0),
                     json.dumps(info.get("results", []), ensure_ascii=False))
                )
                count += 1
            except Exception as e:
                print(f"  [跳过] {name}: {e}")
        print(f"  [完成] 写入 {count} 个战法的扫描结果")
    
    # 观察池
    watch = data.get("watch_pool", {})
    if dry_run:
        total = sum(len(v.get("stocks", [])) for v in watch.values())
        print(f"  [预览] 将写入 {total} 条观察池记录")
    else:
        count = 0
        for name, info in watch.items():
            for stock in info.get("stocks", []):
                try:
                    db.execute(
                        "INSERT INTO strategy_watch "
                        "(strategy_name, code, name, entry_price, stop_loss, target_price, added_date) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (name, stock.get("code", ""), stock.get("name", ""),
                         stock.get("entry_price"), stock.get("stop_loss"),
                         stock.get("target_price"), stock.get("added_date", ""))
                    )
                    count += 1
                except Exception as e:
                    print(f"  [跳过] {stock.get('code')}: {e}")
        print(f"  [完成] 写入 {count} 条观察池记录")


def main():
    dry_run = "--dry" in sys.argv
    
    print("=" * 60)
    print("  数据迁移工具：JSON → 数据库")
    print("=" * 60)
    
    if dry_run:
        print("\n[预览模式] 不会实际写入数据\n")
    else:
        print("\n[迁移模式] 将写入数据库\n")
    
    # 导入数据库模块
    from app.database import db
    
    # 初始化表
    if not dry_run:
        db.init_tables()
    
    # 执行迁移
    migrate_flash_state(db, dry_run)
    migrate_flash_news(db, dry_run)
    migrate_analyses(db, dry_run)
    migrate_reviews(db, dry_run)
    migrate_macro_history(db, dry_run)
    migrate_etf_close(db, dry_run)
    migrate_tracking(db, dry_run)
    migrate_schedule_state(db, dry_run)
    migrate_strategies(db, dry_run)
    
    print("\n" + "=" * 60)
    if dry_run:
        print("  预览完成，未写入任何数据")
    else:
        print("  迁移完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()


def auto_migrate_if_needed():
    """
    启动时自动检查并迁移（仅当数据库为空且 JSON 文件存在时执行）。
    由 main.py 的 lifespan 调用。
    """
    from app.database import db
    
    # 检查数据库是否已有数据
    try:
        news_count = db.fetch_one("SELECT COUNT(*) as cnt FROM flash_news")
        if news_count and news_count.get("cnt", 0) > 0:
            return  # 数据库已有数据，无需迁移
    except Exception:
        pass
    
    # 检查 JSON 文件是否存在
    tracking_path = os.path.join(DATA_DIR, "tracking.json")
    flash_path = os.path.join(DATA_DIR, "flash.json")
    if not os.path.exists(tracking_path) and not os.path.exists(flash_path):
        return  # 没有 JSON 文件，无需迁移
    
    print("[migrate] 检测到数据库为空且 JSON 文件存在，执行自动迁移...")
    db.init_tables()
    migrate_flash_state(db)
    migrate_flash_news(db)
    migrate_analyses(db)
    migrate_reviews(db)
    migrate_macro_history(db)
    migrate_etf_close(db)
    migrate_tracking(db)
    migrate_schedule_state(db)
    migrate_strategies(db)
    print("[migrate] 自动迁移完成")
