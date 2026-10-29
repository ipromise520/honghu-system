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
      </div>

      <div class="seg">
        <button :class="{ on: seg === 'mine' }" @click="seg = 'mine'">我的</button>
        <button :class="{ on: seg === 'grid' }" @click="seg = 'grid'">本网格</button>
      </div>

      <div class="hero-month">
        <span>{{ data.month }} 绩效看板</span>
        <span @click="reload" style="display:flex;align-items:center;gap:4px;opacity:.9">
          <Icon name="refresh" :size="13" /> 刷新
        </span>
      </div>
    </header>

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
          :class="{ 'is-none': c.state === 'none' }"
          :style="{ '--c': GROUP_COLOR[g.key], '--i': (gi * 2 + ci) }"
          @click="open(c)"
        >
          <span class="arrow"><Icon name="right" /></span>
          <div class="card-h">
            <Icon :name="iconOf(c)" />
            <span>{{ c.label }}</span>
            <span v-if="c.kind === 'notice' && numOf(c) > 0" class="badge-dot">{{ numOf(c) }}</span>
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
              <span v-if="c.sub && seg === 'mine'" class="gbadge">{{ c.sub }}</span>
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

const GROUP_COLOR = { orders: '#2563eb', rewards: '#b45309', tasks: '#6d28d9', msg: '#1e3a8a' }
const KIND_ICON = {
  pending_install: 'box', pending_repair: 'wrench', near_timeout: 'diamond',
  completed_month: 'check', timeout: 'alert', broad: 'repeat', ai_issue: 'alert',
  install_qc: 'tool', h5: 'star', qc: 'check', trace: 'repeat', port_clear: 'plug',
  inspection: 'building', care: 'heart', xf: 'wrench', notice: 'bell', knowledge: 'book',
}

const profile = computed(() => data.value?.profile || {})
const initial = computed(() => (profile.value.name || '装')[0])
const shortGrid = computed(() => (profile.value.grid || '').replace(/^洪湖/, '').replace(/网格$/, ''))

function iconOf(c) {
  if (c.kind === 'near_timeout' && c.level === 'platinum') return 'clock'
  return KIND_ICON[c.kind] || 'inbox'
}

// 当前展示的数值（我的=个人值，本网格=团队值）
function rawOf(c) {
  if (seg.value === 'mine') return c.value
  return c.grid == null ? c.value : c.grid
}
function numOf(c) {
  const v = rawOf(c)
  return typeof v === 'number' ? v : 0
}
function shown(c) {
  if (c.state === 'none') return '待接入'
  const v = display[c.key]
  if (v == null) return rawOf(c)
  const isPct = c.unit === '%'
  return isPct ? v.toFixed(1) : Math.round(v)
}
function hint(c) {
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
  if (c.kind === 'knowledge') { router.push('/knowledge'); return }
  router.push({
    path: '/detail',
    query: {
      kind: c.kind, level: c.level || '', label: c.label,
      scope: seg.value, state: c.state, group: '',
    },
  })
}

async function load() {
  loading.value = true
  try {
    const d = await mp.overview('mine')
    data.value = d
    animateAll()
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
