# ============================================================
# 阶段 1：用 Node 构建前端产物（frontend/dist）
# ============================================================
FROM node:18-slim AS frontend-builder
WORKDIR /build/frontend
# 先复制依赖文件，利用 Docker 缓存
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
# 复制前端源码并构建
COPY frontend/ .
RUN npm run build

# ============================================================
# 阶段 2：Python 后端 + 嵌入前端产物
# ============================================================
FROM python:3.9-slim-buster

WORKDIR /app/backend

# 先复制依赖文件并安装依赖
COPY ./backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端源码
COPY ./backend/ .

# 把阶段 1 构建的前端产物复制到 /app/frontend/dist
# （main.py 里 FRONTEND_DIST 解析到 /app/frontend/dist）
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
