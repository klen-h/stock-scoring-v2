# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db

rows = db.fetch(
    "SELECT phase, time, markdown FROM flash_reviews "
    "WHERE phase = 'postmarket' ORDER BY time DESC LIMIT 1")
if not rows:
    print('postmarket 复盘无记录')
else:
    r = rows[0]
    print(f"最近盘后复盘时间: {r['time']}")
    print('=' * 60)
    print(r['markdown'])
