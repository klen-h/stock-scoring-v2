# 路由鉴权策略（AUTH POLICY）· 2026-10-24

> 依据：`docs/PROJECT_GUARDRAILS.md` **#12 鉴权默认化**。
> 本文件是**匿名白名单的唯一成文清单**——新增路由默认挂鉴权（`Depends(get_current_user)`），
> 要匿名必须：① 属于下述白名单类别；② 在本文件登记；③ 函数 docstring 写明理由。
> 清单由 `_tmp_auth_inventory.py` 式扫描生成（197 路由 = 挂鉴权 97 + 匿名 100，2026-10-24 时点）。

## 1. 禁止匿名的类别（已全部挂鉴权，共 97 条）

| 类别 | 例（本批新挂 10 条加 ★） |
|---|---|
| 写库 | user/plans×4、user/watchlist×3、user/portfolio×3、user/sync、paper 5、coach 2、contradictions 4、pattern watch×4、sector build×3/push/take×2、flash ingest、★policy-bottom/confirm、★snapshots/capture、★ranking-record |
| 烧 LLM | flash/brief/refresh、flash/review/{phase}/run、★score/ask |
| 重算/重缓存 | backtest/strategy、★kline-cache/refresh、★indicator-cache/refresh、★indicator-cache/incremental |
| 外部请求 | sector industry-map build×2/backfill、macro earnings-pressure?refresh=true、flash/calendar/refresh、★policy-bottom/announcements/refresh、★policy-bottom/etf-shares/refresh、★market/trigger-refresh（GET 形态触发全市场刷新） |
| 用户数据读 | user/* GET、auth/current、coach 列表类、position-sizing、exit-advice |

安全基建：`app/auth.py`（JWT；未配置 `JWT_SECRET` 时用**随机临时密钥**并在启动时告警——
不再回退机器名可预测密钥）、`get_current_user` / `get_current_user_optional` 依赖。

## 2. 匿名白名单（只读 + 廉价，100 条）

判定标准：**GET 只读 ∧ 读内存缓存/有 TTL 缓存的表 ∧ 无外部请求 ∧ 不写库 ∧ 不触发重算**。

- **auth.py**：`POST /register`、`POST /login`（认证入口本身必须匿名）。
- **外部机器回调**（共享密钥鉴权，无用户态 JWT；同 `POST /system/batch-done` 模式）：
  **POST /market/lidaxiao/push**（★ 2026-10-25：xiaopage GitHub Actions 在 LLM 输出解读的
  时刻回调；`X-Lidx-Token` = `LIDX_PUSH_TOKEN` 共享密钥，未配置 env 一律 403；落库幂等
  （entry_id 主键 upsert）⇒ 重放无害）。
- **行情/宏观读**（market、macro、capital、sector 全部 GET）：overview/temperature/emotion/
  realtime/uplimit-realtime/amount-realtime/limit-review/refresh-status/policy-bottom 三读、
  **GET /market/lidaxiao**（★ 2026-10-25：读 lidaxiao_views 小表前 15 行，无外呼无写库；
  60s 轮询响应 ~3KB）、**GET /macro/liquidity**（★ 2026-10-25：读 liquidity_daily 小表
  ≤120 行，无外呼无写库——实时链路另有 margin_sentiment 与 /macro/panel）、
  sector 18 个 GET、macro snapshot/daily/panel、capital 4 个 GET。
- **评分/榜单读**（scoring）：batch/top、{symbol}、diag、batch/* 各榜、kline-cache/status、
  weights、以及 **POST /ranking-persistence**（POST 形态的只读查询：连续上榜天数+可信度，
  无写库无 LLM）；⚠️ `POST /weight-advice` 暂留匿名（纯计算读侧，无外呼），若日后接 LLM 必须挂鉴权。
- **报告/日志读**：daily_report 3 GET、flash 11 个 GET（brief 读缓存、notifications、status…）、
  backtest /reports×2、contradictions /summary、/validation-stats、system 状态类。
- **模拟盘读**：paper /positions、/stats（数据非敏感；写侧已全挂）。
- **形态扫描读**：pattern /scan（读表；无落库兜底路径已被 `pattern.py:28` 审查项跟踪）。
- **个股读**（stock）：kline/realtime/search/technical/fundamental/finance*/news*/anomal*、
  **POST /finance/batch**（POST 形态只读：1 次 SQL + 30 分钟缓存，上限 1000 只）。
- **杂项读**：market/verification、score/{symbol} 行业映射、batch/industry-map、gate-watch、
  portfolio-radar。

## 3. 维护约定（新增/改动路由时）

1. 新路由**默认**加 `_user: dict = Depends(get_current_user)`（无消费时用下划线前缀）。
2. 要匿名 → 三件事：本文件登记 + docstring 写理由 + 确认满足 §2 判定标准。
3. 改动鉴权后跑一次匿名清单扫描（思路：grep 每个路由装饰器后 14 行是否含 `get_current_user`）
   并更新本文件；前端全站有登录守卫 + axios 全局 Bearer，正常用户不受影响。
4. 已知残余（不属本文件范围，跟踪于 `docs/BACKLOG.md`）：coach_alerts 行级属主
   （需 user_id 列迁移，BK-40 残留）、`GET /market/realtime` size 无上限（BK-31）。

## 4. 2026-10-24 本批新挂鉴权记录（commit afc3db93 + 本批）

paper×5、contradictions×4、sector×6、pattern×4、flash×5、backtest/strategy、
macro earnings-pressure(refresh)/reserves、user.py 全部（原有）、
★score/{ask, snapshots/capture, ranking-record, kline-cache/refresh,
indicator-cache/refresh, indicator-cache/incremental}、
★market/{trigger-refresh, policy-bottom/confirm, policy-bottom/announcements/refresh,
policy-bottom/etf-shares/refresh}
