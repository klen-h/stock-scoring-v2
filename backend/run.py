"""
【文件作用】开发模式启动脚本

直接运行 `python run.py` 即可启动后端，效果等同于命令行执行：
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

参数说明：
  - "app.main:app" → 指向 app/main.py 文件里的 app 变量（即 FastAPI 实例）
  - host="0.0.0.0"  → 监听所有网卡（允许局域网/容器外访问；若只本机用可写 127.0.0.1）
  - port=8000       → 监听端口
  - reload=True     → 代码改动后自动重启（类似 nodemon），仅开发用，生产环境关闭

★ 2026-09-17 新增「启动前把关」_preflight：见其函数注释。目的只有一个 ——
  防止「本地包陈旧 → K线/指标/回测价格**静默回退 Supabase**」把免费档 egress 打爆
  （同型事故第 3 次：9/12 实测 596MB、9/17 再遇 ~600MB，记录见 app/pack_source.py:363-371）。
"""
import os
import sys

import uvicorn

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


def _preflight() -> None:
    """启动前校验数据包新鲜度；不满足则**拒绝启动**（fail-closed）。

    为什么需要：`.env` 里 DATA_SOURCE=local 时，一旦本地包陈旧，
    pack_source._is_stale() 为真 → get_klines() 返回 None（"宁缺毋旧"）→
    各调用方**静默回退**查 Supabase。而 K线/指标/backtest_prices 都是大表，
    免费档 egress 只有 167MB/天，本地开发一天能拉 600MB。
    run.py 的 reload=True 还会放大：每次改代码重启都重新触发一轮读取。

    ★ 刻意不做"近似判断"：直接调用 pack_source._is_stale()，与运行时**同一条**判据，
      否则会出现"自检通过、运行时仍回退"的假安全（曾经踩过：mtime<30h 判"新"
      而交易日历判"陈旧"，两个口径不一致）。
    """
    # .env 必须先于 import app.* 加载，否则读不到 DATA_SOURCE
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(BACKEND_DIR, ".env"))
    except Exception:
        pass

    src = (os.environ.get("DATA_SOURCE") or "db").strip().lower()
    if src not in ("local", "pack"):
        print(f"[preflight] DATA_SOURCE={src} → 直接读 Supabase，跳过数据包校验")
        return

    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)
    try:
        from app.pack_source import (_is_stale, _latest_available_pack_day,
                                    _pack_date_raw)
    except Exception as e:
        print(f"[preflight] 无法加载 pack_source（{e}）→ 跳过校验")
        return

    if not _is_stale():
        print(f"[preflight] 数据包 OK：pack_date={_pack_date_raw()} "
              f"(DATA_SOURCE={src}) → 不会回退 Supabase")
        return

    print("\n" + "=" * 74)
    print("[preflight] ★ 拒绝启动：本地数据包陈旧")
    print(f"  本地包 pack_date = {_pack_date_raw() or '(缺失)'}")
    print(f"  此刻应可用       = {_latest_available_pack_day()}")
    print("  继续启动会让 K线/指标/回测价格 **静默回退 Supabase** ——")
    print("  免费档 egress 167MB/天，历史实测本地开发一天可拉 ~600MB。")
    print("\n  修复（走 GitHub Pages，零 Supabase 流量）：")
    print("    python scripts/dev_local.py           # 推荐：预检+必要时同步+启动")
    print("    python scripts/sync_local.py --force  # 只同步，之后再跑本脚本")
    print("=" * 74 + "\n")
    sys.exit(1)


if __name__ == "__main__":
    _preflight()
    # __name__ == "__main__" 表示该文件被直接运行（而不是被 import）
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
