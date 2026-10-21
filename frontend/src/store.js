import { defineStore } from 'pinia'
import api from './api'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('honghu_token') || '',
    user: JSON.parse(localStorage.getItem('honghu_user') || 'null'),
  }),
  getters: {
    role: s => s.user?.role || '',
    isAdmin: s => ['super_admin', 'district_admin'].includes(s.user?.role || ''),
    isSuperAdmin: s => s.user?.role === 'super_admin',
  },
  actions: {
    async login(username, password) {
      const data = await api.post('/auth/login', { username, password })
      this.token = data.access_token
      this.user = data.user
      localStorage.setItem('honghu_token', data.access_token)
      localStorage.setItem('honghu_user', JSON.stringify(data.user))
      return data.user
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('honghu_token')
      localStorage.removeItem('honghu_user')
    },
    async refreshMe() {
      try {
        this.user = await api.get('/auth/me')
        localStorage.setItem('honghu_user', JSON.stringify(this.user))
      } catch (e) { /* 401 拦截器已处理 */ }
    },
  },
})
