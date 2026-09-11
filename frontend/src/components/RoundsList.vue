<script setup>
import { ref } from 'vue'
import { api, RESULT_LABEL } from '../api.js'
import PlayerReason from './PlayerReason.vue'

const props = defineProps({ status: { type: Object, required: true } })
const emit = defineEmits(['reload', 'notify'])

const busy = ref(null)              // game id 正在请求
const correcting = ref(null)        // game id 展开更正表单
const newResult = ref('D')
const reason = ref('')

function nameOf(id) {
  return props.status.tournament.players.find((p) => p.id === id)?.name ?? `#${id}`
}

async function enter(g, result) {
  busy.value = g.id
  try {
    await api.enterResult(g.id, result)
    emit('notify', `已录入：${g.white_name} vs ${g.black_name} → ${RESULT_LABEL[result]}`)
    emit('reload')
  } catch (e) {
    emit('notify', e.message, true)
  } finally {
    busy.value = null
  }
}

async function submitCorrection(g) {
  if (reason.value.trim().length < 4) {
    emit('notify', '更正理由不少于 4 个字', true)
    return
  }
  busy.value = g.id
  try {
    await api.correctResult(g.id, newResult.value, reason.value.trim())
    emit('notify', `成绩已更正；原裁定 ${RESULT_LABEL[g.verdict]} 保留，积分小分已重算`)
    correcting.value = null
    reason.value = ''
    emit('reload')
  } catch (e) {
    emit('notify', e.message, true)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="panel">
    <h2>已发布轮次（快照不可变 · 刷新不改变）</h2>

    <div v-if="!status.rounds.length" class="muted small">尚未发布任何轮次。</div>

    <div v-for="rd in status.rounds" :key="rd.id" class="panel tight"
         style="background:var(--panel-2);margin-bottom:12px">
      <div class="row">
        <h3 style="margin:0">第 {{ rd.round_no }} 轮</h3>
        <span class="badge ok">已冻结 {{ new Date(rd.published_at).toLocaleString('zh-CN') }}</span>
        <span class="badge muted mono">规则 {{ rd.rule_version }}</span>
        <span v-if="rd.override_used" class="badge overridden">
          NO_REPEAT 已授权放宽：{{ rd.override_reason }}
        </span>
      </div>

      <!-- 冻结对阵 + 裁判录入 -->
      <table style="margin-top:8px">
        <tr>
          <th>白方</th><th></th><th>黑方</th><th>结果</th><th>裁判操作</th><th>审计</th>
        </tr>
        <tr v-for="g in rd.games" :key="g.id">
          <template v-if="g.is_bye">
            <td colspan="2"><span class="bye-box" style="padding:2px 10px">⏸ {{ g.white_name }} 轮空（记 1 分）</span></td>
            <td class="muted">—</td>
            <td><span class="badge info">BYE</span></td>
            <td class="muted small">系统生成</td>
            <td></td>
          </template>
          <template v-else>
            <td><span class="stone-W"></span>{{ g.white_name }}</td>
            <td class="vs">vs</td>
            <td><span class="stone-B"></span>{{ g.black_name }}</td>
            <td>
              <span v-if="g.verdict" class="badge" :class="g.corrected ? 'relaxed' : 'ok'">
                {{ RESULT_LABEL[g.current_result] }}
                <template v-if="g.corrected">
                  （原裁定 {{ RESULT_LABEL[g.verdict] }}）
                </template>
              </span>
              <span v-else class="badge muted">待录入</span>
            </td>
            <td>
              <template v-if="!g.verdict">
                <button class="small" :disabled="busy === g.id" @click="enter(g, 'W')">白胜</button>
                <button class="small" :disabled="busy === g.id" @click="enter(g, 'D')">和棋</button>
                <button class="small" :disabled="busy === g.id" @click="enter(g, 'B')">黑胜</button>
              </template>
              <template v-else>
                <button class="small" @click="correcting = correcting === g.id ? null : g.id">
                  成绩更正
                </button>
                <span class="small muted" style="margin-left:6px">原裁定不可覆盖</span>
              </template>
            </td>
            <td>
              <div v-for="c in g.corrections" :key="c.id" class="small" style="color:var(--accent)">
                ✎ {{ RESULT_LABEL[c.old_result] }} → {{ RESULT_LABEL[c.new_result] }}
                （{{ c.created_by }}：{{ c.reason }}）
              </div>
            </td>
          </template>
        </tr>
      </table>

      <!-- 更正表单 -->
      <div v-for="g in rd.games" :key="'f' + g.id">
        <div v-if="correcting === g.id" class="alert warn row" style="margin-top:8px">
          <label>更正为</label>
          <select v-model="newResult">
            <option value="W">白胜</option>
            <option value="D">和棋</option>
            <option value="B">黑胜</option>
          </select>
          <input style="flex:1;min-width:260px" v-model="reason"
                 placeholder="更正理由（必填，写入审计；积分与小分自动重算）" />
          <button class="primary small" :disabled="busy === g.id" @click="submitCorrection(g)">提交更正</button>
        </div>
      </div>

      <!-- 发布快照查看器 -->
      <details>
        <summary>查看发布时冻结的名单、配对依据与规则版本</summary>
        <div style="margin-top:8px">
          <div class="small muted">
            发布名单 {{ rd.roster_snapshot.length }} 人（含赛前积分/先后手/轮空次数），
            配对由规则 <span class="mono">{{ rd.rule_version }}</span> 计算。
          </div>
          <table style="margin-top:6px">
            <tr>
              <th>报名</th><th>选手</th>
              <th class="num">赛前积分</th><th class="num">累计白</th>
              <th class="num">累计黑</th><th class="num">历史轮空</th>
            </tr>
            <tr v-for="r in rd.roster_snapshot" :key="r.player_id">
              <td class="muted num">{{ r.registration_no }}</td>
              <td>{{ r.name }}</td>
              <td class="num">{{ r.score_before_round }}</td>
              <td class="num">{{ r.whites }}</td>
              <td class="num">{{ r.blacks }}</td>
              <td class="num">{{ r.byes }}</td>
            </tr>
          </table>

          <h3>配对依据（发布时冻结）</h3>
          <PlayerReason v-for="pid in Object.keys(rd.pairing_snapshot.player_reasons)"
                        :key="pid"
                        :reason="rd.pairing_snapshot.player_reasons[pid]" />
        </div>
      </details>
    </div>
  </div>
</template>
