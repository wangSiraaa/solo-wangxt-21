<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: Object, required: true },
  tournamentName: String,
})

// 按积分分成积分组（展示瑞士制典型的"分数段"结构）
const groups = computed(() => {
  const out = []
  for (const s of props.status.standings) {
    const g = out.find((x) => x.points === s.points)
    if (g) g.rows.push(s)
    else out.push({ points: s.points, rows: [s] })
  }
  return out
})

const fmt = (n) => Number(n).toString()
const nameOf = (id) =>
  props.status.tournament.players.find((p) => p.id === id)?.name ?? `#${id}`
const tiedNames = (s) =>
  s.tied_with.filter((id) => id !== s.player_id).map(nameOf).join('、')
</script>

<template>
  <div class="panel">
    <div class="row">
      <h2 style="margin:0">积分榜 / 终局排名</h2>
      <span class="muted small">小分：Buchholz 对手分 → 裁 1 → 胜局 → 等级分；全部相同则并列</span>
    </div>
    <table style="margin-top:10px">
      <tr>
        <th>名次</th><th>报名</th><th>选手</th><th class="num">等级分</th>
        <th class="num">积分</th><th class="num">胜</th><th class="num">和</th><th class="num">负</th>
        <th class="num">轮空</th><th class="num">白/黑</th>
        <th class="num">对手分</th><th class="num">裁1</th><th>备注</th>
      </tr>
      <template v-for="g in groups" :key="g.points">
        <tr v-for="s in g.rows" :key="s.player_id">
          <td><strong>#{{ s.rank }}</strong></td>
          <td class="muted num">{{ s.registration_no }}</td>
          <td>
            {{ s.name }}
            <span v-if="s.withdrawn" class="badge blocked" style="margin-left:6px"
                  :title="`第 ${s.withdrawn_round_no} 轮退赛；既有成绩保留`">
              退赛(R{{ s.withdrawn_round_no }})
            </span>
            <span v-if="s.tied" class="tie-flag">⚑ 并列</span>
            <span v-if="s.correction_count" class="corr-flag"
                  :title="`涉及 ${s.correction_count} 次成绩更正`">
              ✎ 更正×{{ s.correction_count }}
            </span>
          </td>
          <td class="num">{{ s.rating }}</td>
          <td class="num score-group-label">{{ fmt(s.points) }}</td>
          <td class="num">{{ s.wins }}</td>
          <td class="num">{{ s.draws }}</td>
          <td class="num">{{ s.losses }}</td>
          <td class="num">{{ s.byes }}</td>
          <td class="num">{{ s.color_white }}/{{ s.color_black }}</td>
          <td class="num">{{ fmt(s.buchholz) }}</td>
          <td class="num muted">{{ fmt(s.buchholz_cut1) }}</td>
          <td>
            <span v-if="s.tied" class="badge info">
              与 {{ tiedNames(s) }} 同名次
            </span>
          </td>
        </tr>
      </template>
    </table>
    <div class="small muted" style="margin-top:8px">
      并列后名次跳号（如两人并列第 1，下一名为第 3）；报名序号只决定并列者的展示顺序，不打破并列。
    </div>
  </div>
</template>
