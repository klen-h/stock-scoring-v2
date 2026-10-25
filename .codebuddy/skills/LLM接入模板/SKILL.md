# Skill：LLM 接入模板（结构化输出 + 降级 + 成本闸门）

> 目标：新增任何"AI 解读/简报/问答"功能时，一次做对成本、缓存、降级、验收，
> 不重蹈 `trader_brief` 早产缓存、`flash_brief` 指纹、盘中熔断误伤等覆辙。

## 触发条件

- 要新增/改动一个调用 LLM 的功能点（日报、简报、诊断、问答、解读卡…）。
- 要把已有单条 LLM 调用合并成批量调用。
- LLM 相关线上事故的复盘与修复。

## 红线（Guardrails #4 + 项目先例）

1. **默认不自动触发**：展开/点按钮才调用；轮询/定时触发必须显式设计并说明理由。
2. **一次刷新最多 1 次 LLM**；多条内容**批量打包**进同一个 prompt（样板 `flash_brief.py`：
   conclusion + news[] 一次返回）。
3. **按日缓存 + 数据指纹（sig）**：读数没变就不许重烧（`flash_brief` 表 + sig；`trader_brief`
   早产缓存事故：缓存 `candidates_date<今日` 必须视为过期）。
4. **日熔断**：线上业务受 `LLM_DAILY_MAX_CALLS` 保护；**批量实验/脚本绝不写 `llm_usage_daily`**
   （打满日熔断 ⇒ 盘中简报/复盘全挂）；批量记账走 `llm_batch_usage` 独立表。
5. **结构化输出**：JSON + 代码侧逐字段校验 + 失败降级模板；**禁止让前端解析自然语言**
   （后端 `app/insight.py::parse_insight` 是唯一解析入口；解析失败 = 原文照旧渲染）。
6. **不进决策链**：AI 解读不改评分、不进闸门、不给买卖/仓位指令式建议；越界词整段丢弃。
7. 输入最小化：只送聚合摘要，持仓外发必须已在 README「LLM 披露清单」登记。
8. 观点输出前端一律 `md-body`/`InsightBody.vue`，**绝不 `whitespace-pre-wrap` 裸文本**。

## 步骤

1. **成本闸门**：先写清 预计 token/次 × 预计次数/日 × 单价；配置既有环境变量
   （`LLM_TIMEOUT_FAST/SLOW`、`LLM_MAX_TOKENS`、日上限）；盘中级功能另设
   `FLASH_BRIEF_DAILY_MAX` 类独立日上限 + 冷却（`FLASH_BRIEF_COOLDOWN_MIN`）。
2. **Prompt 设计**：输入用 `<<< >>>` 分隔块（样板 `routers/stock.py` 事实问答）；
   检索证据用 `[n]` 编号引用（样板 `app/flash_history.py`，只许引用编号，无命中不得声称有依据）。
3. **结构化契约**：首行 `**结论**：<一句话>`（≤120 字，超长**丢弃不截断**）+
   JSON 字段（`conclusion/reasons[]/...`）；解析唯一入口 `app/insight.py`。
4. **缓存与幂等**：表按日主键 + `sig` 指纹（读数变了才过期）；`GET` 只读、
   `POST .../refresh` 才烧 LLM；`pushed_at` 补推去重（一天一次）。
5. **降级路径**：LLM 失败/超时/熔断 ⇒ 降级模板（纯规则/纯事实）；
   **降级路径也要缓存（短 TTL）**——"只缓存成功"等于没缓存（egress 实测 515 次整表读的教训）。
6. **触发器**（若盘中自动）：只挂 `scheduler`，交易时段判断用 `rules.is_trading_day()` +
   `get_market_status()`；循环守卫显式传 `latest_completed_trading_day()`；`RENDER_READ_ONLY`
   下 `_heavy` 循环不跑 = 重触发器必须迁日批。
7. **前端**：`InsightBody.vue`（结论条 + 依据折叠）或 `md-body`；卡片带 `as_of` 与"依据可追溯"。

## 验证方式

```bash
python -m py_compile backend/app/<file>.py
# 三条路径必须各验一次：
# 1) 首次生成（真烧一次 LLM，看 /api/system/llm-usage 计数 +1）
# 2) 命中缓存（同 sig 二次请求 0 调用）
# 3) LLM 失败 → 降级模板（拔 API key 或 mock 超时；输出仍是合法 JSON/模板，页面不崩）
# 4) 熔断路径：临时把 LLM_DAILY_MAX_CALLS 设 1 跑两次，确认第二次降级且不报 500
curl -s localhost:8000/api/system/llm-usage | head    # 用量与熔断状态
```

- 验收口径：三条路径 + 熔断路径全过；`llm_usage_daily` 增量 == 预期次数（不多记）；
  README「LLM 披露清单」已登记本功能点送出的内容。
