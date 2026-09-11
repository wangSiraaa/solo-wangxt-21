const BASE = ''

async function req(path, options = {}) {
  const r = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const text = await r.text()
  const data = text ? JSON.parse(text) : null
  if (!r.ok) {
    const detail = data?.detail
    let msg = typeof detail === 'string' ? detail : data?.detail?.message || `HTTP ${r.status}`
    const err = new Error(msg)
    err.payload = typeof detail === 'object' ? detail : data
    err.status = r.status
    throw err
  }
  return data
}

export const api = {
  listTournaments: () => req('/tournaments'),
  getStatus: (id, { preview = false, override = false, reason = '' } = {}) => {
    const q = new URLSearchParams()
    if (preview) q.set('preview', 'true')
    if (override) q.set('override_no_repeat', 'true')
    if (reason) q.set('override_reason', reason)
    const qs = q.toString()
    return req(`/tournaments/${id}/status${qs ? `?${qs}` : ''}`)
  },
  createTournament: (payload) => req('/tournaments', {
    method: 'POST', body: JSON.stringify(payload),
  }),
  publishRound: (id, { override = false, reason = '' } = {}) =>
    req(`/tournaments/${id}/rounds/publish`, {
      method: 'POST', body: JSON.stringify({
        override_no_repeat: override, override_reason: reason,
      }),
    }),
  enterResult: (gameId, result) => req(`/games/${gameId}/result`, {
    method: 'POST', body: JSON.stringify({ result, entered_by: '裁判工作台' }),
  }),
  correctResult: (gameId, newResult, reason) =>
    req(`/games/${gameId}/correction`, {
      method: 'POST',
      body: JSON.stringify({ new_result: newResult, reason, created_by: '裁判长' }),
    }),
}

export const RESULT_LABEL = { W: '白胜', B: '黑胜', D: '和棋', BYE: '轮空' }
