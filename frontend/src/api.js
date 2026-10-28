import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from './router'

// 默认 30s；大文件上传 / 邮件同步等慢请求在调用点单独传 timeout（见 DataSync.vue / Dashboard.vue）
const api = axios.create({ baseURL: '/api/v1', timeout: 30000 })

// 路由切换时取消仍在途的请求，避免旧页面慢响应回填造成的水流/闪烁
const __pending = new Set()
router.beforeEach((to, from) => {
  if (from.name) {
    __pending.forEach(ac => { try { ac.abort() } catch (e) {} })
    __pending.clear()
  }
  return true
})

api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('honghu_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  // params 空值过滤（手册 9.1）
  const __ac = new AbortController()
  cfg.signal = __ac.signal
  cfg.__ac = __ac
  __pending.add(__ac)
  if (cfg.params) {
    cfg.params = Object.fromEntries(Object.entries(cfg.params).filter(([, v]) => v !== '' && v !== null && v !== undefined))
  }
  return cfg
})

api.interceptors.response.use(
  res => { if (res.config && res.config.__ac) __pending.delete(res.config.__ac); return res.data },
  err => {
    if (err.name === 'CanceledError' || err.code === 'ERR_CANCELED') return Promise.reject(err)
    const status = err.response?.status
    const detail = err.response?.data?.detail
    if (status === 401) {
      localStorage.removeItem('honghu_token')
      localStorage.removeItem('honghu_user')
      if (router.currentRoute.value.path !== '/login') {
        ElMessage.warning('登录已过期，请重新登录')
        router.push('/login')
      }
    } else if (err.code === 'ECONNABORTED') {
      ElMessage.error('请求超时，请稍后重试或联系管理员')
    } else if (status >= 500) {
      ElMessage.error('服务端处理异常，请稍后重试；若反复出现请联系管理员')
    } else {
      ElMessage.error(typeof detail === 'string' ? detail : (detail?.[0]?.msg || '请求失败'))
    }
    return Promise.reject(err)
  }
)

export default api
