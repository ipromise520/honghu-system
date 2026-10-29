const { mp, getToken } = require('../../utils/request.js')

const GROUP_COLOR = { orders: '#2563eb', rewards: '#b45309', tasks: '#6d28d9', msg: '#1e3a8a' }
const KIND_EMOJI = {
  pending_install: '📦', pending_repair: '🔧', near_timeout: '⏰', completed_month: '✅',
  timeout: '⚠️', broad: '🔁', ai_issue: '📞', install_qc: '🛠️', h5: '⭐', qc: '✔️',
  trace: '🔄', port_clear: '🔌', inspection: '🏢', care: '❤️', xf: '🔧',
  my_task: '📌', notice: '🔔', knowledge: '📚',
}

Page({
  data: { loading: true, data: null, seg: 'mine', initial: '', profile: {}, shortGrid: '' },

  onLoad() { this.load() },
  onShow() {
    if (!getToken()) { wx.reLaunch({ url: '/pages/login/login' }); return }
    if (this.data.data) this.load()
  },
  onPullDownRefresh() { this.load() },

  async load() {
    this.setData({ loading: true })
    try {
      const d = await mp.overview('mine')
      const p = d.profile || {}
      d.groups.forEach((g) => { g.color = GROUP_COLOR[g.key] || '#2563eb' })
      this.setData({
        data: d, loading: false,
        initial: (p.name || '装')[0],
        profile: p,
        shortGrid: (p.grid || '').replace(/^洪湖/, '').replace(/网格$/, ''),
      })
    } catch (e) {
      if (e.message !== '登录已过期') wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
      wx.stopPullDownRefresh()
    }
  },

  setSeg(e) { this.setData({ seg: e.currentTarget.dataset.s }) },
  rawOf(c) {
    const v = this.data.seg === 'mine' ? c.value : (c.grid == null ? c.value : c.grid)
    return typeof v === 'number' ? v : 0
  },
  numOf(c) { return this.rawOf(c) },
  shown(c) {
    if (c.state === 'none') return '待接入'
    const v = this.rawOf(c)
    if (c.unit === '%') return Number(v).toFixed(1)
    return v
  },
  hint(c) {
    if (this.data.seg === 'mine' && c.grid != null && c.grid !== c.value) return '本网格 ' + c.grid
    if (this.data.seg === 'grid' && c.grid != null && c.grid !== c.value) return '个人 ' + c.value
    return ''
  },
  hasBadge(c) { return (c.kind === 'notice' || c.kind === 'my_task') && this.numOf(c) > 0 },
  iconLabel(c) { return KIND_EMOJI[c.kind] || '📋' },
  open(e) {
    const c = e.currentTarget.dataset.c
    if (c.kind === 'knowledge') { wx.navigateTo({ url: '/pages/knowledge/knowledge' }); return }
    const q = `kind=${encodeURIComponent(c.kind)}&level=${encodeURIComponent(c.level || '')}&label=${encodeURIComponent(c.label)}&scope=${this.data.seg}&state=${c.state}`
    wx.navigateTo({ url: '/pages/detail/detail?' + q })
  },
})
