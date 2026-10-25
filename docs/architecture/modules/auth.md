# auth.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】用户认证工具函数

JWT Token + bcrypt 密码哈希。
环境变量：
JWT_SECRET  JWT 签名密钥（默认随机生成，重启后旧 token 失效）
JWT_EXPIRE_HOURS  Token 有效期（默认 87600 小时 = 10 年，实现"永久登录"）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- （无项目内依赖）

## 被谁依赖

- `backend/app/routers/auth.py`
- `backend/app/routers/coach.py`
- `backend/app/routers/performance.py`
- `backend/app/routers/system.py`
- `backend/app/routers/tasks.py`
- `backend/app/routers/user.py`
- `backend/migrate_user_isolation.py`

## 关键阈值 / 环境变量

- `COMPUTERNAME`、`HOSTNAME`、`JWT_EXPIRE_HOURS`、`JWT_SECRET`

## 顶层结构（def/class，前 25 个）

`hash_password`, `verify_password`, `create_token`, `decode_token`, `get_current_user`, `get_current_user_optional`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：113 行。
