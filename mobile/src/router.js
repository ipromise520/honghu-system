import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from './api'

const routes = [
  { path: '/', name: 'home', component: () => import('./pages/Home.vue') },
  { path: '/login', name: 'login', component: () => import('./pages/Login.vue'), meta: { open: true } },
  { path: '/detail', name: 'detail', component: () => import('./pages/Detail.vue') },
  { path: '/notice/:id', name: 'notice', component: () => import('./pages/Notice.vue') },
  { path: '/knowledge', name: 'knowledge', component: () => import('./pages/Knowledge.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory('/m/'),
  routes,
})

router.beforeEach((to) => {
  if (!to.meta.open && !getToken()) return { name: 'login' }
  return true
})

export default router
