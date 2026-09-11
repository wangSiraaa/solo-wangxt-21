<script setup>
import { ref } from 'vue'
import { api, RESULT_LABEL } from '../api.js'
import PlayerReason from './PlayerReason.vue'

const props = defineProps({ status: { type: Object, required: true } })
const emit = defineEmits(['reload', 'notify'])

const busy = ref(null)
const correcting = ref(null)
const newResult = ref('D')
const reason = ref('')

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

async function startBoard(g) {
  busy.value = g.id
  try {
    await api.markStarted(g.id)
    emit('notify', `棋桌已标记开赛（${g.white_name} vs ${g.black_name}），将锁定不可重排`)
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
    const r = await api.correctResult(g.id, newResult.value, reason.value.trim())
    if (r.idempotent) {
      emit('notify', '重复提交同一裁定：未产生新变更（幂等）')
    } else {
      const n = r.ranking_impact.length
      emit('notify',
        `成绩已更正，原裁定 ${RESULT_LABEL[g.verdict]} 保留；${n} 人的积分/小分/名次已重算（对阵不变）`)
    }
    correcting.value = null
    reason.value = ''
    emit('reload')
  } catch (e) {
    emit('notify', e.message, true)
  } finally {
    busy.value = null
  }
}

const revisionStatus = { draft: '方案待确认', applied: '已生效', expired: '已过期' }
</script>

<template>
  <div class="panel">
    <h2>已发布轮次（发布快照不可变 · 刷新不改变）</h2>

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
        <span v-if="rd.revisions?.length" class="badge info">
          受控重排 {{ rd.revisions.length }} 次（{{
            rd.revisions.filter(r => r.status === 'applied').length
          }} 次生效）
        </span>
      </div>

      <!-- 修订历史（第二条审计线） -->
      <div v-if="rd.revisions?.length" style="margin:8px 0">
        <div v-for="rv in rd.revisions" :key="rv.id" class="reason-card"
             :class="rv.status">
          <div class="head small">
            <span class="badge" :class="rv.status === 'applied' ? 'ok'
              : rv.status === 'expired' ? 'muted' : 'relaxed'">
              {{ revisionStatus[rv.status] }}
            </span>
            重排 #{{ rv.id }} · {{ rv.reason }}
          </div>
          <div class="small muted" v-if="rv.status === 'applied'">
            锁定 {{ rv.diff.locked_boards }} 桌 · 撤销 {{ rv.diff.dissolved.length }} ·
            新开 {{ rv.diff.created.length }} · 改动 {{ rv.diff.changed_boards }} 桌 ·
            生效于 {{ new Date(rv.applied_at).toLocaleString('zh-CN') }}
          </div>
          <div class="small muted" v-else-if="rv.status === 'expired'">
            {{ rv.expiry_note || '方案过期' }}
          </div>
        </div>
      </div>

      <table style="margin-top:8px">
        <tr>
          <th>白方</th><th></th><th>黑方</th><th>棋桌状态</th>
          <th>结果</th><th>裁判操作</th><th>审计</th>
        </tr>
        <!-- 取消的棋桌（重排撤销，行保留可回看） -->
        <template v-for="g in rd.games.filter(x => x.status === 'cancelled')" :key="'c' + g.id">
          <tr class="cancelled-row">
            <template v-if="g.is_bye">
              <td colspan="3" class="muted">⏸ {{ g.white_name }} 的轮空安排</td>
            </template>
            <template v-else>
              <td class="muted"><s>{{ g.white_name }}</s></td>
              <td class="vs">vs</td>
              <td class="muted"><s>{{ g.black_name }}</s></td>
            </template>
            <td><span class="badge blocked">已撤销</span></td>
            <td class="muted">—</td>
            <td colspan="2" class="small muted">{{ g.cancelled_reason }}</td>
          </tr>
        </template>

        <template v-for="g in rd.games.filter(x => x.status === 'active')" :key="g.id">
          <tr>
            <template v-if="g.is_bye">
              <td colspan="2"><span class="bye-box" style="padding:2px 10px">⏸ {{ g.white_name }} 轮空（记 1 分）</span></td>
              <td class="muted">—</td>
              <td><span class="badge muted">轮空</span></td>
              <td><span class="badge info">BYE</span></td>
              <td class="muted small">系统生成</td>
              <td></td>
            </template>
            <template v-else>
              <td><span class="stone-W"></span>{{ g.white_name }}</td>
              <td class="vs">vs</td>
              <td><span class="stone-B"></span>{{ g.black_name }}</td>
              <td>
                <span v-if="g.started" class="badge info">● 已开赛 · 锁定</span>
                <span v-else-if="g.verdict" class="badge muted">已裁定</span>
                <span v-else class="badge muted">未开赛</span>
              </td>
              <td>
                <span v-if="g.verdict" class="badge" :class="g.corrected ? 'relaxed' : 'ok'">
                  {{ RESULT_LABEL[g.current_result] }}
                  <template v-if="g.corrected">（原 {{ RESULT_LABEL[g.verdict] }}）</template>
                </span>
                <span v-else class="badge muted">待录入</span>
              </td>
              <td>
                <template v-if="!g.verdict && !g.started">
                  <button class="small" @click="startBoard(g)" :disabled="busy === g.id">标记开赛</button>
                  <button class="small" @click="enter(g, 'W')" :disabled="busy === g.id">白胜</button>
                  <button class="small" @click="enter(g, 'D')" :disabled="busy === g.id">和</button>
                  <button class="small" @click="enter(g, 'B')" :disabled="busy === g.id">黑胜</button>
                </template>
                <template v-else-if="!g.verdict">
                  <span class="small muted">已开赛，录入后锁定</span>
                  <div>
                    <button class="small" @click="enter(g, 'W')">白胜</button>
                    <button class="small" @click="enter(g, 'D')">和</button>
                    <button class="small" @click="enter(g, 'B')">黑胜</button>
                  </div>
                </template>
                <template v-else>
                  <button class="small" @click="correcting = correcting === g.id ? null : g.id">
                    成绩更正
                  </button>
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
          <tr v-for="x in [g]" :key="'f' + g.id">
            <td colspan="7" style="padding:0;border:none">
              <div v-if="correcting === g.id" class="alert warn row" style="margin:6px 0">
                <label>更正为</label>
                <select v-model="newResult">
                  <option value="W">白胜</option>
                  <option value="D">和棋</option>
                  <option value="B">黑胜</option>
                </select>
                <input style="flex:1;min-width:240px" v-model="reason"
                       placeholder="更正理由（必填，写入审计；积分小分重算、对阵不变）" />
                <button class="primary small" :disabled="busy === g.id"
                        @click="submitCorrection(g)">提交更正</button>
              </div>
            </td>
          </tr>
        </template>
      </table>

      <!-- 发布快照回看 -->
      <details>
        <summary>查看发布时冻结的名单、配对依据与规则版本（历史按当时状态回看）</summary>
        <div style="margin-top:8px">
          <div class="small muted">
            发布名单 {{ rd.roster_snapshot.length }} 人；配对由规则
            <span class="mono">{{ rd.rule_version }}</span> 计算。
            下列内容是发布瞬间的快照，后续申诉改判与受控重排都不会改动它。
          </div>
          <table style="margin-top:6px">
            <tr>
              <th>报名</th><th>选手</th>
              <th class="num">赛前积分</th><th class="num">白</th>
              <th class="num">黑</th><th class="num">历史轮空</th>
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
          <h3>发布时配对依据</h3>
          <PlayerReason v-for="pid in Object.keys(rd.pairing_snapshot.player_reasons)"
                        :key="pid"
                        :reason="rd.pairing_snapshot.player_reasons[pid]" />
        </div>
      </details>
    </div>
  </div>
</template>

<style scoped>
.cancelled-row { opacity: 0.62; background: #1a1414; }
.cancelled-row td { border-bottom: 1px dashed #4a3030; }
</style>
