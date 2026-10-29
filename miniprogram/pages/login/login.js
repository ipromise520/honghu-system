const { login, getToken } = require('../../utils/request.js')
const { DEMO } = require('../../config.js')

Page({
  data: { username: '', password: '', loading: false, err: '' },
  onLoad() {
    if (getToken()) wx.reLaunch({ url: '/pages/index/index' })
  },
  onU(e) { this.setData({ username: e.detail.value }) },
  onP(e) { this.setData({ password: e.detail.value }) },
  fillDemo() { this.setData({ username: DEMO.username, password: DEMO.password }) },
  async submit() {
    const { username, password } = this.data
    if (!username || !password) { this.setData({ err: '请填写账号和密码' }); return }
    this.setData({ err: '', loading: true })
    try {
      await login(username, password)
      wx.reLaunch({ url: '/pages/index/index' })
    } catch (e) {
      this.setData({ err: e.message || '登录失败' })
    } finally {
      this.setData({ loading: false })
    }
  },
})
