# -*- coding: utf-8 -*-
"""服务器端精确插入补丁：每处替换都校验唯一匹配，且幂等（已打过则跳过）。

只做字符串级插入，不重写无关内容，避免覆盖其他人正在编辑的改动。
"""
from pathlib import Path

ROOT = Path('/vol2/Trae')

# (文件, 原文片段, 替换后片段)
PATCHES = [
    # 1) 后端：注册新路由模块
    (ROOT / 'backend/app/main.py',
     "from app.routers import (auth, dashboard, metrics, h5, work_orders, tasks, notices, sync, users,\n"
     "                          alerts, honghu, mp, qc, territory)",
     "from app.routers import (auth, dashboard, metrics, h5, work_orders, tasks, notices, sync, users,\n"
     "                          alerts, honghu, mp, qc, territory, broad_user_list)"),
    # 2) 后端：挂载路由
    (ROOT / 'backend/app/main.py',
     "for r in [auth.router, dashboard.router, metrics.router, h5.router, work_orders.router,\n"
     "          tasks.router, notices.router, sync.router, users.router, alerts.router, honghu.router,\n"
     "          mp.router, qc.router, territory.router]:",
     "for r in [auth.router, dashboard.router, metrics.router, h5.router, work_orders.router,\n"
     "          tasks.router, notices.router, sync.router, users.router, alerts.router, honghu.router,\n"
     "          mp.router, qc.router, territory.router, broad_user_list.router]:"),
    # 3) 前端：菜单项紧跟「H5参与率通报」，同组同级，不新建分组
    (ROOT / 'frontend/src/components/Layout.vue',
     "  { path: '/h5-rate', title: 'H5参与率通报', icon: 'DataAnalysis' },\n"
     "  { path: '/alerts', title: '预警中心', icon: 'Bell' },",
     "  { path: '/h5-rate', title: 'H5参与率通报', icon: 'DataAnalysis' },\n"
     "  { path: '/broad-user-list', title: '广义用户清单', icon: 'Document' },\n"
     "  { path: '/alerts', title: '预警中心', icon: 'Bell' },"),
    # 4) 前端：路由紧跟 h5-rate
    (ROOT / 'frontend/src/router.js',
     "      { path: 'h5-rate', component: () => import('./views/H5Rate.vue'), meta: { title: 'H5参与率通报' } },\n"
     "      { path: 'alerts', component: () => import('./views/AlertCenter.vue'), meta: { title: '预警中心' } },",
     "      { path: 'h5-rate', component: () => import('./views/H5Rate.vue'), meta: { title: 'H5参与率通报' } },\n"
     "      { path: 'broad-user-list', component: () => import('./views/BroadUserList.vue'), meta: { title: '广义用户清单' } },\n"
     "      { path: 'alerts', component: () => import('./views/AlertCenter.vue'), meta: { title: '预警中心' } },"),
    # 5) 前端：注册 Document 图标（图标为显式白名单，未注册会渲染空白）
    (ROOT / 'frontend/src/main.js',
     "  MapLocation, Refresh,\n} from '@element-plus/icons-vue'",
     "  MapLocation, Refresh, Document,\n} from '@element-plus/icons-vue'"),
    (ROOT / 'frontend/src/main.js',
     "  MapLocation, Refresh,\n}\n\nconst app = createApp(App)",
     "  MapLocation, Refresh, Document,\n}\n\nconst app = createApp(App)"),
]


def patch(path: Path, old: str, new: str):
    with open(path, 'r', encoding='utf-8', newline='') as f:
        raw = f.read()
    nl = '\r\n' if '\r\n' in raw else '\n'
    old_n, new_n = old.replace('\n', nl), new.replace('\n', nl)
    if new_n in raw:
        print('SKIP  已打过补丁:', path.name, '|', new_n.splitlines()[-1][:60])
        return
    n = raw.count(old_n)
    if n != 1:
        raise SystemExit('ABORT %s: 期望 1 处匹配，实际 %d 处，未做任何修改' % (path, n))
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(raw.replace(old_n, new_n, 1))
    print('PATCH ', path, '| +', new_n.count(nl) - old_n.count(nl), '行')


for p, o, n in PATCHES:
    patch(p, o, n)
print('全部补丁完成')
