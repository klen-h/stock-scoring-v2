
# 使用 Node.js 构建前端
FROM node:16 as builder
WORKDIR /app/frontend
COPY ./frontend/package.json ./frontend/package-lock.json ./frontend/vite.config.js ./frontend/tsconfig.json ./frontend/tsconfig.node.json ./frontend/.eslintrc.cjs ./
RUN npm install
COPY ./frontend .
RUN npm run build

# 使用 Nginx 提供静态文件
FROM nginx:stable-alpine
COPY --from=builder /app/frontend/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
