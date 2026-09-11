<script setup>
import { reactive, ref } from 'vue'
import { api } from '../api.js'

const props = defineProps({ compact: Boolean })
const emit = defineEmits(['created', 'notify'])

const open = ref(false)
const form = reactive({
  name: '',
  total_rounds: 5,
  players: [{ name: '', rating: 1600 }],
})

function addRow() {
  form.players.push({ name: '', rating: 1600 })
}
function removeRow(i) {
  form.players.splice(i, 1)
}

async function submit() {
  const players = form.players
    .map((p) => ({ name: p.name.trim(), rating: Number(p.rating) || 1000 }))
    .filter((p) => p.name)
  if (!form.name.trim() || players.length < 2) {
    emit('notify', '请填写赛事名并至少录入 2 名选手', true)
    return
  }
  try {
    const t = await api.createTournament({
      name: form.name.trim(),
      total_rounds: Number(form.total_rounds),
      players,
    })
    form.name = ''
    form.players = [{ name: '', rating: 1600 }]
    open.value = false
    emit('created', t.id)
  } catch (e) {
    emit('notify', e.message, true)
  }
}
</script>

<template>
  <div class="panel" v-if="compact">
    <div class="row">
      <h2 style="margin:0">新建赛事 / 批量报名</h2>
      <div class="spacer"></div>
      <button class="small" @click="open = !open">{{ open ? '收起' : '展开' }}</button>
    </div>
    <div v-if="open" style="margin-top:12px">
      <CreateForm :form="form" @add="addRow" @remove="removeRow" @submit="submit" />
    </div>
  </div>
  <div class="panel" v-else>
    <h2>新建赛事 / 批量报名</h2>
    <CreateForm :form="form" @add="addRow" @remove="removeRow" @submit="submit" />
  </div>
</template>

<script>
import { defineComponent, h } from 'vue'
const CreateForm = defineComponent({
  props: { form: Object },
  emits: ['add', 'remove', 'submit'],
  setup(props, { emit }) {
    return () =>
      h('div', null, [
        h('div', { class: 'row', style: 'margin-bottom:10px' }, [
          h('label', '赛事名'),
          h('input', {
            value: props.form.name, style: 'flex:1',
            onInput: (e) => (props.form.name = e.target.value),
            placeholder: '如：国庆周末杯',
          }),
          h('label', '轮数'),
          h('input', {
            type: 'number', min: 1, max: 20, style: 'width:80px',
            value: props.form.total_rounds,
            onInput: (e) => (props.form.total_rounds = e.target.value),
          }),
        ]),
        h('table', null, [
          h('tr', null, [h('th', '报名号'), h('th', '姓名'), h('th', '等级分'), h('th', '')]),
          ...props.form.players.map((p, i) =>
            h('tr', { key: i }, [
              h('td', { class: 'muted num' }, i + 1),
              h('td', null, h('input', {
                value: p.name, style: 'width:90%',
                onInput: (e) => (p.name = e.target.value),
              })),
              h('td', null, h('input', {
                type: 'number', value: p.rating, style: 'width:100px',
                onInput: (e) => (p.rating = e.target.value),
              })),
              h('td', null, h('button', {
                class: 'small danger', onClick: () => emit('remove', i),
              }, '删除')),
            ])
          ),
        ]),
        h('div', { class: 'row', style: 'margin-top:10px' }, [
          h('button', { onClick: () => emit('add') }, '+ 添加选手'),
          h('div', { class: 'spacer' }),
          h('button', { class: 'primary', onClick: () => emit('submit') }, '创建并报名'),
        ]),
      ])
  },
})
export default { components: { CreateForm } }
</script>
