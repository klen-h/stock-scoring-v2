# output_feedback.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】输出消费闭环（C2；判据/规格 = `PLAN_NEXT.md` 【附 B】v0，2026-10-11 立项）
把「系统输出改变了我什么」从感觉变成**可统计的数据** —— 这是项目第二个"一直没问的问题"
（第一个 = 评分预测力，已于同日检验 FAIL）的最低成本起步。
【边界（别放宽）】
· 只记 (date, source, ref, action) 四元组，**不存任何内容文本** —— 隐私与 egress 双最小化；
· `action` 只有两档：`acted`(有影响) / `dismissed`(没影响) —— 二选一成本低才做得久
（PLAN_NEXT 附 B 未决问题1 的结论）；
· 同一 `(source, ref)` **幂等覆盖** ⇒ 可改主意，一个输出只保留最新结论；
· **不联动**模拟盘/观察池（附 B 未决问题2 的结论：先白嫖分布，再决定要不要闭环到操作）。
【与 coach_al

## 输入 / 输出

- 【文件作用】输出消费闭环（C2；判据/规格 = `PLAN_NEXT.md` 【附 B】v0，2026-10-11 立项）
- 把「系统输出改变了我什么」从感觉变成**可统计的数据** —— 这是项目第二个"一直没问的问题"
- · 同一 `(source, ref)` **幂等覆盖** ⇒ 可改主意，一个输出只保留最新结论；

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash.rules`

## 被谁依赖

- `backend/app/main.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_ensure`, `save_feedback`, `feedback_summary`, `post_feedback`, `get_feedback_summary`

## 已知坑

- 输出消费闭环：POST /api/feedback 幂等覆盖（同 date+source+ref）；只记 source/ref/date 不存内容文本（隐私+egress 最小化）。

规模：92 行。
