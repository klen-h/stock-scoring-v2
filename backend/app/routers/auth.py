"""
================================================================================
【文件作用】用户认证路由
================================================================================

URL 前缀 /api/auth：
  POST /api/auth/register  → 注册
  POST /api/auth/login     → 登录
  GET  /api/auth/current   → 获取当前用户信息
================================================================================
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.database import db
from app.auth import hash_password, verify_password, create_token, get_current_user

router = APIRouter()


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/register")
def register(req: RegisterRequest):
    """
    注册新用户。
    
    用户名要求：3-20 个字符
    密码要求：至少 4 个字符
    """
    username = req.username.strip()
    password = req.password
    
    if len(username) < 3 or len(username) > 20:
        raise HTTPException(400, "用户名需要 3-20 个字符")
    if len(password) < 4:
        raise HTTPException(400, "密码至少 4 个字符")
    
    # 检查用户名是否已存在
    existing = db.fetch_one("SELECT id FROM users WHERE username = %s", (username,))
    if existing:
        raise HTTPException(400, "用户名已存在")
    
    # 创建用户
    password_hash = hash_password(password)
    db.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
        (username, password_hash)
    )
    
    # 获取用户 ID
    user = db.fetch_one("SELECT id, username FROM users WHERE username = %s", (username,))
    if not user:
        raise HTTPException(500, "注册失败")
    
    # 生成 Token
    token = create_token(user["id"], user["username"])
    
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
        }
    }


@router.post("/login")
def login(req: LoginRequest):
    """登录"""
    username = req.username.strip()
    password = req.password
    
    # 查找用户
    user = db.fetch_one("SELECT id, username, password_hash FROM users WHERE username = %s", (username,))
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    
    # 验证密码
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    
    # 生成 Token
    token = create_token(user["id"], user["username"])
    
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
        }
    }


@router.get("/current")
def get_current(user: dict = Depends(get_current_user)):
    """获取当前用户信息（需要登录）"""
    return {"user": user}
