const { mp } = require('../../utils/request.js')

Page({
  data: { id: null, doc: null, loading: true, signing: false },
  onLoad(o) { this.setData({ id: o.id }); this.load() },
  async load() {
    this.setData({ loading: true })
    try {
      const d = await mp.notice(this.data.id)
      this.setData({ doc: d })
    } catch (e) {
      if (e.message !== '登录已过期') wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },
  async doSign() {
    this.setData({ signing: true })
    try {
      const d = await mp.sign(this.data.id)
      this.setData({ 'doc.signed': true, 'doc.signed_at': d.signed_at || '刚刚' })
    } catch (e) {
      wx.showToast({ title: e.message || '签收失败', icon: 'none' })
    } finally {
      this.setData({ signing: false })
    }
  },
  goBack() { wx.navigateBack() },
})
