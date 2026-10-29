const { mp } = require('../../utils/request.js')

Page({
  data: { kw: '', rows: [], loading: false },
  onLoad() { this.search() },
  onInput(e) {
    this.setData({ kw: e.detail.value })
    clearTimeout(this._t)
    this._t = setTimeout(() => this.search(), 300)
  },
  async search() {
    this.setData({ loading: true })
    try {
      const d = await mp.knowledge(this.data.kw)
      this.setData({ rows: d.rows || [] })
    } catch (e) {
      if (e.message !== '登录已过期') wx.showToast({ title: e.message || '检索失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },
  goBack() { wx.navigateBack() },
})
