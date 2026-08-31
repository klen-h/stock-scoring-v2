# -*- coding: utf-8 -*-
import io, sys, time
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.flash import rules

print("本地 datetime.now():", datetime.now().isoformat())
print("本地 time.strftime :", time.strftime("%Y-%m-%d %H:%M:%S"))
print("北京 rules.beijing_now():", rules.beijing_now().isoformat())
print()
print("本地日期:", datetime.now().strftime("%Y-%m-%d"),
      " 北京日期:", rules.beijing_now().strftime("%Y-%m-%d"),
      " 相同:", datetime.now().strftime("%Y-%m-%d") == rules.beijing_now().strftime("%Y-%m-%d"))
