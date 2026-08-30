# -*- coding: utf-8 -*-
"""手动触发一次复盘流（LLM 调用 + 企微推送），用于验证端到端链路。
★ 临时开启 WECHAT_BUSINESS_ALERTS（.env 默认关闭，否则复盘/诊断推送被静默跳过）。"""
import os
os.environ["WECHAT_BUSINESS_ALERTS"] = "1"   # 必须在 import app.flash 之前设置

import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print(f"[trigger] 开始: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
from app.flash import service, wechat
print(f"[trigger] 企微配置: webhook={'已配置' if wechat.WECHAT_WEBHOOK else '未配置'} "
      f"业务推送={wechat.BUSINESS_ALERTS_ENABLED}", flush=True)

r = service.run_review("premarket")
print(f"[trigger] 结束: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
print(f"[trigger] 结果: phase={r.get('phase')} signals_added={r.get('signals_added')} "
      f"markdown_len={len(r.get('markdown') or '')} error={r.get('error')}", flush=True)
