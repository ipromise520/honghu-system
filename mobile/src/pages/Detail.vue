<template>
  <div class="sheet" v-if="!loading && ready">
    <!-- 顶部栏 -->
    <div class="bar-top">
      <button class="icon-btn" @click="goBack"><Icon name="back" /></button>
      <h1>{{ title }}</h1>
      <span style="width:34px"></span>
    </div>

    <!-- 待接入 -->
    <div class="body" v-if="state === 'none'">
      <div class="empty">
        <div class="ico"><Icon name="tool" :size="54" /></div>
        <h3>功能待接入</h3>
        <p>该指标数据源尚未接入，<br />上线后将在此展示明细。</p>
      </div>
    </div>

    <!-- 通知列表（kind=notice） -->
    <template v-else-if="kind === 'notice'">
      <div class="body">
        <p class="note">共 {{ list.length }} 条通知，点击查看并签收。</p>
        <div
          v-for="(n, i) in list"
          :key="n.id"
          class="row"
          :style="{ '--i': i }"
          @click="openNotice(n)"
        >
          <div class="row-h">
            <span class="row-no" style="font-family:inherit;font-size:14px">{{ n.title }}</span>
            <span v-if="!n.signed" class="row-tag danger">未签</span>
            <span v-else class="row-tag ok">已签</span>
          </div>
          <div class="row-kv">
            <span><span class="k">分类</span> {{ n.category }}</span>
            <span><span class="k">时间</span> {{ n.time }}</span>
          </div>
          <div class="row-kv"><span style="color:var(--ink-3)">{{ n.digest }}</span></div>
        </div>
      </div>
    </template>

    <!-- 我的任务（kind=my_task） -->
    <template v-else-if="kind === 'my_task'">
      <div class="body">
        <p class="note">{{ tasks.length ? ('共 ' + tasks.length + ' 个任务，待办 ' + openCount + ' 个。') : '暂无指派给您的任务。' }}</p>

        <div v-if="tasks.length === 0" class="empty">
          <div class="ico"><Icon name="inbox" :size="54" /></div>
          <h3>暂无任务</h3>
          <p>服务端「任务分派」指派给您后，会出现在这里。</p>
        </div>

        <div v-for="(t, i) in tasks" :key="t.id" class="row task" :style="{ '--i': i }">
          <div class="row-h">
            <span class="row-no" style="font-family:inherit;font-size:14px">{{ t.title }}</span>
            <span class="row-tag" :class="taskClass(t)">{{ t.status_name }}</span>
          </div>
          <div class="row-kv">
            <span class="row-tag" :class="t.priority === 'urgent' ? 'danger' : 'warn'">{{ t.priority_name }}</span>
            <span><span class="k">截止</span> {{ t.deadline || '—' }}</span>
            <span><span class="k">区县</span> {{ t.district || '—' }}</span>
          </div>
          <div class="row-kv">
            <span><span class="k">派单人</span> {{ t.creator || '—' }}</span>
            <span><span class="k">时间</span> {{ t.created_at }}</span>
          </div>
          <div v-if="t.content" class="row-kv"><span style="color:var(--ink-2)">{{ t.content }}</span></div>
          <div class="task-ops" v-if="t.status === 'assigned' || t.status === 'accepted' || t.status === 'rejected'">
            <button v-if="t.status !== 'accepted'" class="btn-sm" :disabled="busy === t.id" @click="acceptTask(t)">接单</button>
            <button class="btn-sm primary" :disabled="busy === t.id" @click="submitTask(t)">提交验收</button>
          </div>
        </div>
      </div>
    </template>

    <!-- 普通明细 -->
    <template v-else>
      <div class="tabs" v-if="canToggle">
        <button :class="{ on: scope === 'mine' }" @click="scope = 'mine'">我的</button>
        <button :class="{ on: scope === 'grid' }" @click="scope = 'grid'">本网格</button>
      </div>
      <div class="body">
        <p class="note" v-if="source">{{ source }}</p>

        <div v-if="rows.length === 0" class="empty">
          <div class="ico"><Icon name="inbox" :size="54" /></div>
          <h3>暂无数据</h3>
          <p>当前范围下没有符合条件的记录。</p>
        </div>

        <template v-else>
          <!-- H5 平铺 -->
          <div v-if="flat" class="row" style="animation:none">
            <div v-for="(r, i) in rows" :key="i" style="display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line)" :style="{ borderBottom: i===rows.length-1 ? 'none':'1px solid var(--line)' }">
              <span style="color:var(--ink-2);font-size:13px">{{ r.label }}</span>
              <span style="font-weight:700;color:var(--ink)">{{ r.value }}</span>
            </div>
          </div>

          <!-- 快超时卡片 -->
          <div v-else-if="kind === 'near_timeout'" v-for="(r, i) in rows" :key="i" class="row" :style="{ '--i': i }">
            <div class="row-h">
              <span class="row-no">{{ r.no }}</span>
              <span class="row-tag" :class="r.left_min <= 0 ? 'danger' : 'warn'">{{ r.left_text }}</span>
            </div>
            <div class="row-owner">
              <span class="owner-label">责任人</span>
              <span class="owner-name">{{ r.handler || '未派单' }}</span>
              <span v-if="r.supervise" class="owner-sup">督办 {{ r.supervise }}</span>
              <span class="row-tag" :class="r.level === 'diamond' ? 'danger' : 'warn'">{{ r.level_name }}</span>
            </div>
            <div class="row-kv">
              <span><span class="k">小区</span> {{ r.community || '—' }}</span>
              <span><span class="k">状态</span> {{ r.status }}</span>
            </div>
            <div class="row-kv">
              <span><span class="k">预约</span> {{ r.appoint }}</span>
              <span><span class="k">网格</span> {{ r.grid }}</span>
            </div>
          </div>

          <!-- 通用列表 -->
          <template v-else>
            <div v-for="(r, i) in rows" :key="i" class="row" :style="{ '--i': i }">
              <div class="row-h">
                <span class="row-no">{{ r[titleKey] }}</span>
                <span v-if="statusOf(r)" class="row-tag" :class="statusClass(r)">{{ statusOf(r) }}</span>
              </div>
              <div class="row-kv" v-for="(kv, j) in kvOf(r)" :key="j">
                <span><span class="k">{{ kv.k }}</span> {{ kv.v }}</span>
              </div>
            </div>
          </template>
        </template>
      </div>
    </template>
  </div>

  <div class="sheet" v-else-if="loading">
    <div class="bar-top"><span style="width:34px"></span><h1>{{ title }}</h1><span style="width:34px"></span></div>
    <div class="skeleton"><div class="sk" v-for="n in 6" :key="n"></div></div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { mp } from '../api'
import Icon from '../components/Icon.vue'

const route = useRoute()
const router = useRouter()

const kind = computed(() => route.query.kind || '')
const level = computed(() => route.query.level || '')
const title = computed(() => route.query.label || '明细')
const state = computed(() => route.query.state || '')
const scope = ref(route.query.scope || 'mine')

const ORDERS = ['pending_install', 'pending_repair', 'near_timeout', 'completed_month']
const canToggle = computed(() => kind.value !== 'notice' && kind.value !== 'my_task' && kind.value !== 'warn' && kind.value !== '')

const loading = ref(true)
const ready = ref(false)
const source = ref('')
const flat = ref(false)
const titleKey = ref('no')
const rows = ref([])
const list = ref([]) // 通知列表
const tasks = ref([]) // 我的任务
const openCount = ref(0)
const busy = ref(0)

function statusOf(r) {
  return r.status || (r.signed === false ? '未签' : r.signed === true ? '已签' : '')
}
function statusClass(r) {
  if (r.signed === true) return 'ok'
  if (r.signed === false) return 'danger'
  if (r.status === '已施工' || r.status === '已完工') return 'ok'
  return 'warn'
}
function kvOf(r) {
  const cols = (r.__cols || []).slice(1)
  const out = cols.map((c) => ({ k: c.label, v: r[c.key] })).filter((x) => x.v !== undefined && x.v !== '')
  if (r.district) out.push({ k: '区县', v: r.district })
  if (r.grid) out.push({ k: '网格', v: r.grid })
  if (r.accept) out.push({ k: '受理', v: r.accept })
  return out
}

async function load() {
  loading.value = true
  ready.value = false
  try {
    if (kind.value === 'notice') {
      const d = await mp.notices()
      list.value = d.rows || []
      source.value = ''
    } else if (kind.value === 'warn') {
      const d = await mp.warnOrders()
      rows.value = (d.rows || []).map((r) => ({ ...r, __cols: d.columns }))
      titleKey.value = d.columns?.[0]?.key || 'no'
      source.value = d.source || ''
      flat.value = false
    } else if (kind.value === 'my_task') {
      const d = await mp.myTasks()
      tasks.value = d.rows || []
      openCount.value = d.open || 0
      source.value = ''
    } else if (ORDERS.includes(kind.value)) {
      const d = await mp.orders(kind.value, scope.value, level.value)
      rows.value = (d.rows || []).map((r) => ({ ...r, __cols: d.columns }))
      titleKey.value = d.columns?.[0]?.key || 'no'
      source.value = d.source || ''
      flat.value = false
    } else {
      const d = await mp.rewards(kind.value, scope.value)
      rows.value = (d.rows || []).map((r) => ({ ...r, __cols: d.columns }))
      titleKey.value = d.columns?.[0]?.key || 'no'
      source.value = d.source || ''
      flat.value = !!d.flat
    }
    ready.value = true
  } catch (e) {
    if (e.message !== '登录已过期') alert(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function taskClass(t) {
  if (t.status === 'completed') return 'ok'
  if (t.status === 'submitted') return 'warn'
  if (t.status === 'rejected') return 'danger'
  return 'warn'
}
async function acceptTask(t) {
  busy.value = t.id
  try {
    await mp.acceptTask(t.id)
    t.status = 'accepted'
    t.status_name = '进行中'
    t.open = true
    openCount.value = tasks.value.filter((x) => x.open).length
  } catch (e) {
    if (e.message !== '登录已过期') alert(e.message || '接单失败')
  } finally {
    busy.value = 0
  }
}
async function submitTask(t) {
  busy.value = t.id
  try {
    await mp.submitTask(t.id)
    t.status = 'submitted'
    t.status_name = '待验收'
    t.open = false
    openCount.value = tasks.value.filter((x) => x.open).length
  } catch (e) {
    if (e.message !== '登录已过期') alert(e.message || '提交失败')
  } finally {
    busy.value = 0
  }
}
function openNotice(n) { router.push('/notice/' + n.id) }
function goBack() { router.back() }

watch(scope, () => { if (canToggle.value) load() })
onMounted(load)
</script>

<style scoped>
/* 我的任务：操作按钮行 */
.task-ops {
  display: flex; justify-content: flex-end; gap: 8px;
  margin-top: 10px; padding-top: 9px; border-top: 1px dashed var(--line);
}
.btn-sm {
  height: 30px; padding: 0 15px; border-radius: 9px; border: 1px solid var(--line);
  background: #fff; color: var(--ink-2); font-size: 12.5px; font-weight: 600;
  transition: transform .12s ease, background .15s ease;
}
.btn-sm.primary {
  background: linear-gradient(135deg, #2563eb, #1d4ed8); border-color: transparent;
  color: #fff; box-shadow: 0 3px 10px rgba(37, 99, 235, .28);
}
.btn-sm:active { transform: scale(.96); }
.btn-sm[disabled] { opacity: .55; }
.task { border-left: 3px solid var(--brand); }

/* 责任人（便于网格长按人督办） */
.row-owner { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; margin: 7px 0 3px; }
.owner-label {
  font-size: 10.5px; color: #92400e; background: #fef3c7;
  padding: 2px 6px; border-radius: 5px; font-weight: 700;
}
.owner-name { font-size: 14px; font-weight: 800; color: var(--ink); letter-spacing: .3px; }
.owner-sup { font-size: 10.5px; color: var(--ink-3); }
</style>
