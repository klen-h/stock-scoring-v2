"""
================================================================================
【文件作用】用户认证工具函数
================================================================================

JWT Token + bcrypt 密码哈希。
环境变量：
  JWT_SECRET  JWT 签名密钥（默认随机生成，重启后旧 token 失效）
  JWT_EXPIRE_HOURS  Token 有效期（默认 87600 小时 = 10 年，实现"永久登录"）
================================================================================
"""

import os
import jwt
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# JWT 配置
# ★ 2026-10-24（BK-04b，GUARDRAILS #12）：JWT_SECRET 未配置时**不再回退机器名可预测密钥**
#   （旧实现 sha256("stock-scoring-{机器名}-secret-2026") —— 密钥可被猜出 ⇒ token 可伪造、
#   鉴权体系整体失效）。改为**每次启动随机生成临时密钥**：
#     · 不可伪造（fail-closed 的安全语义）；
#     · 代价：未配置 JWT_SECRET 时每次重启全员重新登录（个人工具可接受）；
#     · 生产应显式配置 JWT_SECRET（Render 控制台），配置后重启不再掉登录。
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_SECRET_CONFIGURED = bool(JWT_SECRET)
if not JWT_SECRET:
    JWT_SECRET = secrets.token_hex(32)
    print("[auth] WARNING: JWT_SECRET not set, using ephemeral random secret "
          "(all tokens invalidated on restart; configure JWT_SECRET in env)")  # ASCII（铁律⑥）

JWT_ALGORITHM = "HS256"
# 默认 10 年（87600 小时）—— 个人项目无需频繁重新登录；如需调整可在 Render 控制台设 JWT_EXPIRE_HOURS
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "87600"))

# 密码哈希（使用 SHA-256 + salt）

def hash_password(password: str) -> str:
    """密码哈希：salt + SHA-256"""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"


def verify_password(plain: str, stored: str) -> bool:
    """验证密码"""
    if ":" not in stored:
        return False
    salt, hashed = stored.split(":", 1)
    check = hashlib.sha256(f"{salt}{plain}".encode()).hexdigest()
    return secrets.compare_digest(check, hashed)


def create_token(user_id: int, username: str) -> str:
    """创建 JWT Token"""
    payload = {
        "user_id": user_id,
        "username": username,
        # ★ 2026-10-24：utcnow() 已弃用且无时区（坑 7）——显式 UTC aware
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[Dict]:
    """解析 JWT Token，返回 payload 或 None"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ── FastAPI 依赖注入 ──

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict:
    """
    从请求头提取并验证 JWT Token。
    返回 {"user_id": int, "username": str}
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="未登录")
    
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    
    return {
        "user_id": payload["user_id"],
        "username": payload["username"],
    }


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Optional[Dict]:
    """可选认证：有 token 就解析，没有就返回 None"""
    if not credentials:
        return None
    payload = decode_token(credentials.credentials)
    if not payload:
        return None
    return {
        "user_id": payload["user_id"],
        "username": payload["username"],
    }
