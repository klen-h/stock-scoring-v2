# zzshare_client.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】zzshare 数据源接入封装（单点隔离）
说明：
- zzshare 仅作低频补充数据源（匿名限流/慢，内部自带超时重试）。
- token 通过环境变量 ZZSHARE_TOKEN 提供（可选，官网免费获取；缺省匿名）。
- 若 zzshare 库升级导致接口签名变化，只需改本模块，不影响上层。

实测（0.4.11，2026-09-03）：
✅ finance_latest/finance_stock 等 9 个财务接口可用
✅ uplimit_*/plates_*/ths_*/lhb_*/sentiment_*/trade_days 可用
❌ stock_moneyflow / rt_k / market_mf 此版本不存在（README 声明超前）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- （无项目内依赖）

## 被谁依赖

- `backend/app/eastmoney.py`
- `backend/app/mainforce/l3_history.py`
- `backend/app/mainforce/lhb.py`
- `backend/app/realtime_uplimit.py`
- `backend/app/routers/market.py`
- `backend/app/sector_zz.py`
- `backend/app/zzshare_daily.py`
- `backend/app/zzshare_finance.py`
- `scripts/backfill_zzshare_daily.py`
- `scripts/backtest_rescan_by_regime.py`
- `scripts/build_amount_curve.py`
- `scripts/lhb_backfill.py`
- `scripts/lhb_seats_backfill.py`
- `scripts/tail_settle_edge_check.py`
- `scripts/zzshare_probe.py`

## 关键阈值 / 环境变量

- `ZZSHARE_TOKEN`

## 顶层结构（def/class，前 25 个）

`get_api`, `to_zz_code`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：55 行。
