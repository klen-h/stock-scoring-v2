# sector_zz.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】zzshare 板块数据源 —— **独立体系**的板块快照 / 分化度
【为什么独立成表，而不是并入 `sector_daily`（东财）】
★★ 两套板块 taxonomy **完全不兼容**（2026-09-27 实测）：
· 东财：`BK1325` 前缀，**496 个**细分行业（带 Ⅱ/Ⅲ 后缀），如「半导体材料」「非金属材料Ⅲ」
· zzshare：`881101` 前缀，**104 个**粗分行业（同花顺体系），如「种植业与林业」「煤炭开采加工」
⇒ 若混入同一张表，同一个板块会出现两个 key、历史序列直接断裂 ——
正是 `sector_industry.take_snapshot()` 当初**刻意跳过新浪**所避免的坑。
⇒ 故本模块用**独立表 `plate_daily_zz`**，与东财 `sector_daily` 并存，互不干扰。

【为什么不直接用东财（而是新增 zzshare 源）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash.rules`
- `app.zzshare_client`

## 被谁依赖

- `backend/app/data_gaps.py`
- `backend/app/flash/scheduler.py`
- `backend/app/routers/sector.py`
- `scripts/backfill_plate_zz.py`
- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_ensure_table`, `fetch_rank`, `take_snapshot`, `get_snapshot`, `get_history`, `latest_date`, `dispersion`, `stats`

## 已知坑

- zzshare 板块生态接口。

规模：193 行。
