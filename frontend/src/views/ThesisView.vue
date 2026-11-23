<template>
  <div class="fade-in">
    <!-- 头部 -->
    <div class="bg-card border border-border rounded-lg p-4 mb-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">推演复盘</h1>
          <p class="text-xs text-muted mt-0.5">
            周末登记情景 / 概率 / 验证项 → 每日收盘自动对照实际数据 → LLM 修正概率。
            纯复盘，不进决策链。
          </p>
        </div>
        <div class="flex gap-2">
          <button @click="openCreate"
            class="px-3 py-1.5 rounded text-xs bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors">
            ＋ 登记推演
          </button>
          <button @click="openImport"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors">
            ⇪ 导入文本
          </button>
          <button @click="reload" :disabled="loading"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
            {{ loading ? '加载中…' : '刷新' }}
          </button>
        </div>
      </div>
    </div>

    <div class="flex gap-4 items-start">
      <!-- ── 左：推演列表 ── -->
      <div class="w-64 shrink-0 space-y-2">
        <div class="flex items-center justify-between px-1">
          <span class="text-xs text-muted">推演（{{ list.length }}）</span>
          <label class="flex items-center gap-1 text-[10px] text-muted cursor-pointer">
            <input type="checkbox" v-model="activeOnly" @change="loadList" class="accent-accent rounded" />
            仅有效期内
          </label>
        </div>

        <div v-if="!list.length" class="bg-card border border-border rounded-lg p-6 text-center text-xs text-muted">
          还没有登记任何推演<br />
          <span class="text-[10px]">点右上角「＋ 登记推演」</span>
        </div>

        <div v-for="t in list" :key="t.id" @click="select(t)"
          class="bg-card border rounded-lg p-3 cursor-pointer transition-colors"
          :class="current?.thesis?.id === t.id
            ? 'border-accent/50 bg-accent/5'
            : 'border-border hover:border-accent/30'">
          <div class="flex items-start justify-between gap-2">
            <div class="text-xs font-medium text-gray-100 leading-snug">{{ t.title }}</div>
            <span class="shrink-0 px-1.5 py-0.5 rounded text-[10px] border"
              :class="statusClass(t.status)">{{ t.status === 'active' ? '有效' : '归档' }}</span>
          </div>
          <div class="text-[10px] text-muted mt-1">
            {{ t.horizon_start }} ~ {{ t.horizon_end }}
          </div>
          <div class="flex gap-1 mt-1.5 flex-wrap">
            <span v-for="s in t.scenarios" :key="s.name"
              class="px-1.5 py-0.5 rounded text-[10px] bg-bg border border-border text-muted">
              {{ s.name }} {{ s.prob }}%
            </span>
          </div>
          <div class="text-[10px] text-muted mt-1.5">
            验证项 {{ (t.checks || []).length }} 条 · 证伪 {{ (t.invalidators || []).length }} 条
          </div>
        </div>
      </div>

      <!-- ── 右：详情 ── -->
      <div class="flex-1 min-w-0">
        <div v-if="error" class="bg-card border border-red-500/30 rounded-lg p-6 text-sm text-red-400 mb-4">
          {{ error }}
        </div>

        <div v-if="!current" class="bg-card border border-border rounded-lg p-10 text-center text-xs text-muted">
          左侧选一份推演查看逐日对照
        </div>

        <div v-else class="space-y-4">
          <!-- 概况 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <h2 class="text-base font-bold text-gray-100">{{ current.thesis.title }}</h2>
                <div class="text-[11px] text-muted mt-1">
                  {{ current.thesis.horizon_start }} ~ {{ current.thesis.horizon_end }}
                  · 登记于 {{ (current.thesis.created_at || '').slice(0, 16) }}
                  · <span :class="statusClass(current.thesis.status)">
                      {{ current.thesis.status === 'active' ? '有效' : '已归档' }}
                    </span>
                </div>
              </div>
              <div class="flex gap-2">
                <button @click="rerun(todayStr())"
                  :disabled="running"
                  class="px-2.5 py-1 rounded text-[11px] border border-accent/40 text-accent hover:bg-accent/10 transition-colors disabled:opacity-50">
                  {{ running ? '对照中…' : '↻ 今日对照' }}
                </button>
                <button v-if="current.thesis.status === 'active'" @click="doArchive"
                  :disabled="archiving"
                  class="px-2.5 py-1 rounded text-[11px] border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
                  {{ archiving ? '归档中…' : '归档' }}
                </button>
              </div>
            </div>

            <!-- 情景概率 -->
            <div class="mt-4 space-y-2">
              <div class="text-[11px] text-muted">情景（登记时）</div>
              <div v-for="s in current.thesis.scenarios" :key="s.name" class="flex items-start gap-3">
                <!-- ★ 2026-11-23：情景名宽度 112px(w-28) 太窄，"A 指数补跌、广度修复" 会折行 ⇒ 定 150px -->
                <div class="shrink-0 text-xs text-gray-200 leading-snug" style="width:150px">{{ s.name }}</div>
                <div class="w-14 shrink-0 text-xs text-accent text-right">{{ s.prob }}%</div>
                <div class="flex-1 min-w-0">
                  <div class="h-1.5 bg-bg rounded overflow-hidden">
                    <div class="h-full bg-accent/60" :style="{ width: Math.min(100, s.prob) + '%' }"></div>
                  </div>
                  <div v-if="s.path" class="text-[10px] text-muted mt-1 leading-snug">{{ s.path }}</div>
                </div>
              </div>
            </div>

            <!-- 验证项 -->
            <div class="mt-4">
              <div class="text-[11px] text-muted mb-1.5">验证项（{{ current.thesis.checks.length }}）</div>
              <div class="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                <div v-for="(c, i) in current.thesis.checks" :key="i"
                  class="flex items-center gap-2 text-[11px] bg-bg border border-border rounded px-2 py-1.5">
                  <span class="text-gray-200">{{ c.label || labelOf(c.key) }}</span>
                  <span class="text-muted">{{ c.op }}</span>
                  <span class="text-accent font-mono">{{ c.value }}</span>
                </div>
              </div>
            </div>

            <!-- 证伪条件 -->
            <div v-if="current.thesis.invalidators?.length" class="mt-4">
              <div class="text-[11px] text-muted mb-1.5">证伪条件</div>
              <ul class="space-y-1">
                <li v-for="(s, i) in current.thesis.invalidators" :key="i"
                  class="text-[11px] text-muted flex gap-1.5">
                  <span class="text-amber-400 shrink-0">·</span><span>{{ s }}</span>
                </li>
              </ul>
            </div>
          </div>

          <!-- 今日未跑提示 -->
          <div v-if="!hasToday" class="bg-card border border-amber-500/30 rounded-lg p-4">
            <div class="flex items-center justify-between gap-3">
              <div class="text-xs text-amber-400">
                今日（{{ todayStr() }}）还没有对照记录
                <span class="text-[10px] text-muted block mt-0.5">
                  日批会在收盘后自动跑；想立刻看结果点右侧按钮（会调 1 次 LLM，约十几秒）
                </span>
              </div>
              <button @click="rerun(todayStr())" :disabled="running"
                class="shrink-0 px-3 py-1.5 rounded text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 transition-colors disabled:opacity-50">
                {{ running ? '对照中…' : '立即对照' }}
              </button>
            </div>
          </div>

          <!-- 逐日时间线 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-3">
              <h3 class="text-sm font-bold text-gray-100">逐日对照</h3>
              <span class="text-[10px] text-muted">最近 {{ current.data.length }} 天</span>
            </div>

            <div v-if="!current.data.length" class="text-xs text-muted py-6 text-center">
              还没有对照记录。日批收盘后自动生成，也可以点「今日对照」手动跑一次。
            </div>

            <div v-else class="space-y-3">
              <div v-for="d in current.data" :key="d.date"
                class="border border-border rounded-lg p-3">
                <!-- 日期行 -->
                <div class="flex items-center justify-between gap-2 flex-wrap">
                  <div class="flex items-center gap-2">
                    <span class="text-xs font-mono text-gray-100">{{ d.date }}</span>
                    <span v-if="d.scenario_now"
                      class="px-1.5 py-0.5 rounded text-[10px] bg-accent/15 text-accent border border-accent/25">
                      {{ d.scenario_now }}
                    </span>
                    <span v-if="d.pushed" class="text-[10px] text-muted">已推送</span>
                  </div>
                  <div class="flex items-center gap-1.5 text-[10px]">
                    <span class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">
                      命中 {{ count(d, 'pass') }}
                    </span>
                    <span class="px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/20">
                      偏离 {{ count(d, 'fail') }}
                    </span>
                    <span class="px-1.5 py-0.5 rounded bg-bg text-muted border border-border">
                      无数据 {{ count(d, 'nodata') }}
                    </span>
                    <button @click="rerun(d.date)" :disabled="running"
                      class="px-1.5 py-0.5 rounded border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
                      重跑
                    </button>
                  </div>
                </div>

                <!-- 逐项 -->
                <div class="mt-2 grid grid-cols-1 md:grid-cols-2 gap-1">
                  <div v-for="(c, i) in d.checked" :key="i"
                    class="flex items-center gap-1.5 text-[11px] px-1.5 py-1 rounded"
                    :class="verdictBg(c.verdict)">
                    <span class="shrink-0 w-3 text-center" :class="verdictColor(c.verdict)">
                      {{ verdictMark(c.verdict) }}
                    </span>
                    <span class="text-gray-200 truncate">{{ c.label }}</span>
                    <span class="text-muted shrink-0">{{ c.op }} {{ c.target }}</span>
                    <span class="ml-auto shrink-0" :class="c.actual === null ? 'text-muted' : 'text-gray-100 font-mono'">
                      {{ c.actual === null ? '—' : c.actual }}
                    </span>
                  </div>
                </div>
                <div v-if="d.checked?.some(c => c.note)" class="mt-1 text-[10px] text-muted">
                  {{ d.checked.filter(c => c.note).map(c => c.label + '：' + c.note).join('；') }}
                </div>

                <!-- LLM 修正 -->
                <div v-if="d.llm" class="mt-2 pt-2 border-t border-border/60 space-y-1.5">
                  <div v-if="d.llm.progress" class="text-[11px] text-gray-200">
                    <span class="text-muted">兑现进度：</span>{{ d.llm.progress }}
                  </div>

                  <div v-if="probList(d.llm).length" class="flex items-center gap-2 flex-wrap text-[10px]">
                    <span class="text-muted">概率修正：</span>
                    <span v-for="p in probList(d.llm)" :key="p.name"
                      class="px-1.5 py-0.5 rounded bg-bg border border-border">
                      <span class="text-gray-300">{{ p.name }}</span>
                      <span class="text-muted ml-1">{{ p.from }}%</span>
                      <span class="mx-0.5" :class="p.delta > 0 ? 'text-rise' : (p.delta < 0 ? 'text-fall' : 'text-muted')">
                        → {{ p.to }}%
                      </span>
                    </span>
                  </div>

                  <div v-if="d.llm.invalidated?.length" class="text-[11px]">
                    <span class="text-red-400">已被证伪：</span>
                    <span class="text-muted">{{ d.llm.invalidated.join('；') }}</span>
                  </div>

                  <div v-if="d.llm.next_watch?.length" class="text-[11px]">
                    <span class="text-muted">接下来盯：</span>
                    <span class="text-gray-200">{{ d.llm.next_watch.join('；') }}</span>
                  </div>

                  <div v-if="d.llm.falsifier" class="text-[11px] bg-amber-500/5 border border-amber-500/20 rounded px-2 py-1.5">
                    <span class="text-amber-400">什么情况说明这次修正也错了：</span>
                    <span class="text-muted">{{ d.llm.falsifier }}</span>
                  </div>
                </div>
                <div v-else class="mt-2 text-[10px] text-muted">
                  无 LLM 修正（未配置 / 调用失败）；确定性对照已落库。
                </div>

                <!-- 完整推演文本（落库全文；企微只发精简版） -->
                <div v-if="d.report_text" class="mt-2 pt-2 border-t border-border/60">
                  <div class="flex items-center justify-between">
                    <button @click="toggleText(d.date)"
                      class="text-[10px] text-muted hover:text-gray-200 transition-colors">
                      {{ textOpen === d.date ? '收起推演文本 ▲' : '查看推演文本 ▼' }}
                    </button>
                    <button v-if="textOpen === d.date" @click="copyText(d)"
                      class="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted hover:text-gray-200 transition-colors">
                      {{ textCopied === d.date ? '已复制' : '复制' }}
                    </button>
                  </div>
                  <pre v-if="textOpen === d.date"
                    class="mt-1.5 text-[10px] leading-relaxed text-gray-300 bg-bg border border-border rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono">{{ d.report_text }}</pre>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 导入弹层（只解析 → 回填表单，不写库）── -->
    <div v-if="showImport"
      class="fixed inset-0 z-50 bg-black/60 flex items-start justify-center overflow-y-auto p-4 md:p-8"
      @click.self="showImport = false">
      <div class="w-full max-w-2xl bg-card border border-border rounded-lg p-5">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-base font-bold text-gray-100">导入推演文本</h2>
          <button @click="showImport = false"
            class="text-muted hover:text-gray-200 text-lg leading-none">×</button>
        </div>

        <p class="text-[11px] text-muted leading-relaxed mb-3">
          粘贴任意格式的推演（也可以直接让 LLM 按下面格式生成），点「解析」后自动填入登记表单。
          <span class="text-amber-400">解析只回填、不写库</span>，你改完数字再点「登记」。
        </p>

        <!-- 格式示例 -->
        <div class="border border-border rounded-lg overflow-hidden mb-3">
          <div class="flex items-center justify-between px-2.5 py-1.5 bg-bg border-b border-border">
            <span class="text-[11px] text-muted">格式示例</span>
            <button @click="copySample"
              class="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted hover:text-gray-200 transition-colors">
              {{ sampleCopied ? '已复制' : '复制示例' }}
            </button>
          </div>
          <pre class="px-2.5 py-2 text-[11px] leading-relaxed text-gray-300 overflow-x-auto whitespace-pre-wrap font-mono">{{ SAMPLE_TEXT }}</pre>
        </div>

        <textarea v-model="importText" rows="10" placeholder="把推演文本粘贴到这里…"
          class="w-full bg-bg border border-border rounded px-2.5 py-2 text-xs text-gray-200 leading-relaxed focus:outline-none focus:border-accent/50"></textarea>

        <div v-if="parseWarn.length"
          class="mt-2 bg-amber-500/5 border border-amber-500/20 rounded px-2.5 py-2">
          <div class="text-[11px] text-amber-400 mb-1">解析提示（登记前请确认）</div>
          <ul class="space-y-0.5">
            <li v-for="(w, i) in parseWarn" :key="i" class="text-[11px] text-muted">· {{ w }}</li>
          </ul>
        </div>

        <div v-if="parseError" class="mt-2 text-xs text-red-400">{{ parseError }}</div>

        <div class="flex justify-end gap-2 mt-4">
          <button @click="showImport = false"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200">取消</button>
          <button @click="doParse" :disabled="parsing"
            class="px-4 py-1.5 rounded text-xs bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors disabled:opacity-50">
            {{ parsing ? '解析中…（十几秒）' : '解析并填入表单' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ── 登记弹层 ── -->
    <div v-if="showCreate" class="fixed inset-0 z-50 bg-black/60 flex items-start justify-center overflow-y-auto p-4 md:p-8"
      @click.self="showCreate = false">
      <div class="w-full max-w-3xl bg-card border border-border rounded-lg p-5">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-base font-bold text-gray-100">登记推演</h2>
          <button @click="showCreate = false" class="text-muted hover:text-gray-200 text-lg leading-none">×</button>
        </div>

        <div class="space-y-4">
          <!-- 标题 + 有效期 -->
          <div>
            <label class="text-[11px] text-muted block mb-1">标题</label>
            <input v-model="form.title" placeholder="例如：11-24 这一周：缩量磨底还是破位下杀"
              class="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-accent/50" />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-[11px] text-muted block mb-1">开始</label>
              <input v-model="form.horizon_start" type="date"
                class="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-accent/50" />
            </div>
            <div>
              <label class="text-[11px] text-muted block mb-1">结束</label>
              <input v-model="form.horizon_end" type="date"
                class="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-accent/50" />
            </div>
          </div>

          <!-- 情景 -->
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="text-[11px] text-muted">情景与概率</label>
              <div class="flex items-center gap-2">
                <span class="text-[11px]" :class="Math.abs(probTotal - 100) <= 1 ? 'text-emerald-400' : 'text-red-400'">
                  合计 {{ probTotal }}%
                </span>
                <button @click="addScenario"
                  class="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted hover:text-gray-200">＋</button>
              </div>
            </div>
            <div v-for="(s, i) in form.scenarios" :key="'sc' + i" class="flex gap-1.5 mb-1.5">
              <input v-model="s.name" placeholder="情景名"
                class="w-32 shrink-0 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50" />
              <input v-model.number="s.prob" type="number" min="0" max="100" placeholder="%"
                class="w-16 shrink-0 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50" />
              <input v-model="s.path" placeholder="路径描述（可选）"
                class="flex-1 min-w-0 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50" />
              <button @click="form.scenarios.splice(i, 1)"
                class="shrink-0 px-1.5 text-muted hover:text-red-400">×</button>
            </div>
          </div>

          <!-- 验证项 -->
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="text-[11px] text-muted">验证项（取不到数据会标「无数据」，绝不填 0）</label>
              <button @click="addCheck"
                class="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted hover:text-gray-200">＋</button>
            </div>
            <div v-for="(c, i) in form.checks" :key="'ck' + i" class="flex gap-1.5 mb-1.5">
              <select v-model="c.key"
                class="w-40 shrink-0 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50">
                <option v-for="m in metrics" :key="m.key" :value="m.key">{{ m.label }}</option>
              </select>
              <select v-model="c.op"
                class="w-16 shrink-0 bg-bg border border-border rounded px-1 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50">
                <option v-for="o in OPS" :key="o" :value="o">{{ o }}</option>
              </select>
              <input v-model.number="c.value" type="number" step="0.01" placeholder="阈值"
                class="w-28 shrink-0 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50" />
              <span class="flex-1 min-w-0 text-[10px] text-muted self-center">
                例：{{ hintOf(c.key) }}
              </span>
              <button @click="form.checks.splice(i, 1)"
                class="shrink-0 px-1.5 text-muted hover:text-red-400">×</button>
            </div>
          </div>

          <!-- 证伪条件 -->
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="text-[11px] text-muted">证伪条件（什么情况说明这次推演整体错了）</label>
              <button @click="form.invalidators.push('')"
                class="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted hover:text-gray-200">＋</button>
            </div>
            <div v-for="(s, i) in form.invalidators" :key="'iv' + i" class="flex gap-1.5 mb-1.5">
              <input v-model="form.invalidators[i]" placeholder="例如：放量突破 3600 且连续 3 日站稳"
                class="flex-1 min-w-0 bg-bg border border-border rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50" />
              <button @click="form.invalidators.splice(i, 1)"
                class="shrink-0 px-1.5 text-muted hover:text-red-400">×</button>
            </div>
          </div>
        </div>

        <div v-if="formError" class="mt-3 text-xs text-red-400">{{ formError }}</div>

        <div class="flex justify-end gap-2 mt-4">
          <button @click="showCreate = false"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200">取消</button>
          <button @click="submit" :disabled="saving"
            class="px-4 py-1.5 rounded text-xs bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors disabled:opacity-50">
            {{ saving ? '提交中…' : '登记' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  getThesisScenarios, createThesisScenario, getThesisDaily,
  rerunThesisReview, archiveThesisScenario, getThesisMetrics,
  parseThesisText,
} from '../api'

const OPS = ['<=', '>=', '<', '>', '==']

// 导入功能的格式示例（也可以直接丢给 LLM 让它按这个格式生成）
const SAMPLE_TEXT = `【推演】11-23 这周：广度背离能否收敛
有效期：2026-11-23 ~ 2026-11-27

情景：
- A 指数补跌、广度修复：35%
- B 背离继续扩大、结构恶化：40%
- C 缩量僵持、个股继续失血：25%

验证项：
- 上证涨跌% <= -1
- 下跌占比 >= 0.85
- 广度背离 >= 2
- 两市成交额 <= 12000

证伪条件：
- 放量（>15000 亿）站上 3580，且下跌占比 < 0.5`

// 阈值示例：只是提示，不参与计算
const HINTS = {
  sh_close: '如 3500（上证收盘）',
  sh_chg_pct: '如 -1（当日涨跌 %）',
  amount_yi: '如 8000（两市成交额亿）',
  limit_down: '如 100（跌停家数）',
  limit_up: '如 30（涨停家数）',
  down_ratio: '如 0.6（下跌占比 0~1）',
  breadth_gap: '如 2（广度背离 pp）',
}

const list = ref([])
const current = ref(null)      // { thesis, data }
const metrics = ref([])
const loading = ref(false)
const error = ref('')
const activeOnly = ref(false)
const running = ref(false)
const archiving = ref(false)

const showCreate = ref(false)
const saving = ref(false)
const formError = ref('')
const form = ref(blankForm())

// ── 导入（粘贴自然语言 → LLM 解析 → 回填表单，不直接写库）──
const showImport = ref(false)
const importText = ref('')
const parsing = ref(false)
const parseError = ref('')
const parseWarn = ref([])
const sampleCopied = ref(false)

// 完整推演文本：默认收起（太长），按日期展开
const textOpen = ref(null)
const textCopied = ref('')

// ★ 2026-11-22（审查修复）：原来写成 `computed(() => fmtDate(new Date()))` ——
//   它**没有任何响应式依赖** ⇒ Vue 只算一次就缓存，页面跨过午夜后仍返回昨天的日期，
//   "今日未跑"提示和「立即对照」传的日期都会错（后端 `_default_day()` 反而是准的，
//   于是出现"前端说今天没跑、后端却按今天跑了"的错位）。
//   改成普通函数：每次调用取当前北京时间。
function todayStr() {
  return fmtDate(new Date())
}
const hasToday = computed(() =>
  (current.value?.data || []).some(d => d.date === todayStr()))

const probTotal = computed(() =>
  form.value.scenarios.reduce((a, s) => a + (Number(s.prob) || 0), 0))

function pad(n) { return String(n).padStart(2, '0') }
function fmtDate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function blankForm() {
  const start = new Date()
  const end = new Date(start.getTime() + 6 * 86400000)
  return {
    title: '',
    horizon_start: fmtDate(start),
    horizon_end: fmtDate(end),
    scenarios: [
      { name: '', prob: 40, path: '' },
      { name: '', prob: 35, path: '' },
      { name: '', prob: 25, path: '' },
    ],
    checks: [{ key: 'sh_close', op: '<=', value: null }],
    invalidators: [''],
  }
}

function statusClass(s) {
  return s === 'active'
    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/20'
    : 'bg-bg text-muted border-border'
}

function verdictMark(v) { return v === 'pass' ? '✓' : (v === 'fail' ? '✗' : '—') }
function verdictColor(v) {
  return v === 'pass' ? 'text-emerald-400' : (v === 'fail' ? 'text-red-400' : 'text-muted')
}
function verdictBg(v) {
  return v === 'pass' ? 'bg-emerald-500/5' : (v === 'fail' ? 'bg-red-500/5' : '')
}
function count(d, v) {
  return (d.checked || []).filter(c => c.verdict === v).length
}
function labelOf(key) {
  return (metrics.value.find(m => m.key === key) || {}).label || key
}
function hintOf(key) { return HINTS[key] || '' }

// LLM 的 probs 是 {情景名: 新概率}，跟登记值对比出 delta
function probList(llm) {
  const probs = llm?.probs
  const base = current.value?.thesis?.scenarios || []
  if (!probs || typeof probs !== 'object') return []
  return Object.entries(probs).map(([name, to]) => {
    const hit = base.find(s => s.name === name)
    const from = hit ? hit.prob : null
    const toNum = Number(to)
    return {
      name,
      from: from === null ? '?' : from,
      to: isNaN(toNum) ? to : Math.round(toNum),
      delta: from === null || isNaN(toNum) ? 0 : Math.round(toNum) - from,
    }
  })
}

async function loadMetrics() {
  try {
    const { data } = await getThesisMetrics()
    metrics.value = data?.data || []
  } catch (e) {
    console.error('loadMetrics error', e)
  }
}

async function loadList() {
  try {
    const { data } = await getThesisScenarios({ active_only: activeOnly.value ? 1 : undefined })
    list.value = data?.data || []
    // 列表刷新后保持当前选中项（若已不在列表里则清空）
    if (current.value) {
      const still = list.value.find(t => t.id === current.value.thesis.id)
      if (!still) current.value = null
    }
  } catch (e) {
    error.value = '加载列表失败：' + (e.response?.data?.detail || e.message)
  }
}

async function select(t) {
  error.value = ''
  try {
    const { data } = await getThesisDaily(t.id, 30)
    current.value = { thesis: data.thesis, data: data.data || [] }
  } catch (e) {
    error.value = '加载详情失败：' + (e.response?.data?.detail || e.message)
  }
}

async function reload() {
  loading.value = true
  error.value = ''
  try {
    await loadList()
    if (current.value) {
      const t = list.value.find(x => x.id === current.value.thesis.id) || current.value.thesis
      await select(t)
    }
  } finally {
    loading.value = false
  }
}

async function rerun(date) {
  if (!current.value) return
  running.value = true
  error.value = ''
  try {
    await rerunThesisReview(current.value.thesis.id, date)
    await loadList()
    await select(current.value.thesis)
  } catch (e) {
    error.value = '对照失败：' + (e.response?.data?.detail || e.message)
  } finally {
    running.value = false
  }
}

async function doArchive() {
  if (!current.value) return
  if (!confirm(`归档「${current.value.thesis.title}」？归档后不再参与每日复盘。`)) return
  archiving.value = true
  try {
    await archiveThesisScenario(current.value.thesis.id)
    await loadList()
    await select(current.value.thesis)
  } catch (e) {
    error.value = '归档失败：' + (e.response?.data?.detail || e.message)
  } finally {
    archiving.value = false
  }
}

function openCreate() {
  form.value = blankForm()
  formError.value = ''
  showCreate.value = true
}

// ── 导入：解析结果只回填表单，用户审阅/改数字后自己点「登记」──
function openImport() {
  importText.value = ''
  parseError.value = ''
  parseWarn.value = []
  showImport.value = true
}

async function doParse() {
  parseError.value = ''
  parseWarn.value = []
  if (!importText.value.trim()) {
    parseError.value = '请先粘贴推演文本'
    return
  }
  parsing.value = true
  try {
    const { data } = await parseThesisText(importText.value)
    const d = data.draft || {}
    form.value = {
      title: d.title || '',
      horizon_start: d.horizon_start || fmtDate(new Date()),
      horizon_end: d.horizon_end || fmtDate(new Date()),
      scenarios: (d.scenarios || []).length
        ? d.scenarios.map(s => ({ name: s.name, prob: s.prob, path: s.path || '' }))
        : [{ name: '', prob: 100, path: '' }],
      checks: (d.checks || []).length
        ? d.checks.map(c => ({ key: c.key, op: c.op, value: c.value }))
        : [{ key: 'sh_close', op: '<=', value: null }],
      invalidators: (d.invalidators || []).length ? d.invalidators : [''],
    }
    parseWarn.value = data.warnings || []
    showImport.value = false
    showCreate.value = true
    formError.value = ''
  } catch (e) {
    parseError.value = '解析失败：' + (e.response?.data?.detail || e.message)
  } finally {
    parsing.value = false
  }
}

// ★ 2026-11-23 bug：点「复制」提示"复制失败，请手动选中文本"。
//   根因：`navigator.clipboard` 只在**安全上下文**（https / localhost）才存在。
//   线上是 `http://101.201.222.167`（明文 http）⇒ `navigator.clipboard` 为
//   undefined，`navigator.clipboard.writeText` 直接抛 TypeError 被 catch 住。
//   兜底：隐藏 textarea + `document.execCommand('copy')`。
//   execCommand 虽已废弃，但它是非安全上下文下**唯一**能用的方案。
async function copyToClipboard(text) {
  const t = String(text || '')
  if (!t) throw new Error('empty')

  if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(t)
      return
    } catch (e) {
      // 权限被拒 / 焦点丢失 —— 落到下面的兜底
    }
  }

  const ta = document.createElement('textarea')
  ta.value = t
  ta.setAttribute('readonly', '')
  ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0'
  document.body.appendChild(ta)

  const prevSel = window.getSelection()
  const prevRange = prevSel && prevSel.rangeCount ? prevSel.getRangeAt(0) : null

  let ok = false
  try {
    ta.select()
    ta.setSelectionRange(0, t.length)
    ok = document.execCommand('copy')
  } catch (e) {
    ok = false
  }
  document.body.removeChild(ta)

  // 恢复用户原来的选区（否则复制后光标会跳到页面顶部）
  if (prevRange && prevSel) {
    prevSel.removeAllRanges()
    prevSel.addRange(prevRange)
  }
  if (!ok) throw new Error('copy failed')
}

async function copySample() {
  try {
    await copyToClipboard(SAMPLE_TEXT)
    sampleCopied.value = true
    setTimeout(() => { sampleCopied.value = false }, 1800)
  } catch (e) {
    parseError.value = '复制失败，请手动选中文本后按 Ctrl+C'
  }
}

function toggleText(date) {
  textOpen.value = textOpen.value === date ? null : date
}

async function copyText(d) {
  try {
    await copyToClipboard(d.report_text || '')
    textCopied.value = d.date
    setTimeout(() => {
      if (textCopied.value === d.date) textCopied.value = ''
    }, 1600)
  } catch (e) {
    error.value = '复制失败，请手动选中文本后按 Ctrl+C'
  }
}

function addScenario() {
  form.value.scenarios.push({ name: '', prob: 0, path: '' })
}
function addCheck() {
  form.value.checks.push({ key: 'amount_yi', op: '<=', value: null })
}

async function submit() {
  formError.value = ''
  const title = (form.value.title || '').trim()
  if (!title) { formError.value = '标题必填'; return }

  const scenarios = form.value.scenarios
    .filter(s => (s.name || '').trim())
    .map(s => ({
      name: s.name.trim(),
      prob: Number(s.prob) || 0,
      path: (s.path || '').trim(),
    }))
  if (!scenarios.length) { formError.value = '至少填一个情景'; return }

  const total = scenarios.reduce((a, s) => a + s.prob, 0)
  if (Math.abs(total - 100) > 1) {
    formError.value = `情景概率合计需为 100（当前 ${Math.round(total * 10) / 10}）`
    return
  }

  const checks = form.value.checks
    .filter(c => c.key && c.value !== null && c.value !== '')
    .map(c => ({
      key: c.key,
      op: c.op,
      value: Number(c.value),
      label: labelOf(c.key),
    }))
  if (!checks.length) { formError.value = '至少填一个验证项（要有阈值）'; return }

  const payload = {
    title,
    horizon_start: form.value.horizon_start,
    horizon_end: form.value.horizon_end,
    scenarios,
    checks,
    invalidators: form.value.invalidators.map(s => (s || '').trim()).filter(Boolean),
  }

  saving.value = true
  try {
    await createThesisScenario(payload)
    showCreate.value = false
    await loadList()
    const created = list.value.find(t => t.title === title)
    if (created) await select(created)
  } catch (e) {
    formError.value = '登记失败：' + (e.response?.data?.detail || e.message)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try {
    await Promise.all([loadMetrics(), loadList()])
    if (list.value.length) await select(list.value[0])
  } finally {
    loading.value = false
  }
})
</script>
