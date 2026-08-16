"""
【文件作用】开发模式启动脚本

直接运行 `python run.py` 即可启动后端，效果等同于命令行执行：
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

参数说明：
  - "app.main:app" → 指向 app/main.py 文件里的 app 变量（即 FastAPI 实例）
  - host="0.0.0.0"  → 监听所有网卡（允许局域网/容器外访问；若只本机用可写 127.0.0.1）
  - port=8000       → 监听端口
  - reload=True     → 代码改动后自动重启（类似 nodemon），仅开发用，生产环境关闭
"""
import uvicorn

# __name__ == "__main__" 表示该文件被直接运行（而不是被 import）
if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
