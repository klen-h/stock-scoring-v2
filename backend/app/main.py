"""
================================================================================
【文件作用】FastAPI 应用入口（整个后端的"主文件"）
================================================================================

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
================================================================================
"""

from dotenv import load_dotenv
import os

# 加载 .env 文件（必须在读取环境变量之前）
load_dotenv()

FLASH_COOKIE = os.environ.get("FLASH_COOKIE", "")
print(FLASH_COOKIE)

# FastAPI 是后端框架；CORSMiddleware 用于解决跨域问题（和前端联调必须）
# 类比 Express 的 app = express() 和 cors 中间件
from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 导入路由模块（每个模块负责一类业务接口）
# from app.routers import xxx 中的 app 是 backend/app/ 目录（包）
from app.routers import market, stock, capital, sector, scoring, macro, flash
from app.routers import user as user_router
from app.routers import auth as auth_router
from app.strategies.router import router as strategies_router

# ──────────────────────────────────────────────────────────────
# lifespan：应用启动/关闭时执行（快讯监控调度器的启停）
# ──────────────────────────────────────────────────────────────
_scheduler_tasks = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库 + 拉起快讯/复盘/信号跟踪三个后台循环，关闭时优雅取消。"""
    global _scheduler_tasks
    # 初始化数据库表结构
    from app.database import db
    db.init_tables()
    # 执行数据迁移（如果 JSON 文件有未迁移的数据）
    try:
        from migrate import auto_migrate_if_needed
        auto_migrate_if_needed()
    except Exception as e:
        print(f"[main] 数据迁移检查失败: {e}")
    from app.flash import scheduler
    _scheduler_tasks = await scheduler.start()
    yield
    scheduler.stop(_scheduler_tasks)
    # 关闭时强制保存K线缓存到磁盘（下次启动可直接恢复，避免重新拉取触发WAF）
    try:
        from app.tencent import _save_kline_cache
        _save_kline_cache(force=True)
        print("[关闭] K线缓存已持久化到磁盘")
    except Exception as e:
        print(f"[关闭] K线缓存保存失败: {e}")
    # 关闭数据库连接
    try:
        db.close()
    except Exception:
        pass

# 创建 FastAPI 应用实例，配置标题和版本号（会显示在 /docs 文档页）
app = FastAPI(title="A股数据评分系统", version="1.0.0", lifespan=lifespan)

# ──────────────────────────────────────────────────────────────
# CORS 中间件：允许前端跨域访问后端
# ──────────────────────────────────────────────────────────────
# 前端（如 localhost:3000）访问后端（localhost:8000）属于跨域，
# 浏览器默认会拦截，需要后端通过 CORS 头明确放行。
# 这里 allow_origins=["*"] 表示允许任何来源（开发环境方便，生产应限制具体域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # 允许哪些前端域名访问，* 表示全部
    allow_credentials=True,    # 允许携带 Cookie
    allow_methods=["*"],       # 允许所有 HTTP 方法（GET/POST/PUT/DELETE...）
    allow_headers=["*"],       # 允许所有请求头
)

# ──────────────────────────────────────────────────────────────
# 注册路由（路由 = 一组接口的集合）
# ──────────────────────────────────────────────────────────────
# 类比 Express：app.use('/api/market', marketRouter)
# prefix 是 URL 前缀；tags 用于在 /docs 文档里分组显示
app.include_router(market.router,  prefix="/api/market",  tags=["市场行情"])  # 大盘指数、全A股列表
app.include_router(stock.router,   prefix="/api/stock",   tags=["个股数据"])  # 个股K线、实时行情、技术指标
app.include_router(capital.router, prefix="/api/capital", tags=["资金流向"])  # 资金流向（当前为空壳）
app.include_router(sector.router,  prefix="/api/sector",  tags=["板块数据"])  # 行业/概念板块（当前为空壳）
app.include_router(scoring.router, prefix="/api/score",   tags=["评分数据"])  # 股票评分（核心功能）
app.include_router(macro.router,   prefix="/api/macro",   tags=["宏观数据"])  # 宏观面板+规则方向分
app.include_router(flash.router,   prefix="/api/flash",   tags=["快讯监控"])  # 快讯事件/LLM诊断/信号跟踪
app.include_router(strategies_router, prefix="/api/strategies", tags=["战法选股"])  # 量化战法扫描
app.include_router(auth_router.router, prefix="/api/auth", tags=["用户认证"])  # 注册/登录
app.include_router(user_router.router, prefix="/api/user", tags=["用户数据"])  # 自选股/交易计划/持仓


# 路由路径装饰器：把下面的函数绑定到 GET /api/health 这个 URL
# 类比 Express：app.get('/api/health', (req, res) => res.json({...}))
@app.get("/api/health")
def health():
    """健康检查接口：前端/运维用它判断后端是否存活"""
    return {"status": "ok", "service": "stock-scoring-backend"}


# ──────────────────────────────────────────────────────────────
# 前端静态托管（单进程全栈：一个 uvicorn 同时服务 API + 页面）
# ──────────────────────────────────────────────────────────────
# 背景：两人小团队本机部署——后端跑在自己电脑上，另一个人用浏览器直接访问
# 这台电脑的 8000 端口，不再需要单独跑前端服务/GitHub Pages。
#
# 前提：先构建前端（cd frontend && npm run build），产物在 frontend/dist/。
# dist 不存在时（纯 API 用法/开发期用 vite dev server）自动跳过，不影响 API。
import os
from fastapi.responses import FileResponse

FRONTEND_DIST = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))

if os.path.isdir(FRONTEND_DIST) and os.path.exists(os.path.join(FRONTEND_DIST, "index.html")):
    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str):
        """
        前端资源 + SPA 回退（必须注册在所有 /api 路由之后，靠注册顺序保证 API 优先）：
          - /assets/xxx.js 等真实文件 → 直接返回文件
          - /monitor、/stock/000001 等前端路由 → 回退 index.html（Vue Router 接管）
        """
        if full_path:
            file = os.path.join(FRONTEND_DIST, full_path)
            # 防目录穿越：解析后必须仍在 dist 内
            if (os.path.realpath(file).startswith(os.path.realpath(FRONTEND_DIST))
                    and os.path.isfile(file)):
                return FileResponse(file)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
else:
    print("[main] 未找到 frontend/dist（单进程全栈模式未启用；"
          "如需启用：cd frontend && npm run build 后重启）")
