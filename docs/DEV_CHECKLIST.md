# 开发注意事项清单（DEV CHECKLIST）

> 交给执行方：这是「环境 + 依赖 + 命令 + 数据源限额 + 发布/回滚 + 常见坑」的一页清单。
> 与 `docs/PROJECT_GUARDRAILS.md`（红线）配合使用：**红线优先**。

## 1. 环境变量（按重要性）

### 1.1 LLM（`backend/app/flash/llm.py`）
| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 主力站（OpenAI 兼容 `/chat/completions`） |
| `LLM_MODEL_FALLBACK` | 主模型连续空响应时切换的兜底模型 |
| `LLM_FREE_BASE_URL` / `LLM_FREE_API_KEY` / `LLM_FREE_MODEL` / `LLM_FREE_MODEL_FALLBACK` | 免费站（可选，链式优先） |
| `LLM_DAILY_MAX_CALLS` | **线上日熔断**（批量实验不得占用） |
| `LLM_MIN_INTERVAL` | 调用最小间隔（默认 6s，防 429） |
| `LLM_TIMEOUT_FAST` / `LLM_TIMEOUT_SLOW` | 快/慢任务超时 |
| `LLM_MAX_TOKENS` / `LLM_MAX_TOKENS_CAP` | 单次输出上限 / 翻倍上限 |
| `LLM_PRICE_IN` / `LLM_PRICE_OUT` | 元/百万 token，仅用于费用估算展示 |

### 1.2 数据与推送
| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | Postgres（未配则退 SQLite，行为不同，别假设） |
| `FLASH_COOKIE` | 金十快讯 Cookie（失效 ⇒ 快讯 0 条，不是"没新闻"） |
| `ZZSHARE_TOKEN` | zzshare 日线/涨停生态（K 线增量、涨停清单） |
| `WECHAT_WEBHOOK` / `WECHAT_BUSINESS_ALERTS` | 企微推送与业务开关（0/1） |
| `RENDER_READ_ONLY` | 只读模式：重任务不启动（日批另跑） |
| `ENABLE_HEAVY_JOBS` | 进程内是否跑重计算（默认关，交给 Actions） |

### 1.3 业务阈值（观察器，全部 env 可调）
`SC_AMT_HIGH/SC_AMT_LOW`、`SC_RATIO_HIGH/LOW`、`SC_US10Y_LOW/HIGH`、`SC_BRENT_LOW/HIGH`、
`SC_SH_FIX/SC_SH_LAST/SC_SH_REDLINE/SC_SH_ANCHOR`、`SC_GEO_KINETIC`、
`PBW_CATCHDOWN_DAYS`、`FLASH_KEEP_DAYS`(默认 120)、`FLASH_KEEP_ROWS`、
`FLASH_BRIEF_DAILY_MAX`、`FLASH_BRIEF_COOLDOWN_MIN`、`FLASH_HISTORY_DAYS`。
> 改阈值必须写进文档并说明依据，否则下次回归无法解释。

## 2. 依赖与运行

```bash
# 后端（FastAPI）
pip install -r backend/requirements.txt
uvicorn app.main:app --reload            # 在 backend/ 下执行

# 前端（Vite + Vue3 + Tailwind）
cd frontend && npm install
npm run dev                              # 本地开发
npm run build                            # 发布前必须通过

# 日批（唯一计算入口）
python scripts/daily_batch.py --list     # 看任务表
python scripts/daily_batch.py --tasks <name>
python scripts/daily_batch.py --tasks all --force
```

## 3. 数据源与限额（写死在心里）

| 源 | 用途 | 注意 |
|---|---|---|
| 腾讯行情 | 指数/全市场报价 | 全量刷新 ~14s，**只能在调度器里刷，不能在接口里** |
| zzshare | 日线增量、涨停生态 | 需 `ZZSHARE_TOKEN`；日线未发布时脚本返回 2，工作流会重试 |
| 东财 | 板块/资金流 | 会 403/空返回，**必须允许降级**（zzshare/新浪兜底） |
| 金十 | 快讯 | Cookie 失效 ⇒ 0 条；`cluster` 已回填，但检索仍按 `content` 关键词更稳 |
| Supabase | 主库 | 500MB 治理：`db_retention` 任务；egress 是大头，接口避免全量大查询 |

## 4. 发布与回滚

- **前端**：push → Actions「Deploy Frontend to GitHub Pages」；`keep_files: true`。
  ⚠️ 不要本地跑 `publish_packs_local.py`（见 Guardrails #1）。
- **数据包**：GitHub Actions（kline → backend-pack → daily-batch 自动接棒）。
  本地补包：`python scripts/update_pack_incremental.py --pack-dir backend/data/kline --backfill`
  （包名/date 用「最近已完成交易日」，不是日历今天）。
- **后端**：Render 随 `main` push 自动重启；只读模式下重任务不跑，需确认日批/工作流状态。
- **回滚**：前端 `git revert` + push；后端同；数据包回滚需显式 `--allow-downgrade`。

## 5. 验证纪律（不验证等于没改）

```bash
# 后端
python -m py_compile backend/app/xxx.py backend/app/routers/yyy.py
python -c "import sys; sys.path.insert(0,'backend'); from app import xxx; print(xxx.run())"

# 前端
cd frontend && npm run build     # 必须 0 error
# 交互三点：展开/收起、空数据态、缓存命中态
```

- 涉及缓存的改动：必须同时验证「首次生成」与「命中缓存」两条路径。
- 涉及 LLM 的改动：必须验证「LLM 失败 → 降级模板」路径。
- 涉及日期/时区的改动：必须验证「盘中 / 盘后 / 休市」三种口径。

## 6. 常见坑（Top 12，全部真实踩过）

1. 模板引用未声明变量：build 通过、运行时 undefined（观察器清单展不开就是这个）。
2. `flash_news.cluster` 曾整列为空 ⇒ 按簇查命中 0 条（已修，历史已回填）。
3. 涨跌停用 `>=9.5` 一刀切：20cm 涨 10% 被当涨停（实测 150 家 vs 真实 82 家）。
4. 本地 `publish_packs_local.py --force` 覆盖线上新包（见 Guardrails #1）。
5. `upload-artifact` 不保留符号链接 ⇒ `kline-pack-latest` 永远是旧的（工作流已加实体化步骤）。
6. 盘前页面按需生成简报 ⇒ 落库"日期=今日、数据窗口=昨日"，日批命中缓存后**永不推送**。
7. `datetime.utcnow()` + 手加 8 小时：服务器时区差异会双加/少加，统一用 `rules.beijing_now()`。
8. `.get(k, default)` 对 `None` 值不生效 ⇒ 页面打出 "None%"。
9. 同源数据两处渲染必然漂移（教练卡/时间线/情绪数字都删过重复）。
10. 东财接口在非交易时段/被封时返回空，未降级会显示"0 个板块"。
11. `speed_5min` 采样不足返回 `None`，当 0 用会得出"没涨"的错误结论。
12. 日批任务失败被 `cached`/`try-except` 吞掉 ⇒ 表面全绿、实际没产出（必须显式 raise）。

## 7. 协作约定

- 一次提交只做一件事；提交信息写「做了什么 + 为什么 + 验证方式」。
- 改动跨前后端时，先合后端（接口先兼容），再合前端。
- 任何"临时脚本"用完即删（`_tmp_*.py`），不要留在仓库。
- 记忆文件 `.codebuddy/memory/` 记录「决策 + 事故 + 数据口径」，不记录临时搜索结果。
