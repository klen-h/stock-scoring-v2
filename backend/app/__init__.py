"""
================================================================================
【文件作用】app 包初始化：加载 backend/.env 环境变量
================================================================================

必须在任何 app.* 子模块 import 之前执行——因为 llm.py / source.py / wechat.py
都在模块级读取环境变量（LLM_API_KEY / FLASH_COOKIE / WECHAT_WEBHOOK）。
Python 的包机制保证了本文件先于所有子模块执行，因此这里加载 .env 即可全局生效。

自实现极简 .env 解析（KEY=VALUE，# 注释），不引入 python-dotenv 依赖。
已存在的环境变量不会被 .env 覆盖（真实 env 优先级更高，方便部署平台注入）。
================================================================================
"""

import os


def _load_env_file() -> None:
    """解析 backend/.env（若存在），把未设置的环境变量补进 os.environ。"""
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key and key not in os.environ:      # 真实环境变量优先
                    os.environ[key] = value
    except OSError as e:
        print(f"[env] .env 读取失败: {e}")


_load_env_file()
