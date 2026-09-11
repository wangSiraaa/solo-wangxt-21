<script setup>
defineProps({ rules: { type: Object, required: true } })
</script>

<template>
  <div class="panel">
    <h2>赛事规则（锁定版本）</h2>
    <div class="small muted" style="margin-bottom:8px">
      每轮发布时把规则全文写入快照；升级规则不影响历史轮次。当前版本
      <span class="mono badge muted">{{ rules.version }}</span>
    </div>
    <h3>硬规则（求解器绝不违反）</h3>
    <div v-for="h in rules.hard_constraints" :key="h.code" class="tier-row">
      <span class="badge ok">硬</span>
      <div>
        <strong class="small mono">{{ h.code }}</strong>
        <div class="small muted">{{ h.description }}</div>
      </div>
    </div>
    <h3>软约束（字典序分层优化，被放宽会显式报告）</h3>
    <div v-for="s in rules.soft_constraints" :key="s.tier" class="tier-row">
      <span class="tier-tag">{{ s.tier }}</span>
      <div>
        <strong class="small mono">{{ s.code }}</strong>
        <div class="small muted">{{ s.description }}</div>
      </div>
    </div>
    <h3>并列与小分顺序</h3>
    <ol class="small muted" style="margin:4px 0 0;padding-left:20px">
      <li v-for="t in rules.tiebreaks" :key="t.order">
        <span class="mono">{{ t.code }}</span> — {{ t.description }}
      </li>
    </ol>
    <p class="small muted" style="margin:8px 0 0">{{ rules.ranking }}</p>
  </div>
</template>
