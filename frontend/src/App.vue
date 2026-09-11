<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from './api.js'
import CreateTournament from './components/CreateTournament.vue'
import PairingConsole from './components/PairingConsole.vue'
import ControlledRepair from './components/ControlledRepair.vue'
import StandingsTable from './components/StandingsTable.vue'
import RoundsList from './components/RoundsList.vue'
import RulesPanel from './components/RulesPanel.vue'

const tournaments = ref([])
const currentId = ref(null)
const status = ref(null)
const loading = ref(false)
const previewOn = ref(true)
const override = ref(false)
const overrideReason = ref('')
const toast = ref(null)
let toastTimer = null

function notify(message, isError = false) {
  toast.value = { message, isError }
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = null), 4200)
}

async function loadTournaments(selectId = null) {
  tournaments.value = await api.listTournaments()
  if (selectId) currentId.value = selectId
  else if (!currentId.value && tournaments.value.length) {
    currentId.value = tournaments.value[0].id
  }
}

async function loadStatus() {
  if (!currentId.value) { status.value = null; return }
  loading.value = true
  try {
    status.value = await api.getStatus(currentId.value, {
      preview: previewOn.value,
      override: override.value,
      reason: overrideReason.value,
    })
  } catch (e) {
    notify(e.message, true)
  } finally {
    loading.value = false
  }
}

watch(currentId, loadStatus)
watch([previewOn, override, overrideReason], () => {
  if (previewOn.value) loadStatus()
})

onMounted(async () => {
  try {
    await loadTournaments()
    await loadStatus()
  } catch (e) {
    notify('无法连接后端 API：' + e.message, true)
  }
})

async function onCreated(id) {
  await loadTournaments(id)
  await loadStatus()
  notify(`新赛事已创建（#${id}），可直接发布第 1 轮`)
}

const currentName = computed(
  () => tournaments.value.find((t) => t.id === currentId.value)?.name || ''
)
</script>

<template>
  <div class="header-bar">
    <div class="logo"></div>
    <div>
      <h1>围棋俱乐部 · 瑞士制比赛工作台</h1>
      <div class="small muted">报名 → 配对（CP-SAT）→ 发布快照 → 裁判录入 → 小分重算 → 终局排名</div>
    </div>
  </div>

  <div class="panel tight row">
    <label>赛事</label>
    <select :value="currentId" @change="currentId = Number($event.target.value)">
      <option v-for="t in tournaments" :key="t.id" :value="t.id">
        #{{ t.id }} {{ t.name }}
      </option>
    </select>
    <span class="muted small" v-if="status">
      规则版本 <span class="mono">{{ status.rules.version }}</span> ·
      共 {{ status.tournament.total_rounds }} 轮 ·
      {{ status.tournament.players.length }} 人
    </span>
    <div class="spacer"></div>
    <label class="row" style="gap:6px">
      <input type="checkbox" v-model="previewOn" /> 实时配对预览
    </label>
  </div>

  <CreateTournament v-if="!currentId" @created="onCreated" />

  <template v-if="status">
    <PairingConsole
      :status="status"
      :loading="loading"
      v-model:override="override"
      v-model:override-reason="overrideReason"
      @reload="loadStatus"
      @notify="notify"
    />

    <ControlledRepair :status="status" @reload="loadStatus" @notify="notify" />

    <StandingsTable :status="status" :tournament-name="currentName" />

    <RoundsList
      :status="status"
      @reload="loadStatus"
      @notify="notify"
    />

    <RulesPanel :rules="status.rules" />
  </template>

  <div v-else-if="currentId && loading" class="panel muted">加载中…</div>

  <CreateTournament @created="onCreated" v-if="currentId" compact />

  <transition><div v-if="toast" class="toast" :class="{ err: toast.isError }">{{ toast.message }}</div></transition>
</template>
