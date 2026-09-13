"""
================================================================================
【文件作用】企业微信自建应用「接收消息服务器」回调（2026-09-13）
================================================================================

用途：企微管理后台为自建应用配置「接收消息服务器URL」时，会用 GET 验证本端点
（解密 echostr 原文返回）；配置成功后解锁「企业可信IP」配置。
之后企微会把事件以 POST XML 推到这里——当前只验签解密后回 "success"（不处理事件）。

配置（应用详情 → 接收消息 → 设置接收消息服务器URL）：
  URL            = https://stock-scoring-v2.onrender.com/api/wecom/callback
  Token          = WECHAT_WECOM_TOKEN（自定义随机串，控制台与本端点保持一致）
  EncodingAESKey = WECHAT_WECOM_AES_KEY（43 位 [A-Za-z0-9]）

★ 多应用可共用本端点：每个应用的接收消息配置填同一组 Token/AESKey 即可。
★ 签名算法（官方）：sha1(sort(token, timestamp, nonce, echostr|encrypt))
★ 明文结构：random(16B) + msg_len(4B 网络序) + msg + receiveid(corpid)
依赖：pycryptodome（requirements 已声明；AES-256-CBC）。
"""

import base64
import hashlib
import struct

from fastapi import APIRouter, HTTPException, Query, Request, Response

from Crypto.Cipher import AES

import os

router = APIRouter()

_WECOM_TOKEN = os.environ.get("WECHAT_WECOM_TOKEN", "")
_WECOM_AES_KEY = os.environ.get("WECHAT_WECOM_AES_KEY", "")


def _aes_key() -> bytes:
    if not _WECOM_AES_KEY or len(_WECOM_AES_KEY) != 43:
        raise HTTPException(500, "WECHAT_WECOM_AES_KEY 未配置或不是 43 位")
    return base64.b64decode(_WECOM_AES_KEY + "=")


def _pkcs7_unpad(data: bytes) -> bytes:
    return data[:-data[-1]] if 0 < data[-1] <= 32 else data


def _decrypt(encrypt_b64: str) -> str:
    """AES-256-CBC 解密 → 去掉 16B 随机 + 4B 长度头 → 消息明文。"""
    cipher = AES.new(_aes_key(), AES.MODE_CBC, _aes_key()[:16])
    plain = _pkcs7_unpad(cipher.decrypt(base64.b64decode(encrypt_b64)))
    msg_len = struct.unpack(">I", plain[16:20])[0]
    return plain[20:20 + msg_len].decode("utf-8")


def _signature(*parts: str) -> str:
    return hashlib.sha1("".join(sorted(parts)).encode("utf-8")).hexdigest()


def _verify(token: str, timestamp: str, nonce: str, encrypt: str,
            msg_signature: str) -> str:
    if _signature(token, timestamp, nonce, encrypt) != msg_signature:
        raise HTTPException(400, "signature mismatch")
    return _decrypt(encrypt)


@router.get("/callback")
def wecom_callback_verify(request: Request,
                          msg_signature: str = Query(...),
                          timestamp: str = Query(...),
                          nonce: str = Query(...),
                          echostr: str = Query(...)):
    """控制台「设置接收消息服务器URL」的 GET 验证：返回解密后的 echostr 原文。"""
    if not _WECOM_TOKEN:
        raise HTTPException(500, "WECHAT_WECOM_TOKEN 未配置")
    try:
        msg = _verify(_WECOM_TOKEN, timestamp, nonce, echostr, msg_signature)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"verify failed: {e}")
    return Response(content=msg, media_type="text/plain")


@router.post("/callback")
async def wecom_callback_event(request: Request,
                               msg_signature: str = Query(...),
                               timestamp: str = Query(...),
                               nonce: str = Query(...)):
    """事件接收：验签解密后立即回 success（当前不处理事件内容，仅记日志）。"""
    if not _WECOM_TOKEN:
        raise HTTPException(500, "WECHAT_WECOM_TOKEN 未配置")
    body = (await request.body()).decode("utf-8", errors="ignore")
    encrypt = ""
    try:
        import re
        m = re.search(r"<Encrypt><!\[CDATA\[(.*?)\]\]></Encrypt>", body, re.S) \
            or re.search(r"<Encrypt>(.*?)</Encrypt>", body, re.S)
        encrypt = m.group(1) if m else ""
        if encrypt:
            msg = _verify(_WECOM_TOKEN, timestamp, nonce, encrypt, msg_signature)
            print(f"[wecom] 收到事件: {msg[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[wecom] 事件解析失败（忽略，回 success 防重推）: {e}")
    return Response(content="success", media_type="text/plain")
