# 数据包迁移计划（backend-pack / Render 瘦身 / 前端本地化）

生成：2026-09-06 ｜ **死线：2026-10-03**（Supabase 组织超额 → 项目受限）

---

## 一、背景与三个问题

1. **Supabase egress 超额**：本周期 10.6GB / 免费 5GB（212%），大头是
   backtest_prices(71MB) / kline_cache(19MB) / indicator_cache(9MB) 被反复读；
2. **Render 免费实例被详情页打挂**：0.1CPU/512MB 上，`/api/score/000567` 等
   并发重请求 → 502/503 → 浏览器误报 CORS（已复现实锤，9/5）；
3. **JSON 数据包是内存炸弹**：45MB JSON → Python dict 常驻 150-200MB，
   512MB 实例直接 OOM（已实测）。

根因共识：**8/25-9/5 功能浪潮让每个新功能都成为「每日读库的永久订阅」**，只叠加不替换。

---

## 二、目标架构（终态）

- **GitHub Actions**（每日 16:00，重活全在这里）产出三个包，全部发 GitHub Pages（免费）：
  - `backend-pack.db.gz` —— Render 启动下载，sqlite3 按需查（常驻约 0）
  - `indicators-pack.json.gz` —— 浏览器 Worker 读 _series 评分（500 天口径）
  - `kline-pack-latest.json.gz` —— 浏览器图表/本地回测（现状不变，已支持增量）
- **Supabase**（瘦身后）：只剩业务小表 <10MB，egress 目标 <0.5GB/周期
- **Render**（瘦身后）：只剩快讯监控、模拟盘撮合、实时行情、health
  —— 不再算分、不再读肥表，OOM 消失
- **浏览器**（两个用户）：本地评分/详情/回测展示，零后端重请求

---

## 三、Phase 0：已完成（2026-09-05/06）

- [x] generate_backend_pack.py：SQLite 打包（klines/indicators/codes/meta 四表）
      + indicators-pack.json.gz（前端指标小包）+ 双完整性护栏（exit 5/6）
- [x] pack_source.py：DATA_SOURCE 三档（db/pack/local）读取层，
      SQLite 按需查询（常驻约 0）+ 30h 新鲜度 + 陈旧护栏（_is_stale）+ 缺包优雅回退
- [x] 读取层接线 4 处：kline_cache / indicator_cache / backtest/data / mainforce/state
      （全部「pack 未命中回退 DB」，接口签名不变）
- [x] main.py lifespan：启动后台预下载（非阻塞，失败降级 DB）
- [x] overlay.py：主力筹码计算加 260 根窗口（CPU 护栏，解详情页 OOM 的 CPU 层）
- [x] refresh_kline_cache：两轮重试 + 失败退避 + 成功率告警（方案 A）
- [x] CI Python 3.9 兼容修复（PEP 604 注解，7 文件）
- [x] BACKTEST_PREHEAT_WINDOW 死循环笔误修复 + ENABLE_HEAVY_JOBS 开关

---

## 四、Phase 1：上线验证（本周，P0）

> 目标：Render 安全切到 pack 模式，详情页不再 502。

- [ ] push 全部改动，确认 ci.yml（Python 3.9）通过
- [ ] 确认 Secrets：DATABASE_URL（重置后的新密码）+ GH_PAGES_TOKEN
- [ ] Actions 手动触发 kline-data 工作流，验收三件事：
  - [ ] 日志出现「SQLite: ... indicators=1394」（修复验证）
  - [ ] 日志出现「bars=... 约750」（深度验证）
  - [ ] Pages 上 backend-pack.db.gz Last-Modified 为最新
- [ ] Render 环境变量：DATA_SOURCE=pack + ENABLE_HEAVY_JOBS=0 → Restart
  - [ ] 日志出现「[main] 后端数据包就绪: 日期，N 只」
  - [ ] 详情页连开 5 次，无 502（对照 9/5 复现基线）
- [ ] 本地：python scripts/sync_local.py + .env 加 DATA_SOURCE=local
- [ ] 回测口径验证：同一只股票，pack 价格 vs DB backtest_prices
      收盘价对比（qfq vs 东财，除权日允许差异，其余应一致）

**验收标准**：Render Logs 无 OOM/502；Supabase 日 egress <100MB。
**回滚**：删 DATA_SOURCE 变量 → 回 DB 模式（1 分钟）。

---

## 五、Phase 2：前端消费 pack（下周，P1）

> 目标：两个用户切「本地计算」后，Render 的重接口调用频次大降。

- [ ] IndexedDB 新增 indicators store；下载 indicators-pack.json.gz
- [ ] Worker（indicatorWorker.js）优先读 _series（500 天口径），
      缺失回退 150 根现算（现状兜底）
- [ ] 个股详情页 K 线改读 IndexedDB（消除 /api/stock/kline 并发爆发——OOM 触发点）
- [ ] 评分口径对齐验证：同一只股票，前端分数 vs 后端分数差 <1 分
      （pack 指标成为两端的统一事实源）

**验收标准**：本地模式下，详情页/排行榜零后端重接口调用。

---

## 六、Phase 3：日批迁 Actions（10/3 前，P1）

> 目标：数据积累不断档，且不完全依赖 Render 存活。

- [ ] 评分快照迁 Actions：Actions 拉腾讯行情 + 用 pack 指标算分 →
      写 ranking_history（写入免费）；Render 侧保留为兜底（pack 模式下跳过）
- [ ] 每日日报迁 Actions：LLM_API_KEY 入 Secrets；读 pack/小表生成日报
- [ ] mainforce_state 评估：已走 pack 数据源，Render 保留（轻）
- [ ] 战法扫描：主数据走腾讯直连，Render 保留（中负载，观察）

**验收标准**：Actions 失败有企微告警；连续失败时 Render 兜底任务自动顶上。

---

## 七、Phase 4：观察与收尾（10/3 后一周）

- [ ] Supabase egress 曲线 <5GB（目标 <1GB）→ 确认限制解除
- [ ] Render Metrics：Memory 曲线平稳、无 restart
- [ ] 清理：JSON 版 backend-pack 逻辑删除、indicator-refresh.yml
      （pack 稳定后其 DB 写入不再被消费）
- [ ] Supabase 密码重置收尾：确认 Render / 本地 / GitHub Secrets 三处已同步新密码
- [ ] 手册（Agent知识库 v3.1）补记：第八章遗留项状态更新

---

## 八、风险与对策

| 风险 | 概率 | 对策 |
|---|---|---|
| qfq（腾讯）vs 东财口径漂移，回测结果变化 | 中 | Phase 1 对比验证；差异大则 backtest 读取保留 DB 源 |
| Actions 失败 → pack 陈旧 → 评分用旧指标 | 中 | _is_stale 护栏已回退 DB；失败推企微；快照门 18:45 强制执行 |
| Render 512MB 仍然 OOM | 低 | Phase 2 完成后只剩轻负载；仍崩则升级 Render starter（$7/月） |
| GitHub Actions cron 延迟/丢失 | 高（已知特性） | 双 cron + 幂等 + _is_stale 回退 + 手动触发兜底 |
| Supabase 10/3 照常限制 | 中 | Phase 1 完成即脱离肥表依赖；真被限制也只是小表，可快速迁新项目 |

---

## 九、明确不做（本期）

- **增量 delta 包**（后端）：Pages 流量免费、全量 12-20MB 可接受；
  触发条件：本地同步频繁且 74 秒不可接受，或包涨到 50MB+
- **模拟盘 / 快讯监控前端化**：有状态 + 实时，必须留常驻服务
- **后端彻底轻量化重构**：等 Phase 1-3 稳定后自然水到渠成

---

## 十、当前待办（给下次会话/明天开盘前）

- [ ] push（含 3.9 兼容修复 + SQLite 版脚本）
- [ ] 配 DATABASE_URL secret（若未配）
- [ ] 手动跑 kline-data 工作流 → 验证新包（indicators ≈1394、bars ≈750/只）
- [ ] 验证通过后：Render 切 DATA_SOURCE=pack
- [ ] 周一开盘观察：详情页是否还 502、Render 日志有无 OOM
- [ ] Supabase 密码重置 + 三处同步（若尚未完成）
