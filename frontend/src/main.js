import { createApp } from 'vue'
import { createPinia } from 'pinia'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { provideGlobalConfig } from 'element-plus'
import ElMessage from 'element-plus/es/components/message/index.mjs'
import ElMessageBox from 'element-plus/es/components/message-box/index.mjs'
import {
  Platform, Menu, WarningFilled, Edit, Lock, User, Upload, UploadFilled,
  Odometer, TrendCharts, DataLine, Coin, ChatLineSquare, Warning, ChatDotRound,
  DataAnalysis, Bell, Tickets, List, Finished, Promotion, Message,
} from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import './style.css'
// 命令式组件（ElMessage/ElMessageBox）的样式必须显式引入：按需引入模式下
// unplugin-vue-components 只收集模板中出现过的组件样式，命令式服务经 JS 调用，
// 其 theme-chalk CSS 不会自动进入产物（缺失曾导致「确认入库」弹窗无样式、错位于左上角）
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-message-box.css'
import 'element-plus/theme-chalk/el-overlay.css'

// Element Plus 组件与 API 已改为按需引入（vite.config.js 中 unplugin-vue-components /
// unplugin-auto-import + ElementPlusResolver），不再全量注册。
// 命令式服务需显式安装（供拦截器与各视图 import { ElMessage } from 'element-plus' 使用）；
// 图标按需引入：命名导入可被 tree-shaking，未用到的 270+ 个图标不会进入产物。
const ICONS = {
  Platform, Menu, WarningFilled, Edit, Lock, User, Upload, UploadFilled,
  Odometer, TrendCharts, DataLine, Coin, ChatLineSquare, Warning, ChatDotRound,
  DataAnalysis, Bell, Tickets, List, Finished, Promotion, Message,
}

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElMessage)
app.use(ElMessageBox)
for (const [name, comp] of Object.entries(ICONS)) app.component(name, comp)
// 命令式组件（ElMessage/ElMessageBox）脱离组件树，中文语言包需显式全局注入
// （模板组件经 App.vue 的 <el-config-provider> 注入）
provideGlobalConfig({ locale: zhCn }, app, true)
app.mount('#app')
