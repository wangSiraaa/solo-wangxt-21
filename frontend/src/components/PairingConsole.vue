<script setup>
import { computed, ref } from 'vue'
import { api } from '../api.js'
import PlayerReason from './PlayerReason.vue'

const props = defineProps({
  status: { type: Object, required: true },
  loading: Boolean,
  override: Boolean,
  overrideReason: String,
})
const emit = defineEmits(['reload', 'notify', 'update:override', 'update:overrideReason'])

const publishing = ref(false)
const showReasons = ref(true)

const preview = computed(() => props.status.preview)
const report = computed(() => preview.value?.report)
const nameOf = (id) =>
  props.status.tournament.players.find((p) => p.id === id)?.name ?? `#${id}`

const canPublish = computed(() =>
  preview.value?.feasible && props.status.next_round_no !== null && !publishing.value
)

async function publish() {
  publishing.value = true
  try {
    const r = await api.publishRound(props.status.tournament.id, {
      override: props.override,
      reason: props.overrideReason,
    })
    emit('update:override', false)
    emit('update:overrideReason', '')
    emit('notify', `第 ${r.round_no} 轮已发布，名单与规则版本已冻结`)
    emit('reload')
  } catch (e) {
    const inf = e.payload?.infeasibility
    emit('notify', inf ? inf.message : e.message, true)
  } finally {
    publishing.value = false
  }
}

const affectedRelaxedPairs = computed(() =>
  (report.value?.tiers?.find((t) => t.tier === 'A')?.affected_pairs || [])
)
</script>

<template>
  <div class="panel">
    <div class="row">
      <h2 style="margin:0">配对控制台</h2>
      <span v-if="status.next_round_no" class="badge info">
        待发布：第 {{ status.next_round_no }} 轮
      </span>
      <span v-else class="badge muted">全部轮次已发布</span>
      <div class="spacer"></div>
      <button class="small" @click="showReasons = !showReasons">
        {{ showReasons ? '收起配对依据' : '展开配对依据' }}
      </button>
      <button class="primary" :disabled="!canPublish" @click="publish">
        {{ publishing ? '求解并发布中…' : `发布第 ${status.next_round_no ?? '—'} 轮` }}
      </button>
    </div>

    <div v-if="!status.next_round_no" class="alert info" style="margin-top:12px">
      赛事计划轮次已全部发布并冻结。排名即终局排名（见下方积分榜）。
    </div>

    <template v-else-if="!preview">
      <div class="muted small" style="margin-top:10px">勾选顶部"实时配对预览"查看求解器方案。</div>
    </template>

    <template v-else-if="!report.feasible">
      <div class="alert blocked" style="margin-top:12px">
        <strong>⛔ 无合法配对 —— 发布已被阻止，系统没有放松任何硬规则。</strong>
        <div class="small" style="margin-top:6px">{{ report.infeasibility.message }}</div>
      </div>
      <table style="margin-top:8px">
        <tr>
          <th>根因</th>
          <th>允许对阵图连通分量</th>
          <th>奇数分量</th>
          <th class="num">需轮空消化</th>
          <th class="num">规则允许轮空</th>
          <th>无处可赛选手</th>
        </tr>
        <tr>
          <td><span class="badge blocked">{{ report.infeasibility.root_cause }}</span></td>
          <td class="mono">{{ report.infeasibility.allowed_graph.component_sizes.join(' , ') }}</td>
          <td>{{ report.infeasibility.allowed_graph.odd_component_count }} 个</td>
          <td class="num">{{ report.infeasibility.byes_required_to_fix }}</td>
          <td class="num">{{ report.infeasibility.byes_allowed_by_rules }}</td>
          <td>{{ report.infeasibility.isolated_players.join('、') || '—' }}</td>
        </tr>
      </table>
      <div class="alert warn">
        <strong>裁判长选项：</strong>只有在书面授权放宽 <span class="mono">NO_REPEAT</span> 后才能继续。
        被重复的对阵会在对阵表、选手依据与轮次快照中全程标注。
      </div>
      <div class="row" style="margin-top:8px">
        <label class="row" style="gap:6px">
          <input type="checkbox" :checked="override"
                 @change="emit('update:override', $event.target.checked)" />
          我授权本轮放宽"禁止重复交手"
        </label>
        <input style="flex:1;min-width:280px" placeholder="授权理由（必填，不少于 4 字，写入审计）"
               :value="overrideReason"
               @input="emit('update:overrideReason', $event.target.value)" />
      </div>
    </template>

    <template v-else>
      <!-- 硬约束状态条 -->
      <h3>硬规则（求解器不可违反）</h3>
      <div class="row" style="gap:8px">
        <span v-for="h in report.hard_constraints" :key="h.code" class="badge"
              :class="h.status === 'SATISFIED' ? 'ok' : 'overridden'"
              :title="h.detail">
          {{ h.code }} · {{ h.status === 'SATISFIED' ? '满足' : '已授权放宽' }}
        </span>
        <span class="small muted">求解器：单线程固定随机种子，同输入同输出（刷新不变）</span>
      </div>

      <!-- 软约束分层报告 -->
      <h3>软约束分层优化（字典序：A 积分接近 → B 轮空公平 → C 先后手）</h3>
      <div v-for="t in report.tiers" :key="t.tier + t.code" class="tier-row">
        <span class="tier-tag">{{ t.tier }}</span>
        <span class="badge" :class="t.status === 'SATISFIED' ? 'ok' : 'relaxed'">
          {{ t.status === 'SATISFIED' ? '满足' : '被放宽' }}
        </span>
        <div>
          <strong class="small">{{ t.code }}</strong>
          <div class="small muted">{{ t.detail }}</div>
        </div>
      </div>

      <!-- 对阵表 -->
      <h3>第 {{ preview.round_no }} 轮对阵（预览未落库）</h3>
      <table>
        <tr><th>台次</th><th>白方</th><th></th><th>黑方</th><th>配对依据（逐选手可查，见下）</th></tr>
        <tr v-for="(p, i) in report.pairs" :key="i">
          <td class="num muted">{{ i + 1 }}</td>
          <td><span class="stone-W"></span>{{ nameOf(p.white_id) }}</td>
          <td class="vs">VS</td>
          <td><span class="stone-B"></span>{{ nameOf(p.black_id) }}</td>
          <td class="small muted">{{ p.color_reason }}</td>
        </tr>
      </table>
      <div v-if="report.byes.length" class="bye-box" style="margin-top:8px">
        ⏸ 轮空（直接记 1 分）：<strong>{{ report.byes.map(nameOf).join('、') }}</strong>
      </div>

      <div v-if="override" class="alert warn" style="margin-top:10px">
        本轮以 NO_REPEAT 授权方式求解：{{ overrideReason || '（尚未填写理由——发布将被拒绝）' }}
      </div>

      <!-- 逐选手依据 -->
      <details v-if="showReasons" open>
        <summary>配对依据明细（精确到每名选手）</summary>
        <div style="margin-top:8px">
          <PlayerReason v-for="pid in Object.keys(report.player_reasons)" :key="pid"
                        :reason="report.player_reasons[pid]" />
        </div>
      </details>
    </template>
  </div>
</template>
