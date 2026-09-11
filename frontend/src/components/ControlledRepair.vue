<script setup>
import { computed, ref } from 'vue'
import { api } from '../api.js'
import PlayerReason from './PlayerReason.vue'

const props = defineProps({ status: { type: Object, required: true } })
const emit = defineEmits(['reload', 'notify'])

const plan = ref(null)
const working = ref(false)
const override = ref(false)
const overrideReason = ref('')

const latestRound = computed(() => {
  const rs = props.status.rounds
  return rs.length ? rs[rs.length - 1] : null
})

// 最新轮是否存在"可重排"的局面：有未开赛 active 桌，或有人退赛导致无桌
const repairable = computed(() => {
  const rd = latestRound.value
  if (!rd) return false
  return rd.games.some((g) =>
    g.status === 'active' && !g.started && !g.is_bye
  ) || rd.games.some((g) => g.status === 'cancelled')
})

const nameOf = (id) =>
  props.status.tournament.players.find((p) => p.id === id)?.name ?? `#${id}`
const pairKey = (p) => [p.white_id, p.black_id].sort().join('-')

const baselinePairs = computed(() =>
  latestRound.value
    ? latestRound.value.games
        .filter((g) => g.status === 'active' && !g.is_bye)
        .map((g) => ({ white_id: g.white_id, black_id: g.black_id }))
    : []
)

async function generate() {
  working.value = true
  try {
    plan.value = await api.createRepairPlan(props.status.tournament.id, {
      override: override.value, reason: overrideReason.value,
    })
    if (!plan.value.feasible) {
      const inf = plan.value.report.infeasibility
      emit('notify', inf.message, true)
    }
  } catch (e) {
    emit('notify', e.message, true)
  } finally {
    working.value = false
  }
}

async function confirm() {
  working.value = true
  try {
    const r = await api.confirmRepair(plan.value.revision_id)
    if (r.status === 'already_applied') {
      emit('notify', '该方案此前已生效（重复确认幂等，未产生新变更）')
    } else {
      emit('notify', `重排已生效：${r.diff.changed_boards} 张棋桌调整，已开赛桌保持不动`)
    }
    plan.value = null
    emit('reload')
  } catch (e) {
    if (e.status === 409) {
      emit('notify',
        typeof e.payload === 'object'
          ? e.payload.message
          : '确认被拒：' + e.message, true)
      plan.value = null
      emit('reload')
    } else {
      emit('notify', e.message, true)
    }
  } finally {
    working.value = false
  }
}

const proposedRows = computed(() => {
  if (!plan.value?.feasible) return []
  const baseKeys = new Set(baselinePairs.value.map(pairKey))
  return plan.value.report.pairs.map((p) => ({
    ...p,
    locked: p.locked,
    changed: !p.locked && !baseKeys.has(pairKey(p)),
  }))
})

const dissolvedRows = computed(() =>
  plan.value ? plan.value.diff.dissolved : []
)
const costRows = computed(() => {
  if (!plan.value) return []
  const labels = {
    A_score_gap_half_points: 'A 积分差（半子）',
    B_bye: 'B 轮空公平',
    C_color: 'C 先后手总代价',
    unpaired_count: '无桌可下人数',
  }
  const keys = ['A_score_gap_half_points', 'B_bye', 'C_color', 'unpaired_count']
  return keys.map((k) => ({
    key: labels[k],
    base: plan.value.diff.baseline_cost[k],
    now: plan.value.feasible ? plan.value.diff.proposed_cost[k] : '—',
  }))
})
</script>

<template>
  <div class="panel" v-if="latestRound">
    <div class="row">
      <h2 style="margin:0">受控重配对（第 {{ latestRound.round_no }} 轮）</h2>
      <span class="muted small">申诉改判 / 临时退赛后使用；已开赛棋桌锁定不动</span>
      <div class="spacer"></div>
      <label class="row" style="gap:6px">
        <input type="checkbox" v-model="override" /> 授权放宽 NO_REPEAT
      </label>
      <input v-if="override" v-model="overrideReason"
             placeholder="授权理由（必填）" style="width:220px" />
      <button class="primary" :disabled="working || !repairable" @click="generate">
        {{ working ? '求解中…' : '生成重排方案（对比）' }}
      </button>
    </div>

    <div v-if="!repairable" class="muted small" style="margin-top:8px">
      本轮所有棋桌均已开赛/录入或尚无棋桌，没有可重排部分。
    </div>

    <template v-if="plan">
      <!-- 无解 -->
      <div v-if="!plan.feasible" class="alert blocked" style="margin-top:12px">
        <strong>⛔ {{ plan.report.infeasibility.message }}</strong>
        <div class="small" style="margin-top:6px">
          空闲 {{ plan.report.infeasibility.free_players }} 人 ·
          锁定桌 {{ plan.report.infeasibility.locked_boards }} ·
          退赛 {{ plan.report.infeasibility.withdrawn }} ·
          连通分量
          <span class="mono">{{ plan.report.infeasibility.allowed_graph.component_sizes.join(',') }}</span>
        </div>
        <div class="small" style="margin-top:4px">
          系统不会放松硬规则；可勾选上方"授权放宽 NO_REPEAT"并写明理由后重新生成。
        </div>
      </div>

      <template v-else>
        <!-- 代价对比 -->
        <h3>维持原表 vs 局部重排（全轮约束代价，越小越好）</h3>
        <table>
          <tr>
            <th>代价项</th>
            <th class="num">维持原表</th>
            <th class="num">局部重排</th>
            <th>说明</th>
          </tr>
          <tr v-for="r in costRows" :key="r.key">
            <td>{{ r.key }}</td>
            <td class="num" :class="{ 'cost-bad': r.key.includes('无桌') && r.base > 0 }">{{ r.base }}</td>
            <td class="num">{{ r.now }}</td>
            <td class="small muted">
              <template v-if="r.key.includes('无桌') && r.base > 0">
                原表有 {{ r.base }} 人因退赛无桌可下（基线不可行：{{ nameOf(plan.diff.baseline_cost.unpaired_players[0]) }}）
              </template>
              <template v-else-if="r.key.startsWith('A')">字典序第 1 优先：积分最接近</template>
              <template v-else-if="r.key.startsWith('B')">字典序第 2 优先：轮空公平</template>
              <template v-else>字典序第 3 优先：先后手平衡</template>
            </td>
          </tr>
        </table>

        <div class="row" style="margin:10px 0;gap:18px">
          <span class="badge info">锁定棋桌 {{ plan.diff.locked_boards }} 张（不动）</span>
          <span class="badge relaxed">撤销 {{ plan.diff.dissolved.length }} 张</span>
          <span class="badge ok">新开 {{ plan.diff.created.length }} 张</span>
          <span class="badge muted">轮空 {{ plan.diff.byes_before.map(nameOf).join('、') || '无' }}
            → {{ plan.diff.byes_after.map(nameOf).join('、') || '无' }}</span>
          <div class="spacer"></div>
          <span class="muted small">方案未落子；确认时会再次复检是否有棋桌新开赛</span>
          <button class="primary" :disabled="working" @click="confirm">
            确认应用重排（改动 {{ plan.diff.changed_boards }} 桌）
          </button>
        </div>

        <!-- 重排后对阵 -->
        <table>
          <tr><th>台次</th><th>白方</th><th></th><th>黑方</th><th>状态</th><th>依据</th></tr>
          <tr v-for="(p, i) in proposedRows" :key="i">
            <td class="num muted">{{ i + 1 }}</td>
            <td><span class="stone-W"></span>{{ nameOf(p.white_id) }}</td>
            <td class="vs">VS</td>
            <td><span class="stone-B"></span>{{ nameOf(p.black_id) }}</td>
            <td>
              <span v-if="p.locked" class="badge info">🔒 已开赛锁定</span>
              <span v-else-if="p.changed" class="badge relaxed">↻ 新开棋局</span>
              <span v-else class="badge muted">维持</span>
            </td>
            <td class="small muted">{{ p.color_reason }}</td>
          </tr>
        </table>
        <div v-if="plan.report.byes.length" class="bye-box" style="margin-top:8px">
          ⏸ 轮空：{{ plan.report.byes.map(nameOf).join('、') }}
        </div>

        <!-- 软约束层 -->
        <h3>重排方案的软约束分层</h3>
        <div v-for="t in plan.report.tiers" :key="t.tier" class="tier-row">
          <span class="tier-tag">{{ t.tier }}</span>
          <span class="badge" :class="t.status === 'SATISFIED' ? 'ok' : 'relaxed'">
            {{ t.status === 'SATISFIED' ? '满足' : '被放宽' }}
          </span>
          <div class="small muted">{{ t.detail }}</div>
        </div>

        <details>
          <summary>逐选手配对依据（含锁定桌与退赛者）</summary>
          <div style="margin-top:8px">
            <PlayerReason v-for="pid in Object.keys(plan.report.player_reasons)"
                          :key="pid" :reason="plan.report.player_reasons[pid]" />
          </div>
        </details>
      </template>
    </template>
  </div>
</template>

<style scoped>
.cost-bad { color: var(--red); font-weight: 700; }
</style>
