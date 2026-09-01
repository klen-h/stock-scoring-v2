# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db

# 1. 误归"其它行业"的银行股 → 金融行业（农商行/城商行无"银行"二字漏网）
fixes = [
    (("%银行%",), "金融行业", "名字含银行"),
    (("%电器%",), "家电行业", "名字含电器"),
    (("%电商%",), "商业百货", "名字含电商"),
    (("%农商行%", "%城商行%"), "金融行业", "名字含农商/城商"),
]
for pats, target, label in fixes:
    for p in pats:
        n = db.execute(
            "UPDATE stock_industry SET main_industry = %s "
            "WHERE main_industry = '其它行业' AND name LIKE %s",
            (target, p))
        print(f"{label} ({p}) → {target}: {n} 只")

# 2. 校验关键行业规模
for ind in ("金融行业", "家电行业", "商业百货", "其它行业"):
    r = db.fetch_one("SELECT COUNT(*) AS c FROM stock_industry WHERE main_industry=%s", (ind,))
    print(f"  {ind}: {r['c']} 只")
