// 装维人员端 H5 专用 API 前缀（由 8011 在 /m/api 独立承载，与桌面端 /api/v1 隔离）
const BASE = '/m/api'
const TOKEN_KEY = 'hh_mp_token'
const USER_KEY = 'hh_mp_user'

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const getUser = () => {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null') } catch { return null }
}
const save = (token, user) => {
  localStorage.setItem(TOKEN_KEY, token)
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
}
export const logout = () => {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

async function request(path, { method = 'GET', body = null, timeout = 15000 } = {}) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeout)
  try {
    const res = await fetch(BASE + path, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(getToken() ? { Authorization: 'Bearer ' + getToken() } : {}),
      },
      body: body ? JSON.stringify(body) : null,
      signal: ctrl.signal,
    })
    if (res.status === 401) {
      logout()
      if (location.pathname.indexOf('/m/login') < 0) location.replace('/m/login')
      throw new Error('登录已过期')
    }
    if (!res.ok) {
      let msg = '请求失败（' + res.status + '）'
      try {
        const j = await res.json()
        if (j && j.detail) msg = typeof j.detail === 'string' ? j.detail : msg
      } catch { /* 忽略非 JSON */ }
      throw new Error(msg)
    }
    return await res.json()
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('网络超时，请重试')
    throw e
  } finally {
    clearTimeout(timer)
  }
}

export async function login(username, password) {
  const d = await request('/auth/login', { method: 'POST', body: { username, password } })
  save(d.access_token, { username })
  return d
}

export const mp = {
  overview: (scope = 'mine') => request('/mp/overview?scope=' + scope),
  orders: (kind, scope = 'mine', level = '') =>
    request(`/mp/orders?kind=${kind}&scope=${scope}${level ? '&level=' + level : ''}`),
  rewards: (kind, scope = 'mine') => request(`/mp/rewards?kind=${kind}&scope=${scope}`),
  tasks: (kind) => request(`/mp/tasks?kind=${kind}`),
  myTasks: () => request('/mp/my-tasks'),
  acceptTask: (id) => request('/mp/my-tasks/' + id + '/accept', { method: 'POST' }),
  submitTask: (id, comment = '') => request('/mp/my-tasks/' + id + '/submit', { method: 'POST', body: { comment } }),
  notices: () => request('/mp/notices'),
  notice: (id) => request('/mp/notices/' + id),
  sign: (id) => request(`/mp/notices/${id}/sign`, { method: 'POST' }),
  knowledge: (kw = '') => request('/mp/knowledge?keyword=' + encodeURIComponent(kw)),
}
