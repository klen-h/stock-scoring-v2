"""
================================================================================
【文件作用】用户认证工具函数
================================================================================

JWT Token + bcrypt 密码哈希。
环境变量：
  JWT_SECRET  JWT 签名密钥（默认随机生成，重启后旧 token 失效）
  JWT_EXPIRE_HOURS  Token 有效期（默认 72 小时）
================================================================================
"""

import os
import jwt
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict
from functools import wraps

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# JWT 配置
JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    # 未配置时生成一个固定密钥（基于机器名 + 固定盐值）
    # 注意：重启后不会变化，但不同机器会生成不同密钥
    _machine_id = os.environ.get("COMPUTERNAME", os.environ.get("HOSTNAME", "default"))
    JWT_SECRET = hashlib.sha256(f"stock-scoring-{_machine_id}-secret-2026".encode()).hexdigest()

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "72"))

# 密码哈希（使用 SHA-256 + salt）
import secrets

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
    return check == hashed


def create_token(user_id: int, username: str) -> str:
    """创建 JWT Token"""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.utcnow(),
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
