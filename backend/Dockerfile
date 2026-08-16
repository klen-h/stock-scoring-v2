# Dockerfile：构建后端 Docker 镜像的配置文件
#
# 作用：把整个后端打包成一个可移植的容器，方便部署。
# 配合根目录的 docker-compose.yml 使用（docker-compose up 即可启动）。
#
# 对前端工程师：你可以把镜像理解成一个"装好运行环境的轻量虚拟机"。
# Dockerfile 就是描述"如何装这个环境"的脚本。

# 基础镜像：官方 Python 3.9 精简版（slim-buster 体积小，基于 Debian）
FROM python:3.9-slim-buster

# 容器内的工作目录（后续命令都在这个目录下执行）
# 类比：cd /app/backend
WORKDIR /app/backend

# 先只复制依赖文件并安装依赖
# 技巧：单独复制 requirements.txt 是为了利用 Docker 缓存层 ——
#       只要 requirements.txt 不变，依赖安装这一层就不会重复执行，构建更快。
COPY ./requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # --no-cache-dir 减小镜像体积

# 再复制整个应用代码（src 变了才会重新执行这一层）
COPY . .

# 声明容器对外暴露的端口（仅文档作用，实际映射由 docker-compose 的 ports 控制）
EXPOSE 8000

# 容器启动时执行的命令：用 uvicorn 启动 FastAPI 应用
# 注意这里没有 --reload（生产环境不开热重载）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
