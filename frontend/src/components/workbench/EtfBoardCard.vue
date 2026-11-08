<template>
  <div>
    <div class="flex items-center justify-between mb-1.5 flex-wrap gap-1">
      <div class="text-[11px] text-muted">
        热度分=量比×|涨跌|×√成交额 · 白名单 {{ board?.n || '—' }} 只 · {{ board?.as_of }}
      </div>
      <div class="flex gap-1">
        <button v-for="s in sorts" :key="s[0]" @click="sort = s[0]; load(s[0])"
                :class="sort === s[0] ? 'bg-accent/20 text-accent border-accent/40' : 'bg-white/5 text-muted border-border/60'"
                class="px-2 py-0.5 rounded text-[11px] border transition-colors">{{ s[1] }}</button>
      </div>
    </div>
    <div v-if="loading" class="text-muted text-xs py-3 text-center">加载中…</div>
    <table v-else-if="board?.rows?.length" class="w-full text-xs">
      <thead class="text-muted">
        <tr class="border-b border-border">
          <th class="text-left py-1 pr-1">#</th>
          <th class="text-left py-1 px-1">名称/代码</th>
          <th class="text-right py-1 px-1">现价</th>
          <th class="text-right py-1 px-1">涨跌</th>
          <th class="text-right py-1 px-1">量比</th>
          <th class="text-right py-1 pl-1">成交额</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(r, i) in board.rows" :key="r.code" class="border-b border-border/30 hover:bg-white/[0.03]">
          <td class="py-1 pr-1 font-mono text-muted">{{ i + 1 }}</td>
          <td class="py-1 px-1">
            <a :href="`#/stock/${r.code}`" target="_blank"
               class="text-gray-200 hover:text-accent hover:underline">{{ r.name }}</a>
            <a :href="xqUrl(r.code)" target="_blank" rel="noopener"
               class="font-mono text-[10px] text-muted/80 hover:text-accent ml-0.5" title="雪球">{{ r.code }}</a>
          </td>
          <td class="py-1 px-1 text-right font-mono text-gray-200">{{ r.price }}</td>
          <td class="py-1 px-1 text-right font-mono font-semibold"
              :class="r.change_pct > 0 ? 'text-red-400' : r.change_pct < 0 ? 'text-emerald-400' : ''">
            {{ r.change_pct > 0 ? '+' : '' }}{{ r.change_pct }}%</td>
          <td class="py-1 px-1 text-right font-mono"
              :class="r.volume_ratio >= 1.5 ? 'text-red-400' : (r.volume_ratio <= 0.6 ? 'text-muted' : '')">{{ r.volume_ratio }}</td>
          <td class="py-1 pl-1 text-right font-mono text-muted">{{ (r.amount_wan / 10000).toFixed(1) }}亿</td>
        </tr>
      </tbody>
    </table>
    <div v-else class="text-muted text-xs py-3 text-center">—（未就绪，交易时段自动就位）</div>
    <div class="text-[10px] text-muted mt-1">{{ board?.note }}</div>
  </div>
</template>

<script setup>
// ★ 2026-11-08（ETF 榜·方案 B）：可复用热度榜卡——评分排行页 tab + 工作台右栏 ETF Top10。
//   行情展示非信号（R2）；不含溢价率/主力资金流（不可得，见 ETF 榜方案文档）。
import { ref, onMounted } from 'vue'
import { getEtfBoard } from '../../api'
import { xqUrl } from '../../composables/displayMeta'

const props = defineProps({
  top: { type: Number, default: 10 },
  auto: { type: Boolean, default: true },
})
const sorts = [['hot', '热度'], ['chg', '涨幅'], ['vr', '量比'], ['amt', '成交额']]
const board = ref(null)
const loading = ref(false)
const sort = ref('hot')

async function load(s = sort.value) {
  sort.value = s
  loading.value = true
  try {
    const { data } = await getEtfBoard(s, props.top)
    board.value = data?.ok ? data : null
  } catch { board.value = null }
  finally { loading.value = false }
}

onMounted(() => { if (props.auto) load() })
defineExpose({ load })
</script>
