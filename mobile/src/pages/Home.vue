<template>
  <div class="app" v-if="!loading && data">
    <!-- 头部 -->
    <header class="hero">
      <div class="hero-top">
        <div class="avatar">{{ initial }}</div>
        <div class="who">
          <div class="who-name">
            {{ profile.name }}
            <span class="who-tag">{{ profile.position }}</span>
          </div>
          <div class="who-meta">{{ profile.district }} · {{ profile.team }} · {{ shortGrid }}</div>
        </div>

        <!-- 四类提醒铃铛 -->
        <button class="bell" :class="{ ringing: alertTotal > 0, open: panelOpen }" @click.stop="togglePanel">
          <Icon name="bell" :size="19" />
          <span v-if="alertTotal > 0" class="bell-num">{{ alertTotal > 99 ? '99+' : alertTotal }}</span>
        </button>
      </div>


      <div class="seg">
        <button :class="{ on: seg === 'mine' }" @click="seg = 'mine'">我的</button>
        <button :class="{ on: seg === 'grid' }" @click="seg = 'grid'">本网格</button>
      </div>

      <div class="hero-month">
        <span>{{ data.month }} 绩效看板</span>
        <span @click="reload" style="display:flex;align-items:center;gap:4px;opacity:.9;white-space:nowrap;flex:0 0 auto">
          <Icon name="refresh" :size="13" /> 刷新
        </span>
      </div>
    </header>

      <!-- 提醒面板 -->
      <transition name="drop">
        <div v-if="panelOpen" class="alert-panel" @click.stop>
          <div class="ap-h">
            <span>提醒中心</span>
            <span class="ap-sub">{{ alertTotal > 0 ? ('共 ' + alertTotal + ' 条待处理') : '暂无待处理' }}</span>
          </div>
          <div class="ap-list">
            <button
              v-for="(it, i) in alertItems"
              :key="it.key"
              class="ap-item"
              :class="{ dim: it.state === 'none' }"
              :style="{ '--ac': ALERT_COLOR[it.key], '--i': i }"
              @click="goAlert(it)"
            >
              <span class="ap-ico"><Icon :name="ALERT_ICON[it.key]" :size="15" /></span>
              <span class="ap-txt">
                <span class="ap-label">{{ it.label }}</span>
                <span class="ap-hint">{{ it.state === 'none' ? '待接入' : it.hint }}</span>
              </span>
              <span class="ap-cnt" :class="{ zero: !it.count }">{{ it.count }}</span>
              <span class="arrow"><Icon name="right" :size="12" /></span>
            </button>
          </div>
          <div v-if="alertTop" class="ap-top">
            <Icon name="alert" :size="12" /> 最紧急：{{ alertTop }}
          </div>
        </div>
      </transition>
      <div v-if="panelOpen" class="ap-mask" @click="panelOpen = false"></div>


    <!-- 分组 -->
    <section v-for="(g, gi) in data.groups" :key="g.key" class="grp">
      <div class="grp-h">
        <span class="grp-t">{{ g.label }}</span>
        <span class="grp-line"></span>
      </div>
      <div class="grid">
        <button
          v-for="(c, ci) in g.cards"
          :key="c.key"
          class="card"
          :class="{ 'is-none': c.state === 'none', 'is-na': naOf(c) }"
          :style="{ '--c': GROUP_COLOR[g.key], '--i': (gi * 2 + ci) }"
          @click="open(c)"
        >
          <span class="arrow"><Icon name="right" /></span>
          <div class="card-h">
            <Icon :name="iconOf(c)" />
            <span>{{ c.label }}</span>
            <span v-if="(c.kind === 'notice' || c.kind === 'my_task') && numOf(c) > 0" class="badge-dot">{{ numOf(c) }}</span>
          </div>

          <template v-if="c.state === 'none'">
            <div class="v" style="font-size:17px;color:#cbd5e1">待接入</div>
          </template>
          <template v-else>
            <div class="card-v">
              <span class="v">{{ shown(c) }}</span>
              <span class="u" v-if="c.unit">{{ c.unit }}</span>
            </div>
            <div class="card-s">
              <span v-if="c.sub && seg === 'mine' && !hasBadge(c)" class="gbadge">{{ c.sub }}</span>
              <span v-if="hint(c)" class="gbadge">{{ hint(c) }}</span>
            </div>
          </template>
        </button>
      </div>
    </section>
  </div>

  <!-- 骨架 -->
  <div class="app" v-else-if="loading">
    <div class="hero" style="height:178px"></div>
    <div class="skeleton">
      <div class="sk" v-for="n in 8" :key="n"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { mp, getUser } from '../api'
import Icon from '../components/Icon.vue'

const router = useRouter()
const data = ref(null)
const loading = ref(true)
const seg = ref('mine')
const display = reactive({}) // 卡片动画数值
const panelOpen = ref(false)
const alerts = ref({ total: 0, items: [], top: '', urgent: 0 })
const alertTotal = computed(() => alerts.value.total || 0)
const alertItems = computed(() => alerts.value.items || [])
const alertTop = computed(() => alerts.value.top || '')

const GROUP_COLOR = { orders: '#2563eb', rewards: '#b45309', tasks: '#6d28d9', msg: '#1e3a8a' }
// 四类提醒的统一配色与图标（新类型在此加一行即可）
const ALERT_COLOR = { task: '#2563eb', notice: '#dc2626', kb: '#0891b2', warn: '#ea580c' }
const ALERT_ICON = { task: 'check', notice: 'bell', kb: 'book', warn: 'alert' }
const KIND_ICON = {
  pending_install: 'box', pending_repair: 'wrench', near_timeout: 'diamond',
  completed_month: 'check', timeout: 'alert', broad: 'repeat', ai_issue: 'alert',
  install_qc: 'tool', h5: 'star', qc: 'check', trace: 'repeat', port_clear: 'plug',
  inspection: 'building', care: 'heart', xf: 'wrench', my_task: 'check', notice: 'bell', knowledge: 'book',
}

const profile = computed(() => data.value?.profile || {})
const initial = computed(() => (profile.value.name || '装')[0])
const shortGrid = computed(() => (profile.value.grid || '').replace(/^洪湖/, '').replace(/网格$/, ''))

function iconOf(c) {
  if (c.kind === 'near_timeout' && c.level === 'platinum') return 'clock'
  return KIND_ICON[c.kind] || 'inbox'
}

// 当前展示的数值（我的=个人值，本网格=团队值）
// v1.6.6：网格视图下该指标本身没有网格口径（后端返回 grid=null，如 AI回访/安装质检
// 这类表没有 grid 列）时，**不能回退成个人值** —— 否则卡片显示个人的 2，
// 点进去 scope=grid 查出 0 条，就是「卡片有数、明细为空」。
// 跟人走、不参与网格口径的卡片（后端只按本人算，网格视图下同样正常显示）
const PERSONAL_ONLY = ['my_task', 'notice', 'knowledge']
function rawOf(c) {
  if (seg.value === 'mine') return c.value
  if (PERSONAL_ONLY.includes(c.kind)) return c.value   // 跟人走，没有网格值
  return c.grid == null ? null : c.grid
}
// 该卡片在当前视图下是否「口径不可算」→ 显示「—」并禁用点击
// 只对「我的 vs 本网格」会分出两个数的统计类指标生效
function naOf(c) {
  if (c.state === 'none') return false
  if (PERSONAL_ONLY.includes(c.kind)) return false
  return seg.value === 'grid' && c.grid == null
}
function numOf(c) {
  const v = rawOf(c)
  return typeof v === 'number' ? v : 0
}
function hasBadge(c) { return (c.kind === 'notice' || c.kind === 'my_task') && numOf(c) > 0 }

function shown(c) {
  if (c.state === 'none') return '待接入'
  if (naOf(c)) return '—'
  const v = display[c.key]
  if (v == null) return rawOf(c)
  const isPct = c.unit === '%'
  return isPct ? v.toFixed(1) : Math.round(v)
}
function hint(c) {
  if (naOf(c)) return '仅个人口径'
  if (seg.value === 'mine' && c.grid != null && c.grid !== c.value) return '本网格 ' + c.grid
  if (seg.value === 'grid' && c.grid != null && c.grid !== c.value) return '个人 ' + c.value
  return ''
}

// 数字滚动动画
function animate(key, target) {
  if (typeof target !== 'number') { display[key] = null; return }
  const dur = 620, start = performance.now()
  const from = typeof display[key] === 'number' ? display[key] : 0
  function step(now) {
    const t = Math.min(1, (now - start) / dur)
    const e = 1 - Math.pow(1 - t, 3)
    display[key] = from + (target - from) * e
    if (t < 1) requestAnimationFrame(step)
    else display[key] = target
  }
  requestAnimationFrame(step)
}
function animateAll() {
  data.value?.groups.forEach((g) => g.cards.forEach((c) => animate(c.key, rawOf(c))))
}

function open(c) {
  if (naOf(c)) return       // 网格视图下无网格口径，点进去必然是空列表
  if (c.kind === 'knowledge') { router.push('/knowledge'); return }
  router.push({
    path: '/detail',
    query: {
      kind: c.kind, level: c.level || '', label: c.label,
      scope: seg.value, state: c.state, group: '',
    },
  })
}

function togglePanel() { panelOpen.value = !panelOpen.value }

function goAlert(it) {
  panelOpen.value = false
  if (!it.count && it.state === 'none') return
  if (it.detail === 'knowledge') { router.push('/knowledge'); return }
  if (it.detail === 'alert_order') {
    router.push({ path: '/detail', query: { kind: 'warn', label: '工单预警', scope: 'grid', state: 'ok' } })
    return
  }
  router.push({ path: '/detail', query: { kind: it.detail, label: it.label, scope: 'mine', state: 'ok' } })
}

async function loadAlerts() {
  try { alerts.value = await mp.alerts() } catch (e) { /* 提醒失败不影响主看板 */ }
}

async function load() {
  loading.value = true
  try {
    const d = await mp.overview('mine')
    data.value = d
    animateAll()
    loadAlerts()
  } catch (e) {
    if (e.message !== '登录已过期') alert(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}
function reload() { load() }

watch(seg, () => animateAll())
onMounted(load)
</script>
<style scoped>
/* v1.6.6：网格视图下「仅个人口径」的卡片 —— 显示 —，弱化且不可点 */
.card.is-na { cursor: default; }
.card.is-na .v { color: #cbd5e1; }

/* ============ 四类提醒铃铛 ============ */
.bell {
  position: relative; z-index: 3; flex: 0 0 auto;
  width: 36px; height: 36px; border-radius: 12px;
  background: rgba(255, 255, 255, .16);
  border: 1px solid rgba(255, 255, 255, .28);
  color: #fff; display: flex; align-items: center; justify-content: center;
  transition: background .18s ease, transform .18s ease;
}
.bell:active { transform: scale(.94); }
.bell.open { background: rgba(255, 255, 255, .3); }
.bell.ringing { animation: bellRing 2.4s ease-in-out infinite; }
@keyframes bellRing {
  0%, 62%, 100% { transform: rotate(0); }
  68% { transform: rotate(-13deg); }
  74% { transform: rotate(11deg); }
  80% { transform: rotate(-8deg); }
  86% { transform: rotate(5deg); }
  92% { transform: rotate(0); }
}
.bell-num {
  position: absolute; top: -5px; right: -6px;
  min-width: 17px; height: 17px; padding: 0 4px; border-radius: 9px;
  background: #ef4444; color: #fff; font-size: 10px; font-weight: 800;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1.5px solid #1e3a8a; line-height: 1;
  box-shadow: 0 0 0 0 rgba(239, 68, 68, .7);
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, .65); }
  70% { box-shadow: 0 0 0 7px rgba(239, 68, 68, 0); }
  100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
}

/* 面板 */
.ap-mask { position: fixed; inset: 0; z-index: 20; background: rgba(15, 23, 42, .22); }
.alert-panel {
  position: fixed; z-index: 21; right: 14px; top: 58px; width: 268px;
  background: #fff; border-radius: 16px; padding: 12px 10px 10px;
  box-shadow: 0 18px 44px rgba(15, 23, 42, .3);
}
.alert-panel::before {
  content: ""; position: absolute; top: -6px; right: 20px;
  width: 12px; height: 12px; background: #fff; transform: rotate(45deg);
  border-radius: 2px;
}
.ap-h {
  display: flex; align-items: baseline; justify-content: space-between;
  padding: 1px 6px 9px; font-size: 13.5px; font-weight: 800; color: var(--ink);
}
.ap-sub { font-size: 10.5px; font-weight: 500; color: var(--ink-3); }
.ap-list { display: flex; flex-direction: column; gap: 5px; }
.ap-item {
  display: flex; align-items: center; gap: 9px; width: 100%;
  padding: 9px 8px; border-radius: 11px; background: #f8fafc;
  border: 1px solid transparent; text-align: left;
  animation: apIn .28s ease both; animation-delay: calc(var(--i) * 45ms);
}
.ap-item:active { background: #eef2f7; }
.ap-item.dim { opacity: .58; }
@keyframes apIn {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}
.ap-ico {
  flex: 0 0 26px; width: 26px; height: 26px; border-radius: 8px;
  background: color-mix(in srgb, var(--ac) 13%, #fff);
  color: var(--ac); display: flex; align-items: center; justify-content: center;
}
.ap-txt { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.ap-label { font-size: 13px; font-weight: 700; color: var(--ink); }
.ap-hint { font-size: 10px; color: var(--ink-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ap-cnt {
  flex: 0 0 auto; min-width: 22px; height: 20px; padding: 0 6px; border-radius: 7px;
  background: var(--ac); color: #fff; font-size: 11.5px; font-weight: 800;
  display: inline-flex; align-items: center; justify-content: center;
}
.ap-cnt.zero { background: #e2e8f0; color: #94a3b8; }
.ap-top {
  margin-top: 9px; padding: 7px 9px; border-radius: 9px;
  background: #fff7ed; color: #c2410c; font-size: 10.5px; font-weight: 600;
  display: flex; align-items: center; gap: 5px; line-height: 1.4;
}
/* 图标必须锁死尺寸，否则 flex 内 SVG 会被撑满 */
.ap-top svg, .ap-ico svg, .bell svg, .ap-item .arrow svg { flex: 0 0 auto; width: 1em; height: 1em; }
.ap-top svg { width: 12px; height: 12px; }
.bell svg { width: 19px; height: 19px; }
.ap-ico svg { width: 15px; height: 15px; }
.ap-item .arrow svg { width: 12px; height: 12px; }
.ap-top span { flex: 1; min-width: 0; }
/* 展开动画 */
.drop-enter-active, .drop-leave-active { transition: opacity .2s ease, transform .2s cubic-bezier(.34, 1.56, .64, 1); }
.drop-enter-from, .drop-leave-to { opacity: 0; transform: translateY(-10px) scale(.96); }
</style>
