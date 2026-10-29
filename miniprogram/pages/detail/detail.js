const { mp } = require('../../utils/request.js')
const ORDERS = ['pending_install', 'pending_repair', 'near_timeout', 'completed_month']

Page({
  data: {
    kind: '', level: '', title: '明细', state: '', scope: 'mine',
    loading: true, ready: false, source: '', flat: false, titleKey: 'no',
    rows: [], list: [], tasks: [], openCount: 0, busy: 0, canToggle: false,
  },

  onLoad(o) {
    const kind = o.kind || ''
    const level = o.level || ''
    const title = o.label || '明细'
    const state = o.state || ''
    const scope = o.scope || 'mine'
    this.setData({
      kind, level, title, state, scope,
      canToggle: kind !== 'notice' && kind !== 'my_task' && kind !== '',
    })
    this.load()
  },

  async load() {
    this.setData({ loading: true, ready: false })
    const { kind, level, scope } = this.data
    try {
      if (kind === 'notice') {
        const d = await mp.notices()
        this.setData({ list: d.rows || [], ready: true })
      } else if (kind === 'my_task') {
        const d = await mp.myTasks()
        this.setData({
          tasks: (d.rows || []).map((t) => ({ ...t, cls: this.taskClass(t) })),
          openCount: d.open || 0, ready: true, source: '',
        })
      } else if (ORDERS.includes(kind)) {
        const d = await mp.orders(kind, scope, level)
        this.setData({
          rows: this.buildRows(d.rows || [], d.columns),
          titleKey: d.columns ? d.columns[0].key : 'no',
          source: d.source || '', flat: false, ready: true,
        })
      } else {
        const d = await mp.rewards(kind, scope)
        this.setData({
          rows: this.buildRows(d.rows || [], d.columns),
          titleKey: d.columns ? d.columns[0].key : 'no',
          source: d.source || '', flat: !!d.flat, ready: true,
        })
      }
    } catch (e) {
      if (e.message !== '登录已过期') wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  buildRows(rows, cols) {
    if (!cols) return rows.map((r, i) => ({ ...r, __id: i, _title: r.no, _cells: [], _status: '', _statusClass: '' }))
    const titleKey = cols[0].key
    return rows.map((r, i) => {
      const cells = cols.slice(1)
        .map((c) => ({ k: c.label, v: r[c.key] }))
        .filter((x) => x.v !== undefined && x.v !== '')
      if (r.district) cells.push({ k: '区县', v: r.district })
      if (r.grid) cells.push({ k: '网格', v: r.grid })
      if (r.accept) cells.push({ k: '受理', v: r.accept })
      let status = r.status || ''
      let cls = 'warn'
      if (r.status === '已施工' || r.status === '已完工') cls = 'ok'
      return { ...r, __id: i, _title: r[titleKey], _cells: cells, _status: status, _statusClass: cls }
    })
  },

  setScope(e) {
    const s = e.currentTarget.dataset.s
    if (s !== this.data.scope) { this.setData({ scope: s }); this.load() }
  },
  taskClass(t) {
    if (t.status === 'completed') return 'ok'
    if (t.status === 'submitted') return 'warn'
    if (t.status === 'rejected') return 'danger'
    return 'warn'
  },
  async acceptTask(e) {
    const t = e.currentTarget.dataset.t
    this.setData({ busy: t.id })
    try {
      await mp.acceptTask(t.id)
      const tasks = this.data.tasks.map((x) =>
        x.id === t.id ? { ...x, status: 'accepted', status_name: '进行中', open: true, cls: 'warn' } : x)
      this.setData({ tasks, openCount: tasks.filter((x) => x.open).length })
      wx.showToast({ title: '已接单', icon: 'success' })
    } catch (err) {
      if (err.message !== '登录已过期') wx.showToast({ title: err.message || '接单失败', icon: 'none' })
    } finally {
      this.setData({ busy: 0 })
    }
  },
  async submitTask(e) {
    const t = e.currentTarget.dataset.t
    this.setData({ busy: t.id })
    try {
      await mp.submitTask(t.id)
      const tasks = this.data.tasks.map((x) =>
        x.id === t.id ? { ...x, status: 'submitted', status_name: '待验收', open: false, cls: 'warn' } : x)
      this.setData({ tasks, openCount: tasks.filter((x) => x.open).length })
      wx.showToast({ title: '已提交验收', icon: 'success' })
    } catch (err) {
      if (err.message !== '登录已过期') wx.showToast({ title: err.message || '提交失败', icon: 'none' })
    } finally {
      this.setData({ busy: 0 })
    }
  },
  openNotice(e) { wx.navigateTo({ url: '/pages/notice/notice?id=' + e.currentTarget.dataset.n.id }) },
  goBack() { wx.navigateBack() },
})
