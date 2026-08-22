# 前后端指标对照脚本（后端侧）：末几根 DIF/DEA/MACD 高精度输出
# 用法: cd backend; python _cmpM.py [股票代码]   （默认 002479）
# 对照: node _cmpM.cjs [股票代码]（注意后端 500 根 vs 数据包 150 根，EMA 系列初期种子不同但已收敛）
import sys

sys.path.insert(0, ".")

from app.tencent import get_kline
from app.routers.scoring import _calc_technical

code = sys.argv[1] if len(sys.argv) > 1 else "002479"
kl = get_kline(code, period="day", count=500)
print("K线根数:", len(kl), "末3日期:", [k["date"] for k in kl[-3:]])
tech = _calc_technical(kl)
for t in tech[-5:]:
    print(t["date"], "close=", t["close"], "DIF=", t["dif"], "DEA=", t["dea"], "MACD=", t["macd"])
