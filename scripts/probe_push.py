# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】企微推送自检：区分「系统没判断要说」/「判断了但没发出去」
================================================================================

用法（服务器上）：
    backend/.venv/bin/python scripts/probe_push.py            # 只诊断，不发消息
    backend/.venv/bin/python scripts/probe_push.py --send     # 额外真发一条测试

为什么需要它：
  推送链有三道静默闸门，任一道没开都表现为"企微没收到"，而且**都不报错**：
    ① `WECHAT_WEBHOOK` 为空            ⇒ 全部推送直接 return（日志也不打）
    ② `WECHAT_BUSINESS_ALERTS != 1`    ⇒ **日常推送全关**（默认就是 0！）
        只放行 `force=True`：关键通知 / 失败告警 / 战法买入信号
    ③ 分类群 `WECHAT_HOOK_*` 配错       ⇒ 回落主群，看起来"少了一些"
  再叠加 `push_log` 表：它记录的是「**系统判断要说这件事**」，不代表已送达。
  ⇒ 有记录但没收到 = 送达问题；没记录 = 任务没跑 / 被开关拦下。
================================================================================
"""
import os
import sys

_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, _BACKEND)


def _load_dotenv(path: str) -> int:
    """手工解析 `backend/.env`，**必须在 import app 模块之前**调用。

    ★ 为什么必须显式加载：`wechat.py` 里 `WECHAT_WEBHOOK = os.environ.get(...)`
      是在 **import 时求值**的常量。若先 import 再读文件，诊断会显示"全部未配置"，
      而实际 systemd 跑起来是好的 —— 会得出完全相反的结论。
    ★ 用 `setdefault`：**已经存在的环境变量优先**（systemd EnvironmentFile 注入的
      应覆盖文件里的旧值），避免诊断结果与线上进程不一致。
    """
    n = 0
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
                n += 1
    return n


_loaded = _load_dotenv(os.path.join(_BACKEND, ".env"))


def _mask(v: str) -> str:
    """webhook / secret 脱敏：只留头尾，避免日志泄露 key。"""
    if not v:
        return "（空）"
    return (v[:34] + "..." + v[-6:]) if len(v) > 46 else (v[:12] + "***")


def main():
    send = "--send" in sys.argv

    print("=" * 88)
    print("企微推送自检")
    print("=" * 88)
    print(f"（从 backend/.env 载入 {_loaded} 个未在环境中定义的变量）")

    # ── ① 原始环境变量 ──
    hook_main = (os.environ.get("WECHAT_WEBHOOK") or "").strip()
    biz = (os.environ.get("WECHAT_BUSINESS_ALERTS") or "").strip()
    hooks = {k: (os.environ.get(f"WECHAT_HOOK_{k}") or "").strip()
             for k in ("RISK", "COACH", "BRIEF", "ALERT")}
    apps = {k: (os.environ.get(f"WECHAT_NOTIFY_{k}") or "").strip()
            for k in ("RISK", "COACH", "BRIEF", "ALERT", "DEFAULT", "TOUSER")}

    print("\n【环境变量】")
    print(f"  WECHAT_WEBHOOK            = {_mask(hook_main)}")
    print(f"  WECHAT_BUSINESS_ALERTS    = {biz!r}   {'← 未设置(默认0)' if not biz else ''}")
    for k, v in hooks.items():
        print(f"  WECHAT_HOOK_{k:<6}         = {_mask(v)}")
    for k, v in apps.items():
        print(f"  WECHAT_NOTIFY_{k:<9}     = {_mask(v)}")

    # ── ② 代码里实际生效的开关（以模块里的常量为准，不信"我以为配了"）──
    from app.flash import wechat
    enabled = wechat.BUSINESS_ALERTS_ENABLED
    has_hook = bool(hook_main or any(hooks.values()))
    print("\n【实际生效】")
    print(f"  主 webhook 或分类群可用 : {has_hook}")
    print(f"  日常推送开关(业务告警)  : {'开' if enabled else '关'}")
    print(f"  只放行 force=True 的     : {sorted(['关键通知', '失败告警', '战法买入信号'])}")

    problems = []
    if not has_hook:
        problems.append("没有任何 webhook 配置 ⇒ 所有推送都会静默 return（连日志都不打）")
    if not enabled:
        problems.append("WECHAT_BUSINESS_ALERTS 未设为 1 ⇒ 诊断/复盘/信号提醒等**日常推送全部被拦**，"
                        "只有 force=True 的三类能发")
    print("\n【结论】")
    if problems:
        for p in problems:
            print("  ⚠️ " + p)
    else:
        print("  ✅ 配置看起来是完整的（日常推送已开）")

    # ── ③ push_log：系统"判断要说"的留痕 ──
    print("\n【push_log 今日记录】（有记录≠已送达；无记录=任务没跑或被开关拦）")
    try:
        from app.flash import signal_bus
        from app.flash.rules import beijing_now
        day = beijing_now().strftime("%Y-%m-%d")
        # `by_date()` 返回 List[Dict]，字段名是 `force`（不是 force_flag）
        items = signal_bus.by_date(date=day, limit=40, trunc=120) or []
        print(f"  日期 {day} · {len(items)} 条")
        for it in items[:12]:
            f = "force" if it.get("force") else "业务"
            print(f"    {str(it.get('ts'))[11:16]}  [{f:<5}] {str(it.get('title'))[:44]}")
        if len(items) > 12:
            print(f"    ...另有 {len(items)-12} 条")
        if not items:
            print("    （今天没有任何记录 ⇒ 调度器可能没跑，或任务被判据跳过）")
    except Exception as e:
        print(f"  查询失败（不影响其它结论）: {type(e).__name__}: {str(e)[:80]}")

    # ── ④ 真发一条（可选）──
    if send:
        print("\n【测试推送】")
        if not has_hook:
            print("  ⚠️ 没有 webhook，无法测试")
        else:
            ok_force = wechat.notify("alert", "自检 · force 通道",
                                     "这条走 force=True（不受日常开关限制）。收到说明 webhook 通。",
                                     force=True)
            print(f"  force 通道 : {'✅ 已发出' if ok_force else '❌ 未发出'}")
            if not enabled:
                print("  日常 通道 : ⚠️ 被 WECHAT_BUSINESS_ALERTS 拦下（这正是默认值的原因）")
            else:
                wechat.push_markdown_batched("自检 · 日常通道", "这条走日常通道，应能收到。")
                print("  日常 通道 : ✅ 已发出")

    print("\n提示：改完 .env 后必须 `sudo systemctl restart stock-scoring` 才生效"
          "（环境变量在进程启动时读取，不改代码就不会重载）。")


if __name__ == "__main__":
    main()
