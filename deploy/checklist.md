# 阿里云 ECS 迁移清单（2核2G · Supabase → 同机 Postgres · Render → Docker）

> 配套文件：`deploy/docker-compose.prod.yml` / `deploy/env.template` /
> `deploy/Caddyfile` / `deploy/migrate_supabase.sh` / `deploy/backup_db.sh`
> 前提：域名已购 + ICP 备案提交（1-2 周窗口，正好用来跑步骤 1-6）

---

## 阶段〇：服务器初始化（30 分钟）

- [ ] 购买 ECS：2核2G，**Ubuntu 22.04 LTS 64位** 系统盘 40G，带宽按量或 3M 固定
- [ ] 安全组放行：22（SSH，限自己 IP）、80、443；**5432 永不开放公网**
- [ ] 安装 Docker + compose 插件：
      `curl -fsSL https://get.docker.com | bash && sudo usermod -aG docker $USER`
- [ ] 时区核对：`timedatectl`（阿里云默认 CST ✓，调度器全部按北京时间触发）
- [ ] clone 项目：`git clone git@github.com:klen-h/stock-scoring-v2.git`

## 阶段一：先跑通后端（数据库过渡期用 Supabase，1 小时）

- [ ] `cp deploy/env.template backend/.env` 并填写（此阶段 DATABASE_URL 仍填 Supabase 串）
- [ ] `docker compose -f deploy/docker-compose.prod.yml --env-file deploy/env.template up -d backend`
- [ ] 验证：`curl localhost:8000/api/health` 200；调度器日志 25 循环启动；企微测试推送到达
- [ ] **把 Render 下线**（不再有 0.1CPU/512MB 的 OOM/502）——Supabase egress 立降
      （后端日常只剩业务小表读写；肥表读取在本地模式由前端承担、在服务端由 pack 承担）

## 阶段二：备案窗口期（1-2 周，跑步骤的同时等结果）

- [ ] 域名购买 + 实名 + **ICP 备案提交**（阿里云控制台一键提交管局）
- [ ] 备案期间无域名也可用：手机/电脑浏览器直接访问 `http://IP:8000/docs` 临时自查；
      排行榜用**本地模式**（零后端调用）不受影响

## 阶段三：数据库迁入 + HTTPS 上线（备案通过后，半天）

- [ ] `SUPABASE_DUMP_URL="直连串" bash deploy/migrate_supabase.sh`
- [ ] 核对脚本输出的行数表与 Supabase 控制台一致
- [ ] `backend/.env` 切 `DATABASE_URL=postgresql://stockapp:...@postgres:5432/stockapp`
      → `docker compose restart backend` → `/api/health` + 登录验证
- [ ] Caddyfile 填真实域名 → `docker compose up -d caddy` → 证书自动签发
- [ ] 前端 API 地址切到 `https://api.域名`（GitHub Pages 后台配 VITE 变量重新构建，或
      直接用现有构建的相对路径约定核对）→ 浏览器验证登录/排行/详情
- [ ] Supabase 原项目保留只读观察 1 周 → 归档

## 阶段四：收尾与守护

- [ ] crontab 加数据库每日备份：`deploy/backup_db.sh`（脚本内含保留 14 份轮转），
      并用 `scp`/OSS 把备份拷出本机一份（防单机故障）
- [ ] `ENABLE_HEAVY_JOBS=1`（指标刷新/回测预热迁回本机执行——本机 Postgres IO 快，
      且不再吃任何云配额）
- [ ] 观察一周：`docker stats`（backend 内存应 <400MB）、每日日报正常生成、
      数据新鲜度卡片全绿
- [ ] 更新 Agent 知识库手册：部署章节（Render → 阿里云）

---

## 回滚路径（每一步都可逆）

| 场景 | 回滚 |
|---|---|
| 阿里云后端异常 | 前端 API 指回 Render（Render 实例在迁移期保持运行，数据库未动） |
| 数据库迁移后缺数据 | Supabase 原库未删，改回连接串即恢复 |
| 备案被驳回 | 期间一直用 Supabase+过渡方案，无损失 |
