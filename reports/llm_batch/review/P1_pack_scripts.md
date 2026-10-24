# P1 打包/发布脚本代码审查

审查范围：5 文件 / 约 2738 行 / 2026-10-24

背景：2026-10-24 事故（本地旧包经 orphan 重建 force push 把线上新包整批删掉）后，重点核查 publish_packs_local.py 的日期护栏、增量脚本的一致性/完整性、generate-kline-pack 的 --top 与质量门槛、以及一切会 force push / 清空远端的点。所有引用均为文件原文。

---

## scripts/publish_packs_local.py

### [P1] scripts/publish_packs_local.py:226 orphan 重建模式下，本地缺失的包文件会被静默跳过并从线上整批删除，且无任何护栏
- 原文: `                print(f"  [跳过] {fn}（本地不存在）")`
- 问题: 本地不存在的 PACK_FILES 只打印"[跳过]"就继续，而后续 orphan 重建是"单 commit 全新树 + push --force"（第 189-191、313 行），新树只含本次 prepared 文件 + dist + .nojekyll。
- 影响: 线上存在、本地缺失的包（backend-pack.db.gz / indicators-pack.json.gz / kline-pack-latest.json.gz / realtime-quotes.json）会被直接从 Pages 删除——比"日期回滚"更彻底的数据丢失；且护栏 4 因 `kline_date=""` 而整体失效。干净 clone 里只跑 update_pack_incremental.py（其 docstring 第 25 行就指示"完成后：python scripts/publish_packs_local.py"）即可触发：线上后端包/指标包被删。
- 建议: 发布前对 4 个文件逐一探测线上 URL（HEAD 或 Range GET），"线上存在但本次不发布"视为回滚类危险操作，默认拦截（可用与 --allow-downgrade 同级的显式 flag 绕过）。
- 置信度: 高

### [P1] scripts/publish_packs_local.py:216 frontend/dist 缺失时的提示与 orphan 重建实际行为矛盾，会造成整站 404
- 原文: `            print("  ⚠️ frontend/dist 不存在——本次只发数据包，前端页面保持现状"`
- 问题: 提示称"前端页面保持现状"，但 orphan 重建不 clone 旧树，push --force 后新树里没有 index.html/assets，页面不可能"保持现状"。同文件第 196-197 行注释自己记载了 2026-09-09 同款事故："orphan 只放 data/ 会把页面抹掉 → 网页 404"。
- 影响: 本地没 build 过前端（dist 不存在）时跑本脚本 → 线上整站 404，且提示语引导用户以为安全。
- 建议: dist 不存在时默认拒绝发布（或要求类似 --data-only-wipe-site 的显式 flag），并把提示语改为与 orphan 语义一致（"页面会被删除"）。
- 置信度: 高

### [P1] scripts/publish_packs_local.py:273 回滚护栏只比对 K 线包日期，backend-pack.db.gz / indicators-pack.json.gz 的线上回滚不在防护内
- 原文: `            if remote_date and kline_date and kline_date < remote_date:`
- 问题: 护栏 4 只取 `dates.get("kline-pack-latest.json.gz")` 与线上 KLINE_URL 比较；backend-pack.db.gz（meta pack_date）和 indicators-pack.json.gz（JSON date 字段）没有对应的"线上日期 vs 本地日期"比对，也不探测对应线上 URL。
- 影响: 带 --force 发布时（--force 绕过护栏 1-3 的日期一致性），本地陈旧的后端/指标包可以整批回滚线上较新的后端/指标包——正是 10-24 事故的同一模式，只是换了文件。
- 建议: 把 indicators-pack.json.gz（date 在 JSON 头部，可复用 _remote_pack_date 的前 128KB 方案）纳入护栏 4 的逐一比对；backend 包可经"与 indicators 包本地日期一致（护栏 2 已保证）"间接覆盖。
- 置信度: 高

### [P2] scripts/publish_packs_local.py:163 读线上日期失败即静默跳过回滚护栏（fail-open），断网/404 时 10-24 护栏整体失效
- 原文: `        print(f"  ⚠️ 读线上包日期失败（跳过回滚护栏）: {str(e)[:80]}")`
- 问题: `_remote_pack_date` 异常时返回 ""，护栏 4 条件因 `remote_date` 为空而不成立，仅打印一行警告后照常 force push。
- 影响: 网络不通、Pages 404（如换仓库/fork）或 CDN 异常时，`--force` 可以在"看不见线上状态"的情况下重现 10-24 事故，且日志里只有一行易被忽略的警告。
- 建议: 读线上日期失败时改为默认拦截（要求显式 --allow-downgrade 或 --force 才放行），而非静默放行。
- 置信度: 高

### [P2] scripts/publish_packs_local.py:91 新鲜度下限用"日历工作日"而非"交易日"，法定节假日后必现假阳性拦截
- 原文: `    while d.weekday() >= 5:`
- 问题: `_last_weekday_bj` 只跳过周末，不看 A 股休市日（春节/国庆等）；节假日后的工作日 floor 会超过真实最近交易日。
- 影响: 例如国庆后首个交易日（10-09）发布节前 09-30 的包会被判"早于上一个工作日 10-08"而拒绝，用户被迫习惯性加 --force——而 --force 会连护栏 1/2 一起绕过，反而放大风险。
- 建议: floor 改用 `backend/app/flash/rules.latest_completed_trading_day`（两个增量脚本已在用），或用 zzshare db 的 MAX(date) 作为最近交易日。
- 置信度: 高

### [P3] scripts/publish_packs_local.py:283 护栏拦截路径直接 return，进程退出码为 0
- 原文: `                    return`
- 问题: 护栏 4 与护栏 1-3 的拦截都走普通 `return`（main 正常结束，exit 0）。
- 影响: 若被脚本/CI 链式调用（`update_xxx && publish`），拦截不会被感知为失败，调用方误以为发布成功。
- 建议: 拦截路径改 `sys.exit(1)`（或返回非零码），与两个增量脚本返回 2/5 的做法对齐。
- 置信度: 高

### 摘要 scripts/publish_packs_local.py
职责：把本地 backend/data/kline 下的 4 个包文件 + frontend/dist 以 git orphan 单提交 force push 到 gh-pages（GitHub Pages），发布前跑 4 道护栏（规模下限 / 同批日期一致 / 新鲜度 / 线上回滚）｜输入：--dir（默认 backend/data/kline）的 kline-pack-latest.json.gz、backend-pack.db.gz、indicators-pack.json.gz、realtime-quotes.json；frontend/dist；origin remote；线上 KLINE_URL（仅护栏 4 用）｜输出：gh-pages 单 commit force push（树中仅含本次文件）｜关键阈值：MIN_COUNTS kline≥1000 / indicators≥500 / backend≥500；日期下限=上一个工作日（北京时间，utcnow+8h）；回滚比对仅针对 kline 包；线上读取超时 60s、只解前 128KB｜依赖：git（本地 SSH 推送权）、纯标准库（gzip/sqlite3/zlib/urllib/tempfile），不 import backend｜已知坑：orphan 重建=整批覆盖，线上有而本地无的文件（含历史日期包、kline-delta-*.json）会被删除；护栏 4 只护 kline 包、读线上失败即跳过；dist 缺失提示语与实际行为矛盾；被拦截时退出码 0；护栏 4 在 --dry-run 下不执行（dry-run 结论可能与实跑不一致）；--force 只绕护栏 1-3、绕不过护栏 4（该交互本身符合设计）。

---

## scripts/generate-kline-pack.py

### [P2] scripts/generate-kline-pack.py:1025 包内/包名 date 用日历今天（Actions runner 上是 UTC），不是最近已完成交易日
- 原文: `    date_str = datetime.now().strftime("%Y%m%d")`
- 问题: 与 update_pack_incremental.py:324 已修复的口径（`expected` = 最近已完成交易日）不一致：盘中手动跑、周末跑、或 runner 时区非北京时，都会产出"日期是今天、数据止于上一交易日"的包。
- 影响: 下游按包 date 判断"缺一根/是否最新"会误判（正是 update_pack_incremental 注释里记载的 10-24 同款问题）；与后端包 date 混批时触发 publish 护栏 2 拦截。
- 建议: 改用 `latest_completed_trading_day(beijing_now())`（backend/app/flash/rules），与增量脚本统一。
- 置信度: 高

### [P2] scripts/generate-kline-pack.py:1029 "同日已生成即复用"防御不校验条目数，同日小样本包会被当作正式包采纳
- 原文: `    if (prev_data and prev_data.get("date") == date_str`
- 问题: 复用条件只看 prev 包 date==今天且 stocks 非空；若同日先跑过 export_browser_packs_from_local.py（118 只小样本包与正式包同名同目录，见 publish_packs_local.py 头注释），118 只会被当作"今日正式包"复用并写回 latest，完整性警告（与 prev 比较）也不会触发。
- 影响: 本地 latest 被小样本包覆盖；publish 护栏 1（≥1000）能拦住发布，但拦截后用户容易顺手加 --force。
- 建议: 复用分支增加条目数下限（如 ≥500，与 update_pack_incremental._load_prev 的校验一致）。
- 置信度: 高

### [P2] scripts/generate-kline-pack.py:1057 完整性不足只打印警告不中止，缩水包照常落盘
- 原文: `        if cur_n < prev_n * 0.8:`
- 问题: 本次条目数低于历史包 80% 时仅打印警告继续生成；generate_backend_pack.py 同场景是 `sys.exit(5)`（硬失败），两者口径不一致。且 publish 的 MIN_COUNTS=1000 拦不住"1564→1200（77%）"这类缩水。
- 影响: 被腾讯限流砍掉 20%+ 股票的包可静默上线，本地评分池缩小且无失败信号。
- 建议: 与后端包对齐：低于 80% 时以非零码退出（Actions 上可见失败），或把 publish 下限改为与"上一份包条目数"动态挂钩。
- 置信度: 高

### [P3] scripts/generate-kline-pack.py:331 股票池创业板只枚举到 301999，302xxx 新段缺失
- 原文: `    for i in range(300001, 302000):`
- 问题: 创业板新号段 302xxx 未纳入（generate_backend_pack.py:501 注释已把"300/301/302"并列表述，说明 302 段在用）。
- 影响: 302 段股票永远进不了前端 K 线包（本地榜缺席）；后端包靠快照/市值池部分覆盖，两边池子不一致。
- 建议: 上限改 303000（不存在的代码会被行情过滤自然剔除，无副作用）。
- 置信度: 中

### [P3] scripts/generate-kline-pack.py:869 cleanup_old_packs 的 "kline-" 前缀会误删 kline-failures.json
- 原文: `        if not filename.startswith("kline-"):`
- 问题: 失败黑名单文件名 kline-failures.json 同样以 "kline-" 开头；其 mtime 只在走全量拉取路径（_update_failures）时刷新，长期只跑增量（update_pack_incremental 每次都会调 gkp.generate_packs → cleanup_old_packs）超过 7 天即被删。
- 影响: 失败黑名单静默清零，退市/僵尸股重新进入池子逐只探测，浪费重试预算。
- 建议: 清理匹配改为明确前缀（kline-pack- / kline-delta-），或把 kline-failures.json 显式排除。
- 置信度: 高

### 摘要 scripts/generate-kline-pack.py
职责：全量生成前端 K 线包（kline-pack-YYYYMMDD.json.gz + kline-delta + latest 软链/副本），内置股票池枚举、批量行情、K 线拉取（令牌桶限速 / WAF 自适应退避 / 中途熔断）、失败黑名单、末根补齐｜输入：腾讯 qt.gtimg.cn 行情 + web.ifzq.gtimg.cn fqkline；可选 PACK_SOURCE=zzshare 本地库（全量模式自带"factor 口径不同"警告）；--quotes-file 共享行情（6h 内复用，0 请求）｜输出：--output-dir（默认 ./data/kline，cwd 相对；而 publish/增量脚本默认 backend/data/kline——本地手动全量跑容易产错目录）｜关键阈值：流通市值≥50亿、股价≥3 元、PE>0、K线≥30 根；--top 按总市值降序截取（0=不限制，作用于行情过滤后的池）；KLINE_COUNT_CAP=800；QPS 默认 1（下限 0.25）；WAF_MAX_EVENTS=30 主动报错；完整性 <80% 仅警告｜依赖：requests；可选 sqlite3；注释口径与 backend/app/tencent.py 对齐｜已知坑：date 用日历今天（runner 上为 UTC）；同日复用防御无条目数下限；创业板 302 段未枚举；cleanup 前缀波及 kline-failures.json；zzshare 全量模式仅限研究（脚本已自印警告）；--quotes-file 复用文件无来源校验（靠 6h mtime 窗口）。

---

## scripts/generate_backend_pack.py

### [P2] scripts/generate_backend_pack.py:632 pack_date 用日历今天（runner 上是 UTC），非最近已完成交易日
- 原文: `    date_str = datetime.now().strftime("%Y%m%d")`
- 问题: 与 update_pack_incremental.py:324 已修复的口径不一致；盘中/周末手动跑、或 runner 处于 UTC 早于北京日期的时段，meta pack_date 会超前于数据实际末日。
- 影响: 下游按 pack_date 判新鲜度（ensure_pack_fresh 类逻辑）会放行"日期新、数据旧"的包；与前端包混批发布时触发 publish 护栏 2。
- 建议: 改用 `latest_completed_trading_day(beijing_now())`，并显式固定北京时区语义。
- 置信度: 高

### 摘要 scripts/generate_backend_pack.py
职责：全量生成后端包 backend-pack.db.gz（SQLite：klines/indicators/codes/meta）+ indicators-pack.json.gz（指标 + 趋势健康度 + 消息分 + mainforce 标签）｜输入：共享行情（本地文件 6h 窗口 → Pages URL → 自拉，三级回退）；Supabase（kline_cache 清单、market_snapshot、backtest_prices、mainflow_history，均失败降级）；腾讯 K 线（1100 日历天，count 上限 800 ≈ 750 根交易日）｜输出：--output-dir 下 backend-pack.db.gz + indicators-pack.json.gz（pack_date 写入 meta）｜关键阈值：池 = 市值前 800 ∪ market_snapshot 池（总市值≥50亿、成交额≥1000万、剔 ST/科创板）∪ 回测既有池（豁免质量过滤）∪ 指数/ETF；质量过滤剔 ST、总市值<50亿（保留创业板与亏损股）；完整性 <80% sys.exit(5)、指标 0 只 sys.exit(6)；护栏基准 ind_count_raw 在 mainforce 合并前取（正确）｜依赖：backend 全量依赖（scoring/mainforce/eastmoney_news/psycopg2）、requests、backend/.env 加载｜已知坑：pack_date 用 datetime.now()；快照池仍剔 688（靠 cap/sb 池覆盖，与 10-11 决策自洽）；--max-codes 截断会把代码打回"回源 Supabase"状态（有日志提示）；共享行情复用无日期字段校验（仅 6h mtime）。

---

## scripts/update_pack_incremental.py

### [P2] scripts/update_pack_incremental.py:86 --url 参数被忽略，下载写死 PACK_URL
- 原文: `    r = requests.get(PACK_URL, timeout=90)`
- 问题: `_load_prev(pack_dir, url)` 接收了 `args.url` 却在函数体内使用模块常量 PACK_URL 下载。
- 影响: 传 --url 指定镜像/测试源无效，仍会请求官方 Pages URL，参数静默失效。
- 建议: 改为 `requests.get(url, timeout=90)`（使用函数入参）。
- 置信度: 高

### [P2] scripts/update_pack_incremental.py:266 大面积 mismatch / no_overlap / skipped 时无比例护栏，仍正常产包发布
- 原文: `          f"重叠不符 {mismatch} / 无重叠 {no_overlap} / 跳过 {len(skipped)}")`
- 问题: 异常股票一律"沿用上一份旧 bar"回填（第 313-315 行），包的股票数不变、date 字段前进到 expected；后续没有任何"异常占比过高则中止"的校验，publish 三道护栏（数量下限/日期一致/新鲜度）也全部放行。
- 影响: 若 zzshare 库口径整体变动（如重建后复权基准不同导致全池 mismatch），会产出"date 最新但全部股票数据停在旧日期"的包并直接发布，前端评分静默跑在旧数据上。
- 建议: mismatch + no_overlap + skipped 超过阈值（如 20%）时返回非零码拒绝产包（--dry-run 可先观察分布）。
- 置信度: 高

### [P3] scripts/update_pack_incremental.py:208 backfill 子进程 check=False，且合并不校验交易日连续性
- 原文: `            "--start", start, "--end", expected], check=False)`
- 问题: 回填失败被忽略（check=False）；随后 `_merge_one` 对"新增 bar"只判 `d > last_d`，不校验与上一根之间的交易日连续性——zzshare 中间缺某日而末日在时，会产出带洞的 K 线序列。
- 影响: MA/EMA/筹码等按序列计算的指标在缺口后整体错位，且包 date 仍显示最新。
- 建议: 合并完成后校验新增 bar 日期在预期交易日序列上连续（或至少"新增段天数与自然日跨度"合理性检查），不满足即该只回退腾讯/计入 skipped。
- 置信度: 中

### [P3] scripts/update_pack_incremental.py:129 factor 为空/0 的行会跳过因子校验直接追加
- 原文: `        if f and abs(float(f) / f_last - 1.0) > 1e-4:`
- 问题: docstring 宣称"因子校验：缺口内 factor 不得变化"，但 `if f and` 使 factor 缺失（NULL/0）的行不经校验即按原始价追加。
- 影响: 若 zzshare 个别行 factor 缺失而实际发生除权，该股 qfq 序列出现口径断裂，且不会被送去 _merge_exdiv 重算。
- 建议: f 为空时按"存疑"处理（该只回退腾讯或计入 skipped），不要静默追加。
- 置信度: 中

### 摘要 scripts/update_pack_incremental.py
职责：前端 K 线包增量更新——历史保留上一份腾讯包，仅从 zzshare 追加新交易日 bar；除权股按 factor 比例重算（_merge_exdiv）或可选回退腾讯｜输入：本地 kline-pack-latest.json.gz（<100KB 或 <500 只拒用，回退 Pages 下载）；data/zzshare_daily.db；backend/app/flash.rules 的最近已完成交易日；scripts/etf_index_daily.py（ETF/指数兜底）｜输出：经 gkp.generate_packs 写 kline-pack-YYYYMMDD.json.gz（date=最近已完成交易日，10-24 已修）+ latest + delta｜关键阈值：重叠日收盘容差 0.5%；因子变化容差 1e-4；保留 150 根；zzshare 未就绪 return 2（交 workflow 重试）；本地小样本 <500 只拒用｜依赖：requests、sqlite3、backend（app.flash.rules）、importlib 加载 generate-kline-pack.py、etf_index_daily｜已知坑：--url 参数不生效；mismatch/no_overlap/skipped 无比例护栏；股票池冻结（增量不新增股票，name/market_cap 永远沿用旧包）；backfill check=False 且不校验日期连续性；factor 空值跳过校验；_merge_exdiv 的乘性缩放与腾讯 qfq 存在 ~1% 口径差（脚本自认）。

---

## scripts/update_backend_pack_incremental.py

### [P1] scripts/update_backend_pack_incremental.py:342 包内 date 仍用 beijing_now() 日历今天——2026-10-24 在 update_pack_incremental.py 修掉的同一问题这里没修
- 原文: `    date_str = beijing_now().strftime("%Y%m%d")`
- 问题: update_pack_incremental.py:321-324 已改为"最近已完成交易日"并写明原因（"周六手动重跑会写出 kline-pack-20261024.json.gz（数据只到 10-23）"），本脚本重写 backend 包时仍用日历今天。
- 影响: 周末/节假日/盘中运行时 pack_date 超前于数据末日（下游按日期判"缺一根"误判）；且与同批发布的前端包（date=交易日）不一致，publish 护栏 2 必然拦截、逼用户加 --force，--force 又会连护栏 1/3 一起绕过。
- 建议: 与 update_pack_incremental.py:324 对齐，改用 `expected.replace("-", "")`。
- 置信度: 高

### [P3] scripts/update_backend_pack_incremental.py:332 `len(merged) < len(prev_bars) * 0.8` 是死条件，护栏永远只靠 `not ind_out`
- 原文: `    if len(merged) < len(prev_bars) * 0.8 or not ind_out:`
- 问题: 合并循环对每个 prev 代码在所有分支（含 skipped/exdiv 失败）都写入 merged，末尾还有 setdefault 兜底（第 300-301 行），len(merged) 恒等于 len(prev_bars)，前半个条件不可达。
- 影响: 日志/注释暗示有"K线缩水护栏"，实际不存在；真正的异常（如全部 skipped）只体现在打印统计里，不会中止。
- 建议: 把护栏改为"有效率"校验（如 updated + n_rebase 占比过低且 target>prev 时告警或中止），或删掉死条件避免误信。
- 置信度: 高

### [P3] scripts/update_backend_pack_incremental.py:219 "已是最新"分支只补 indicators-pack.json.gz，不检查 backend-pack.db.gz 是否在本地
- 原文: `        if not os.path.exists(ind_gz):`
- 问题: 该分支的设计意图是"把发布所需文件补齐"（第 217 行注释），但只处理 indicators 包；当 _load_prev_db 走缓存命中路径（_backend_prev.db 有效即返回，第 120-131 行）而目录里没有 backend-pack.db.gz 时，脚本以 0 退出且 gz 缺失。
- 影响: 随后执行 publish（两个脚本的 docstring 都如此指示）时，backend-pack.db.gz 走"[跳过]"路径，orphan 重建把线上后端包整批删除（与 publish_packs_local.py:226 的问题叠加）。
- 建议: 该分支对 backend-pack.db.gz 做同样的存在性检查，缺失时从 Pages 补下。
- 置信度: 高

### 摘要 scripts/update_backend_pack_incremental.py
职责：后端包增量更新——SQLite 上一份包 + zzshare 新 bar 合并，重算全部指标与趋势健康度，mainforce/news_score 从上一份 indicators 原样继承，重写 backend-pack.db.gz + indicators-pack.json.gz｜输入：_backend_prev.db 缓存（klines>50 万行即用）→ 本地 gz（>5MB 且 >50 万行）→ Pages 下载（下载时保留 gz 供发布）三级回退；zzshare db；etf_index_daily｜输出：backend-pack.db.gz（pack_date=beijing_now 日历今天）+ indicators-pack.json.gz｜关键阈值：缓存/本地有效性 = klines 行数 >500000；zzshare 未就绪 return 2；护栏 `len(merged)<0.8*prev or not ind_out` 返回 5（前半为死代码）；BARS_MAX=750｜依赖：backend（app.flash.rules、app.scoring.indicator_cache）、update_pack_incremental（复用 _zz_code/_zz_max_date/_merge_one）、generate_backend_pack（write_sqlite/write_indicators_pack/compute_trend_health_batch）、etf_index_daily｜已知坑：date 用 beijing_now（10-24 同款问题未修）；"已是最新"分支不查 backend gz；_backend_prev.db 缓存只验行数不验 pack_date，陈旧缓存被长期复用（除权重算按更早基准重做，累积 ~1% 口径差）；mainforce/news_score 永久继承旧值（news 是 24h 衰减的时间敏感分数，增量模式下不衰减不更新）；写包顺序（先 sqlite → gz → indicators）中途失败会留下日期不一致的包（publish 护栏 2 可拦）。

---

## 未发现问题

- publish_packs_local.py 的 --force / --allow-downgrade 交互本身符合设计意图：--force 只绕护栏 1-3，护栏 4（线上回滚）必须 --allow-downgrade，且 --dry-run 下不做网络检查；对"本地 kline 包日期 < 线上"这一 10-24 事故主路径，护栏 4 在本地文件存在且网络可达时确实有效。
- update_pack_incremental.py 的重叠校验设计正确：以"上一份包末日"为重叠锚点、0.5% 收盘容差验证腾讯 qfq 末日=原始收盘的锚定假设、因子变化即整只回退（返回 prev_bars 而非部分合并结果，不会留下半截追加）、停牌/零成交量不造假 bar。
- 两个增量脚本"zzshare 未发布目标日线即 return 2"的前置检查，正确阻止了"包日期新、数据旧"的产出；--backfill 从数据末日（而非 2005）起补，请求数可控。
- generate_backend_pack.py 的完整性护栏实现正确：<80% exit 5、指标 0 只 exit 6、护栏基准 ind_count_raw 在 mainforce/news 合并之前快照（避免了"补挂标签"干扰判据的时序坑）。
- 增量脚本失败中断后的包完整性可接受：所有文件写入集中在末尾（先写完整包再替换 latest，latest 损坏时下轮有 Pages 回退与 <100KB/行数校验自愈），不会留下"latest 指向半截包"的状态。
- generate-kline-pack.py 的 --top 语义（行情质量过滤后按总市值降序截取、0=不限制）与帮助文本一致；数据全部来自实时接口或 zzshare 本地库，未发现混入测试/样本数据的路径（小样本风险集中在"同日复用"与 export_browser_packs 的同名残留，见上文 P2 条目）。
