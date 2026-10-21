import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', component: () => import('./views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('./components/Layout.vue'),
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', component: () => import('./views/Dashboard.vue'), meta: { title: '首页看板' } },
      { path: 'metrics/overview', component: () => import('./views/MetricsOverview.vue'), meta: { title: '指标总览' } },
      { path: 'metrics/completion-rate', component: () => import('./views/CompletionRate.vue'), meta: { title: '竣工率' } },
      { path: 'metrics/extension', component: () => import('./views/Extension.vue'), meta: { title: '延伸服务' } },
      { path: 'metrics/ai-visit', component: () => import('./views/AiVisit.vue'), meta: { title: 'AI装维即访' } },
      { path: 'metrics/shortcomings', component: () => import('./views/Shortcomings.vue'), meta: { title: '短板分析' } },
      { path: 'h5-monitor', component: () => import('./views/H5Monitor.vue'), meta: { title: 'H5短信盯控' } },
      { path: 'h5-rate', component: () => import('./views/H5Rate.vue'), meta: { title: 'H5参与率通报' } },
      { path: 'alerts', component: () => import('./views/AlertCenter.vue'), meta: { title: '预警中心' } },
      { path: 'work-orders', component: () => import('./views/WorkOrders.vue'), meta: { title: '工单管理' } },
      { path: 'tasks', component: () => import('./views/Tasks.vue'), meta: { title: '任务分派' } },
      { path: 'my-tasks', component: () => import('./views/MyTasks.vue'), meta: { title: '我的任务' } },
      { path: 'notices', component: () => import('./views/Notices.vue'), meta: { title: '通知管理' } },
      { path: 'my-notices', component: () => import('./views/MyNotices.vue'), meta: { title: '我的通知' } },
      { path: 'sync', component: () => import('./views/DataSync.vue'), meta: { title: '数据同步', admin: true } },
      { path: 'users', component: () => import('./views/Users.vue'), meta: { title: '用户管理', admin: true } },
    ]
  },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(to => {
  const token = localStorage.getItem('honghu_token')
  if (!to.meta.public && !token) return '/login'
  if (to.path === '/login' && token) return '/dashboard'
})

export default router
