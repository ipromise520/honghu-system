<template>
  <el-container style="height: 100vh">
    <el-aside v-if="!isMobile" width="210px" style="background: var(--hh-sidebar)">
      <div class="logo">
        <el-icon size="22" color="#4da1ff"><Platform /></el-icon>
        <div>
          <div class="logo-title">洪湖支撑服务中心</div>
          <div class="logo-sub">Web8011 运维平台</div>
        </div>
      </div>
      <el-menu :default-active="$route.path" router background-color="#1d2b3a" text-color="#aeb9c5"
               active-text-color="#ffffff" style="border-right: none">
        <template v-for="m in menus" :key="m.path">
          <el-menu-item :index="m.path">
            <el-icon><component :is="m.icon" /></el-icon>
            <span>{{ m.title }}</span>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <div class="left">
          <el-icon v-if="isMobile" size="20" class="hamburger" @click="drawer = true"><Menu /></el-icon>
          <div class="crumb">{{ $route.meta.title || '' }}</div>
        </div>
        <div class="right">
          <el-tag v-if="user" type="primary" effect="plain" size="small">{{ user.role_name }}</el-tag>
          <span class="uname">{{ user?.real_name }}（{{ user?.username }}）</span>
          <el-dropdown @command="onCmd">
            <el-avatar :size="32" style="background:#1a6fdb;cursor:pointer">{{ user?.real_name?.[0] }}</el-avatar>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="pwd">修改密码</el-dropdown-item>
                <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <div v-if="overdueCount > 0" class="alert-banner" @click="$router.push('/h5-monitor')">
        <el-icon><WarningFilled /></el-icon>
        H5 超时未收预警：当前有 <b>{{ overdueCount }}</b> 单超时 {{ OVERDUE_HOURS }} 小时未收回执，请点击处理
      </div>
      <el-main class="main-area" style="background:#f5f7fa; padding:16px; overflow:auto">
        <router-view />
      </el-main>
    </el-container>

    <!-- 移动端抽屉菜单 -->
    <el-drawer v-model="drawer" direction="ltr" size="76%" :with-header="false" append-to-body>
      <div style="background:#1d2b3a; height:100%; overflow-y:auto">
        <div class="logo">
          <el-icon size="22" color="#4da1ff"><Platform /></el-icon>
          <div>
            <div class="logo-title">洪湖支撑服务中心</div>
            <div class="logo-sub">Web8011 运维平台</div>
          </div>
        </div>
        <el-menu :default-active="$route.path" router background-color="#1d2b3a" text-color="#aeb9c5"
                 active-text-color="#ffffff" style="border-right: none" @select="drawer = false">
          <template v-for="m in menus" :key="m.path">
            <el-menu-item :index="m.path">
              <el-icon><component :is="m.icon" /></el-icon>
              <span>{{ m.title }}</span>
            </el-menu-item>
          </template>
        </el-menu>
      </div>
    </el-drawer>

    <el-dialog v-model="pwdVisible" title="修改密码" width="400px">
      <el-form label-width="80px">
        <el-form-item label="原密码"><el-input v-model="pwdForm.old_password" type="password" show-password /></el-form-item>
        <el-form-item label="新密码"><el-input v-model="pwdForm.new_password" type="password" show-password /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" @click="changePwd">确定</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '../store'
import api from '../api'

const OVERDUE_HOURS = 2
const router = useRouter()
const store = useUserStore()
const user = computed(() => store.user)
const overdueCount = ref(0)

// 移动端适配：窄屏隐藏侧边栏，改用抽屉菜单
const isMobile = ref(window.innerWidth <= 768)
const drawer = ref(false)
const onResize = () => {
  isMobile.value = window.innerWidth <= 768
  if (!isMobile.value) drawer.value = false
}

const allMenus = [
  { path: '/dashboard', title: '首页看板', icon: 'Odometer' },
  { path: '/metrics/overview', title: '指标总览', icon: 'TrendCharts' },
  { path: '/metrics/completion-rate', title: '竣工率', icon: 'DataLine' },
  { path: '/metrics/extension', title: '延伸服务', icon: 'Coin' },
  { path: '/metrics/ai-visit', title: 'AI装维即访', icon: 'ChatLineSquare' },
  { path: '/metrics/shortcomings', title: '短板分析', icon: 'Warning' },
  { path: '/h5-monitor', title: 'H5短信盯控', icon: 'ChatDotRound' },
  { path: '/h5-rate', title: 'H5参与率通报', icon: 'DataAnalysis' },
  { path: '/alerts', title: '预警中心', icon: 'Bell' },
  { path: '/work-orders', title: '工单管理', icon: 'Tickets' },
  { path: '/tasks', title: '任务分派', icon: 'List', admin: true },
  { path: '/my-tasks', title: '我的任务', icon: 'Finished', worker: true },
  { path: '/notices', title: '通知管理', icon: 'Promotion', admin: true },
  { path: '/my-notices', title: '我的通知', icon: 'Message' },
  { path: '/sync', title: '数据同步', icon: 'Upload', admin: true },
  { path: '/users', title: '用户管理', icon: 'User', admin: true },
]

const menus = computed(() => {
  const role = store.role
  if (role === 'super_admin') return allMenus
  if (role === 'district_admin') return allMenus.filter(m => !m.worker)
  return allMenus.filter(m => !m.admin && (m.worker || ['my-tasks', 'my-notices'].includes(m.path) || !m.worker))
})

const pwdVisible = ref(false)
const pwdForm = ref({ old_password: '', new_password: '' })

function onCmd(cmd) {
  if (cmd === 'logout') {
    store.logout()
    router.push('/login')
  } else if (cmd === 'pwd') {
    pwdForm.value = { old_password: '', new_password: '' }
    pwdVisible.value = true
  }
}

async function changePwd() {
  if (!pwdForm.value.new_password || pwdForm.value.new_password.length < 8) {
    ElMessage.warning('新密码长度至少 8 位')
    return
  }
  await api.post('/auth/change-password', pwdForm.value)
  ElMessage.success('密码修改成功，请重新登录')
  store.logout()
  router.push('/login')
}

async function loadOverdue() {
  // 页面不可见时不发请求（后台标签页无需轮询，避免多开时成倍放大服务端压力）
  if (document.hidden) return
  try {
    const data = await api.get('/h5/alerts', { params: { date: 'today' } })
    overdueCount.value = data?.count || 0
  } catch (e) { /* ignore */ }
}

let overdueTimer = null
function onVisibilityChange() {
  // 切回前台立即刷新一次，保证数字不是旧的
  if (!document.hidden) loadOverdue()
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  document.addEventListener('visibilitychange', onVisibilityChange)
  store.refreshMe()
  loadOverdue()
  overdueTimer = setInterval(loadOverdue, 120000)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (overdueTimer) { clearInterval(overdueTimer); overdueTimer = null }
})
</script>

<style scoped>
.logo { display: flex; gap: 10px; align-items: center; padding: 18px 16px; color: #fff; }
.logo-title { font-weight: 700; font-size: 15px; letter-spacing: .5px; }
.logo-sub { font-size: 12px; color: #7f93a8; margin-top: 2px; }
.topbar { display: flex; align-items: center; justify-content: space-between; background: var(--hh-surface); border-bottom: 1px solid var(--hh-border); height: var(--hh-topbar-h); box-shadow: var(--shadow-1); }
.left { display: flex; align-items: center; min-width: 0; }
.hamburger { cursor: pointer; margin-right: 10px; padding: 6px; border-radius: var(--radius-sm); }
.hamburger:hover { background: var(--hh-surface-2); }
.crumb { font-weight: 600; font-size: 16px; color: var(--hh-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.right { display: flex; align-items: center; gap: 12px; min-width: 0; }
.uname { color: var(--hh-text-2); font-size: 14px; white-space: nowrap; }

/* 侧栏菜单：激活项左侧主色条 + 高亮底（深海军蓝侧栏的清晰层级） */
:deep(.el-menu) { border-right: none; }
:deep(.el-menu-item) { height: 46px; margin: 2px 8px; border-radius: var(--radius-sm); width: calc(100% - 16px); }
:deep(.el-menu-item:hover) { background: rgba(255,255,255,.06); }
:deep(.el-menu-item.is-active) { background: var(--hh-sidebar-2); position: relative; font-weight: 600; }
:deep(.el-menu-item.is-active)::before {
  content: ''; position: absolute; left: 0; top: 8px; bottom: 8px; width: 3px;
  border-radius: 0 3px 3px 0; background: var(--hh-primary);
}

.alert-banner { background: var(--hh-bad-weak); color: var(--hh-bad); padding: 8px 20px; display: flex; gap: 8px; align-items: center; cursor: pointer; font-size: 14px; border-bottom: 1px solid #fbd5d6; line-height: 1.5; word-break: break-word; }
.alert-banner b { color: var(--hh-bad); }

@media (max-width: 768px) {
  .topbar { padding: 0 12px; }
  .crumb { font-size: 14px; }
  .uname { display: none; }
  .alert-banner { font-size: 12px; padding: 6px 12px; flex-wrap: wrap; }
  .hamburger { margin-right: 6px; background: var(--hh-surface-2); }
  .main-area { padding: 12px !important; }
}
</style>
