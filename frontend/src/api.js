import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from './router'

const api = axios.create({ baseURL: '/api/v1', timeout: 180000 })

api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('honghu_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  // params 空值过滤（手册 9.1）
  if (cfg.params) {
    cfg.params = Object.fromEntries(Object.entries(cfg.params).filter(([, v]) => v !== '' && v !== null && v !== undefined))
  }
  return cfg
})

api.interceptors.response.use(
  res => res.data,
  err => {
    const status = err.response?.status
    const detail = err.response?.data?.detail
    if (status === 401) {
      localStorage.removeItem('honghu_token')
      localStorage.removeItem('honghu_user')
      if (router.currentRoute.value.path !== '/login') {
        ElMessage.warning('登录已过期，请重新登录')
        router.push('/login')
      }
    } else {
      ElMessage.error(typeof detail === 'string' ? detail : (detail?.[0]?.msg || '请求失败'))
    }
    return Promise.reject(err)
  }
)

export default api
