# Skill：数据包发布与回滚

> 背景（2026-10-24 事故）：`scripts/publish_packs_local.py` 是 **orphan 重建**（force push 整条
> gh-pages 只含本地文件），本地包旧 ⇒ 把线上新包整批删掉（实测把工作流刚发的 10-23 K 线包
> 回滚成 10-22）。本 skill 固化正确姿势与护栏。

## 触发条件

- 需要发布/更新 前端 K 线包 / backend-pack / indicators 包。
- 数据包疑似回滚/陈旧，需要核对与补救。
- 前端页面改动需要部署。

## 铁律（先读，违反 = 事故重演）

1. **禁止本地跑 `scripts/publish_packs_local.py` 发布"只是本地有旧包"的场景**。
   前端/代码改动 ⇒ **commit + push**，由 GitHub Actions 部署（`keep_files: true` 不会动 `data/`）。
   只有本地确实生成了**更新**的包时才允许用该脚本（护栏：本地日期 < 线上 ⇒ 拒绝；
   `--force` 绕不过，需 `--allow-downgrade`——出现这两个参数的时机必须人工确认）。
2. 包名/`date` 用**最近已完成交易日**（`rules.latest_completed_trading_day()`，15:00 分界），
   不是日历今天；增量判据用**最后一根 bar 的日期**，不能用 `pack_date`（那是生成日）。
3. gh-pages 三个写入者必须同一 `concurrency.group` + `cancel-in-progress:false` + `keep_files:true`。
4. `upload-artifact` 不保留符号链接 ⇒ `kline-pack-latest` 永远是旧的 ⇒ 工作流先 `cp` 实体化。
5. zzshare 只用于**追加新交易日原始 bar**（复权口径与腾讯不同，不能整包替换）；
   除权股回退腾讯；ETF/指数 zzshare 无 ⇒ 用腾讯批量行情 + 新浪历史补缺口。

## 步骤

### A. 日常发布（本地补包场景）

```bash
# 1) 增量补 K 线包（重叠校验 + factor 变化检测 + 除权回退腾讯）
python scripts/update_pack_incremental.py --pack-dir backend/data/kline --backfill
# 2) 增量补后端包（重算 indicators/trend，mainforce/news 继承上一份）
python scripts/update_backend_pack_incremental.py
# 3) 确认包日期 = 最近已完成交易日（不是今天！）
ls -la backend/data/kline | tail
# 4) 此时才允许 publish（本地确有新包；护栏会拒绝 downgrade）
python scripts/publish_packs_local.py
```

### B. 代码/前端改动

```bash
git push origin main        # Actions 自动部署；Render 随 main 自动重启
# 排查第一步：curl -s https://<render>/api/health | jq .build   # vs git rev-parse HEAD
```

### C. 回滚

- 前端代码：`git revert <commit>` + push（走 Actions，**不要**本地重建 dist）。
- 数据包：`--allow-downgrade` 只在**明确确认要回滚**时使用；先核对线上/本地包日期
  （Pages 上 `data/kline/kline-pack-<YYYYMMDD>.json.gz` 的 `date` 字段）。
- 回滚后必须写一段「根因 + 复现条件 + 为什么这样修」进 MEMORY（Guardrails #9）。

## 验证方式

```bash
# 发布后 15 分钟内：
curl -s https://<pages>/data/kline/kline-pack-latest.json | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('date'), len(d.get('stocks', d.get('klines', {}))))"
# 前端工作台打开 → 详情页 K 线最后一根日期 == 包 date
# egress 观察：python scripts/egress_probe.py snapshot --tag pack-<date>
```

## 红线

- 禁止删除 gh-pages 上任何包文件（只追加）；`cleanup` 类操作需要人工确认清单。
- 禁止把"本地旧包"发布上线（宁可少发、不可回滚）。
- 改动未 push 时，Actions/Render 仍跑旧代码——"本地已修"≠"线上已修"，验证必须对准线上 build。
