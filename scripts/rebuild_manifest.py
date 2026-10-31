# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】重建 dump 目录的 manifest.json / _tables.txt
================================================================================
什么时候需要（2026-10-31 实际发生）：
  用 `--tables <单表>` 重跑一张表时，脚本最后会**覆盖** manifest，
  于是 106 张的清单被写成 1 张。数据文件本身完好，只是清单丢了。
  ⇒ 本脚本扫描目录、从每个 `.csv.gz` 数出行数、用 `_schema.json` 补列名，
    重新生成完整清单（`tables_exported` 等字段恢复正确）。

用法：
    python scripts/rebuild_manifest.py [dump_dir]
================================================================================
"""
import gzip
import json
import os
import sys
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    d = argv[0] if argv else "db_dump_20261031"
    if not os.path.isdir(d):
        print(f"FAIL 目录不存在: {d}")
        return 1

    schema_path = os.path.join(d, "_schema.json")
    tables = {}
    if os.path.exists(schema_path):
        with open(schema_path, encoding="utf-8") as f:
            tables = json.load(f).get("tables") or {}

    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".csv.gz"):
            continue
        t = name[: -len(".csv.gz")]
        p = os.path.join(d, name)
        try:
            # ★ 多 gzip member（分批导出）读取时自动串联，数行数无需特殊处理
            with gzip.open(p, "rt", encoding="utf-8", newline="") as fh:
                next(fh, None)                  # 跳过表头
                n = sum(1 for _ in fh)
        except Exception as e:
            print(f"WARN 读取失败 {t}: {type(e).__name__} {str(e)[:80]}")
            n = None
        cols = [c["name"] for c in (tables.get(t) or {}).get("columns") or []]
        out.append({"name": t, "rows": n,
                    "bytes_gz": os.path.getsize(p), "columns": cols})

    manifest = {
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "rebuilt": True,
        "note": "由 scripts/rebuild_manifest.py 依据目录实际文件重建",
        "tables_total": len(tables) or len(out),
        "tables_exported": len(out),
        "tables": out,
    }
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    with open(os.path.join(d, "_tables.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(x["name"] for x in out) + "\n")

    total_rows = sum(x["rows"] or 0 for x in out)
    total_mb = sum(x["bytes_gz"] for x in out) / 1048576
    print(f"重建完成: {len(out)} 张表 / {total_rows} 行 / {total_mb:.2f} MB")
    bad = [x["name"] for x in out if x["rows"] is None]
    if bad:
        print("读不出行数的表: " + ", ".join(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
