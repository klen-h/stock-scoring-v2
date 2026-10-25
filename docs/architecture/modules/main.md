# main.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】FastAPI 应用入口（整个后端的"主文件"）

类比前端：
- 这个文件相当于 Vue 的 main.js / React 的 App.jsx，是程序启动的起点。
- FastAPI 是 Python 的后端框架，地位类似 Node.js 的 Express。

启动方式（见 run.py）：
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
解释：用 uvicorn（一个 ASGI 服务器，类似 nodemon + express）
运行 app/main.py 文件里名为 app 的对象。

启动后访问：
- http://localhost:8000/docs          → FastAPI 自动生成的接口文档（Swagger UI）
- http://localhost:8000/api/health    → 健康检查

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.database`
- `app.env_check`
- `app.flash`
- `app.output_feedback`
- `app.routers`
- `app.routers.coach`
- `app.routers.paper`
- `app.routers.performance`
- `app.strategies.router`
- `app.tencent`

## 被谁依赖

- `backend/tmp_archive/tmp_dbg_weight.py`

## 关键阈值 / 环境变量

- `DATA_SOURCE`、`FLASH_COOKIE`、`GIT_COMMIT`、`RENDER_GIT_COMMIT`

## 顶层结构（def/class，前 25 个）

`lifespan`, `health`

## 已知坑

- 路由注册顺序敏感：routers/scoring.py 的 @router.get("/{symbol}") 通配在前，单段静态路由必须两段路径否则被吞（返回 200 {"error"}）；lifespan 启动需 PYTHONIOENCODING=utf-8（Windows GBK）。

规模：318 行。
