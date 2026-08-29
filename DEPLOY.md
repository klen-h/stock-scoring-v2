# 部署指南

> 核心原则：**凭证永远不进 git**。仓库里只有 `.env.example`（变量名模板），
> 真实值在三个地方之一：本地 `backend/.env`、Render 控制台、部署服务器的 `.env`。
> 代码无需任何修改——环境变量注入后自动生效（真实环境变量优先于 .env 文件）。

## 方式〇（两人小团队·推荐）：本机部署

**一台电脑跑后端（单进程同时服务 API + 页面），另一个人用浏览器访问。**
数据存在本机硬盘 `backend/data/`——彻底解决 Render 免费版"部署清零/休眠"两个问题，
不需要任何云服务。

```bash
# 一次性准备
cp backend/.env.example backend/.env && vim backend/.env   # 填凭证
cd frontend && npm ci && npm run build && cd ..            # 构建前端（产物 dist/）

# 日常启动（backend 目录下）
python run.py            # 或 uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 自己用：打开 `http://localhost:8000`
- 另一人用（同一网络）：`http://<你的内网IP>:8000`（Windows 查 IP：`ipconfig`）
- 两人不在同一网络：装 [Tailscale](https://tailscale.com)（免费，两台都装），
  用 Tailscale 分配的 IP 访问，等效"虚拟局域网"，无需公网/端口映射
- Windows 防火墙放行 8000 端口：`netsh advfirewall firewall add rule name="stock-8000" dir=in action=allow protocol=TCP localport=8000`

**开机自启（Windows）**：写一个 `start.bat`（内容：`cd /d D:\klen\stock-scoring-v2\backend && python run.py`），
任务计划程序 → 创建基本任务 → 触发器"登录时" → 操作"启动程序"选 start.bat。

**特性与限制**：
- 数据持久：信号跟踪/胜率/诊断历史全在本机硬盘，重启不丢
- 调度器随进程常驻：电脑开机即运行；**电脑关机则停**（第二天开机会自动补跑错过的复盘——
  窗口已加宽：盘前补到 11:30、午盘补到 13:00、盘后补到 23:59）
- 注意 `--reload` 只用于开发；长期挂着可去掉（run.py 默认带，改用命令行方式启动）

## 云部署备选（Render / Docker）


## 需要配置的变量

| 变量 | 必要性 | 说明 |
|---|---|---|
| `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` | LLM 诊断/复盘必需 | 不配则事件流照常、诊断显示"未配置" |
| `FLASH_COOKIE` | 快讯事件流必需 | 金十会话 Cookie，**会过期**（健康监控会告警提醒） |
| `WECHAT_WEBHOOK` | 可选 | 手机推送；不配走 Web + 页面通知 |
| `LLM_DAILY_MAX_CALLS` 等 | 可选 | 熔断上限/轮询间隔/费用估算，见 `.env.example` |

## 方式一：Render（render.yaml 已配好）

1. 推送仓库到 GitHub，Render 导入（`render.yaml` 自动生效）
2. **控制台 → Environment** 填入上面各变量的真实值（yaml 里 `sync: false` 的项）
3. ⚠️ **免费版两个硬伤**：
   - 15 分钟无访问会**休眠 → 调度器停摆**（快讯不轮询、复盘不跑）。
     补救：用 [cron-job.org](https://cron-job.org) 免费定时每 10 分钟 GET 你的 `/api/health`；
     或用仓库里的 `.github/workflows/keep-alive.yml`（公开仓库免费）
   - 文件系统临时：**每次部署 backend/data/ 清零**（胜率统计/信号历史全丢）。
     补救（已内置，推荐先开着）：**浏览器数据镜像**——见下
4. 本项目是常驻后台任务型应用（调度器+LLM+推送），长期建议 starter 计划

### 浏览器数据镜像（免费版持久化兜底，已内置）

两个用户各打开一次页面，浏览器每 5 分钟自动把 `backend/data/` 全量备份到
localStorage；服务端部署清零后，前端检测到"服务端条目数 < 镜像条目数"会自动
POST `/api/flash/restore` 恢复，并弹通知告知。

- 数据缺口 = 最后一次镜像同步之后的几分钟（页面关着则到上次开页为止）
- 两台电脑 = 两份镜像互为备份；**不要清浏览器站点数据**（清了镜像就没了）
- 可选防护：设置 `BACKUP_SECRET` 环境变量后，恢复接口要求请求头
  `X-Backup-Secret` 匹配（首次恢复时浏览器会弹框要求输入一次）
- 快讯监控页状态条显示"浏览器镜像 HH:MM"即正常工作

## 方式二：Docker / VPS（推荐，调度器常驻不睡）

```bash
git clone <你的仓库> && cd stock-scoring-v2
cp backend/.env.example backend/.env
vim backend/.env          # 填入真实值（此文件不进 git）
docker compose up -d --build
```

- `docker-compose.yml` 已配置：`env_file` 自动读取 `backend/.env`
- `./backend` 整目录挂载进容器 → `backend/data/` 随宿主机持久化（容器重建不丢）
- `restart: unless-stopped` → 宿主机重启后自动拉起，调度器常驻

## 方式三：本地开发

`backend/.env`（同 `.env.example` 结构）+ `python run.py`。热重载时改 `.env`
需重启进程（环境变量在启动时读取）。

## 数据与备份

所有运行时状态在 `backend/data/`（已 gitignore）：
`tracking.json`（信号+胜率）｜`analyses.json`（LLM诊断全文）｜`reviews.json`（复盘全文）｜
`macro_history.json`（宏观历史，回测用）｜`llm_usage.json`（用量）｜`flash_state.json`（事件簇游标）｜
`calendar.json`（财经日历缓存，丢了会自动重拉，可不备份）

迁移/备份 = 拷贝这个目录。金十 Cookie 过期时页面通知会弹"数据源异常：金十快讯"。
（财经日历是独立数据源，用开放接口无需 Cookie，不会随快讯 Cookie 过期一起告警。）
