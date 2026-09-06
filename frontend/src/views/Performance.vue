<script setup>
/**
 * 系统绩效页（/performance）—— 回答"有没有 edge"
 *
 * 三轨对照，每轨明确标注统计属性：
 *   模拟盘（前瞻真实） / 排行榜（半前瞻·重叠窗口） / 战法回放（in-sample）
 * 结论导向：不证明当下有 edge，而是让 edge 与否在积累后有数据可答。
 */
import { ref, onMounted } from 'vue'
import { getSystemPerformance } from '../api'

const perf = ref(null)
const loading = ref(true)
const error = ref('')

function deltaText(v) {
  if (v == null) return '-'
  return (v > 0 ? '+' : '') + v + '%'
}
function pnlColor(v) {
  if (v == null) return 'text-muted'
  return v > 0 ? 'text-rise' : 'text-fall'
}

onMounted(async () => {
  try {
    const { data } = await getSystemPerformance()
    perf.value = data
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="fade-in">
    <div class="bg-card border border-border rounded-lg p-4 mb-4">
      <h1 class="text-lg font-bold text-gray-100">系统绩效 · 有没有 Edge</h1>
      <p class="text-xs text-muted mt-0.5">
        三轨对照：模拟盘（前瞻真实）/ 排行榜（半前瞻·重叠窗口）/ 战法回放（样本内）。
        每个数字的样本量与统计属性都标在旁边——<span class="text-amber-400">样本不足时，"不知道"就是答案</span>。
      </p>
    </div>

    <div v-if="loading" class="bg-card border border-border rounded-lg p-8 text-center">
      <div class="loading-spinner mx-auto mb-3"></div>
      <p class="text-xs text-muted">计算中（战法回放需重放全部历史信号，约 20 秒）…</p>
    </div>
    <div v-else-if="error" class="bg-card border border-border rounded-lg p-6 text-sm text-fall">{{ error }}</div>

    <template v-else-if="perf">
      <!-- 轨道一：模拟盘 -->
      <div class="bg-card border border-border rounded-lg p-4 mb-4">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-bold text-gray-100">① 模拟盘（前瞻真实记录）</h2>
          <span class="text-xs text-muted">运行 {{ perf.paper.days }} 天 · 起 {{ perf.paper.start }}</span>
        </div>
        <div v-if="perf.paper.available" class="grid grid-cols-2 md:grid-cols-6 gap-3 text-center">
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">已平仓</div>
            <div class="text-lg font-bold text-gray-100">{{ perf.paper.closed }}</div>
          </div>
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">胜率</div>
            <div class="text-lg font-bold" :class="pnlColor(perf.paper.win_rate)">{{ perf.paper.win_rate ?? '-' }}%</div>
          </div>
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">平均单笔</div>
            <div class="text-lg font-bold" :class="pnlColor(perf.paper.avg_pnl)">{{ perf.paper.avg_pnl ?? '-' }}%</div>
          </div>
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">已实现盈亏</div>
            <div class="text-lg font-bold" :class="pnlColor(perf.paper.realized)">{{ perf.paper.realized?.toLocaleString() }}</div>
          </div>
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">持仓中</div>
            <div class="text-lg font-bold text-gray-100">{{ perf.paper.holding }}</div>
          </div>
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">同期沪深300</div>
            <div class="text-lg font-bold" :class="pnlColor(perf.paper.benchmark_hs300)">{{ deltaText(perf.paper.benchmark_hs300) }}</div>
          </div>
        </div>
        <p v-if="perf.paper.note" class="mt-3 text-xs text-amber-400">⚠️ {{ perf.paper.note }}</p>
      </div>

      <!-- 轨道二：排行榜 -->
      <div class="bg-card border border-border rounded-lg p-4 mb-4">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-bold text-gray-100">② 评分排行榜 Top10（每日等权，T+5）</h2>
          <span v-if="perf.ranking.available" class="text-xs text-muted">
            {{ perf.ranking.start }} ~ {{ perf.ranking.end }} · {{ perf.ranking.days }} 个快照日
          </span>
        </div>
        <div v-if="perf.ranking.available" class="grid grid-cols-2 md:grid-cols-4 gap-3 text-center mb-3">
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">日均收益（重叠近似）</div>
            <div class="text-lg font-bold" :class="pnlColor(perf.ranking.avg_daily)">{{ deltaText(perf.ranking.avg_daily) }}%</div>
          </div>
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">累计（重叠乘积）</div>
            <div class="text-lg font-bold" :class="pnlColor(perf.ranking.cum_return)">{{ deltaText(perf.ranking.cum_return) }}%</div>
          </div>
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">日胜率</div>
            <div class="text-lg font-bold text-gray-100">{{ perf.ranking.win_rate_days }}%</div>
          </div>
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-[10px] text-muted">同期沪深300</div>
            <div class="text-lg font-bold" :class="pnlColor(perf.ranking.benchmark_hs300)">{{ deltaText(perf.ranking.benchmark_hs300) }}</div>
          </div>
        </div>
        <div v-if="perf.ranking.available && perf.ranking.per_day?.length" class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-muted border-b border-border">
                <th class="text-left py-1.5 px-2">日期</th>
                <th class="text-right py-1.5 px-2">Top10 均收益(T+5)</th>
                <th class="text-right py-1.5 px-2">个股胜率</th>
                <th class="text-right py-1.5 px-2">样本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in perf.ranking.per_day" :key="d.date" class="border-t border-border/50">
                <td class="py-1.5 px-2 font-mono">{{ d.date }}</td>
                <td class="py-1.5 px-2 text-right font-mono" :class="pnlColor(d.mean)">{{ deltaText(d.mean) }}%</td>
                <td class="py-1.5 px-2 text-right font-mono">{{ d.win }}%</td>
                <td class="py-1.5 px-2 text-right font-mono text-muted">{{ d.n }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="perf.ranking.note" class="mt-3 text-xs text-amber-400">⚠️ {{ perf.ranking.note }}</p>
      </div>

      <!-- 轨道三：战法回放 -->
      <div class="bg-card border border-border rounded-lg p-4 mb-4">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-bold text-gray-100">③ 战法回放（部署管线：主力过滤 + v2 退出）</h2>
          <span class="text-[10px] px-2 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/20">in-sample</span>
        </div>
        <div v-if="perf.replay.available" class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-xs text-muted mb-2">基线（原参数，未过滤）</div>
            <div class="text-xs space-y-1 font-mono">
              <div>样本 {{ perf.replay.baseline?.n }} 笔</div>
              <div>胜率 {{ perf.replay.baseline?.win }}% ｜ 均收益 {{ deltaText(perf.replay.baseline?.avg) }}%</div>
              <div>盈亏比 {{ perf.replay.baseline?.pf }} ｜ 同期沪深300 {{ deltaText(perf.replay.baseline?.bench) }}</div>
            </div>
          </div>
          <div class="bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-3">
            <div class="text-xs text-emerald-400 mb-2">部署管线（G 过滤 + v2 退出）</div>
            <div class="text-xs space-y-1 font-mono">
              <div>样本 {{ perf.replay.deployed?.n }} 笔</div>
              <div>胜率 {{ perf.replay.deployed?.win }}% ｜ 均收益 {{ deltaText(perf.replay.deployed?.avg) }}%</div>
              <div>盈亏比 {{ perf.replay.deployed?.pf }} ｜ 同期沪深300 {{ deltaText(perf.replay.deployed?.bench) }}</div>
            </div>
          </div>
        </div>
        <p v-if="perf.replay.note" class="mt-3 text-xs text-red-400">⚠️ {{ perf.replay.note }}</p>
      </div>

      <!-- 结论框 -->
      <div class="bg-card border border-border rounded-lg p-4 text-xs text-muted leading-relaxed">
        <p class="font-semibold text-gray-300 mb-1">怎么读这页</p>
        <p>· 模拟盘是唯一"前瞻真实"的记录，但 {{ perf.paper.closed }} 笔平仓在统计上等于没有结论——它的价值从第 30 笔平仓开始。</p>
        <p>· 排行榜数字偏乐观（权重同期校准 + 重叠窗口），只看趋势与相对变化。</p>
        <p>· 战法回放的正期望是样本内调优的结果，<span class="text-amber-400">不可外推</span>；它证明的是"管线按设计运行"，不是"未来赚钱"。</p>
        <p class="mt-2">生成时间：{{ perf.generated_at }}（绩效数据 1 小时缓存）</p>
      </div>
    </template>
  </div>
</template>
