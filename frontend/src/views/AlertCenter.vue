<template>
  <div>
    <!-- 页面级横幅：H5 超时未响应（实时计算，非模拟数据），点击跳到站内消息通知 -->
    <el-alert v-if="h5.count > 0" type="error" :closable="false" show-icon style="margin-bottom:12px;cursor:pointer"
              :title="`H5 超时未响应预警：当前有 ${h5.count} 单超时 ${h5.overdue_hours} 小时未响应，请点击处理`"
              @click="tab = 'inner'; load()" />

    <el-tabs v-model="tab" @tab-change="load">
      <!-- 待处理工单预警：139邮箱预警工单 卡片 + 明细 -->
      <el-tab-pane label="待处理工单预警" name="workorder">
        <el-card shadow="never" class="page-card">
          <template #header>
            <div class="card-header">
              <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <span class="title">省公司预警 · 139邮箱工单</span>
                <el-tag v-if="alertMeta.has_data" size="small" type="danger">数据时间：{{ alertMeta.meta.mail_time }}</el-tag>
                <span v-if="alertMeta.has_data" class="header-tip">
                  邮件：{{ alertMeta.meta.mail_subject }} · 导出 {{ alertMeta.meta.export_time }} · 拉取 {{ alertMeta.meta.fetched_at }}
                </span>
              </div>
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                <el-select v-model="alertDistrict" size="small" style="width:120px" placeholder="全部区县" clearable
                           @change="onAlertDistrict">
                  <el-option v-for="d in districtOptions" :key="d" :label="d === '全部' ? '全部区县' : d"
                             :value="d === '全部' ? '' : d" />
                </el-select>
                <el-button v-if="isAdmin" size="small" type="danger" plain :loading="alertSyncing" @click="syncAlert">
                  同步邮件
                </el-button>
              </div>
            </div>
          </template>

          <template v-if="alertMeta.has_data">
            <!-- 卡片汇总 -->
            <div class="stat-cards">
              <div class="stat-card blue clickable" :class="{ active: alertType === 'install' }" @click="pickAlert('install')">
                <div class="label">待装工单</div>
                <div class="value">{{ alertMeta.counts?.install ?? alertMeta.meta.install_total }}<span class="unit">单</span></div>
                <div class="sub">{{ alertInstallTop }}</div>
              </div>
              <div class="stat-card purple clickable" :class="{ active: alertType === 'complaint' }" @click="pickAlert('complaint')">
                <div class="label">投诉待处理</div>
                <div class="value">{{ alertMeta.counts?.complaint ?? alertMeta.meta.complaint_total }}<span class="unit">单</span></div>
                <div class="sub">{{ alertComplaintTop }}</div>
              </div>
              <div class="stat-card red clickable" :class="{ active: alertType === 'book' }" @click="pickAlert('book')">
                <div class="label">预约异常</div>
                <div class="value">{{ alertMeta.counts?.book ?? alertMeta.meta.book_abnormal }}<span class="unit">单</span></div>
                <div class="sub">超时/临近/冲突</div>
              </div>
              <div class="stat-card orange clickable" :class="{ active: alertType === 'monitor' && monitorLevel === '黄' }"
                   @click="pickAlert('monitor', '黄')">
                <div class="label">黄色告警人员</div>
                <div class="value">{{ alertMeta.counts?.warn_yellow ?? alertMeta.meta.warn_yellow }}<span class="unit">人</span></div>
                <div class="sub">单量超负荷预警</div>
              </div>
              <div class="stat-card red clickable" :class="{ active: alertType === 'monitor' && monitorLevel === '红' }"
                   @click="pickAlert('monitor', '红')">
                <div class="label">红色告警人员</div>
                <div class="value">{{ alertMeta.counts?.warn_red ?? alertMeta.meta.warn_red }}<span class="unit">人</span></div>
                <div class="sub">需立即调单</div>
              </div>
              <div class="stat-card green">
                <div class="label">全球通-白金卡</div>
                <div class="value">{{ alertMeta.global_cnt?.platinum ?? 0 }}<span class="unit">单</span></div>
                <div class="sub">当月通报待施工</div>
              </div>
              <div class="stat-card blue">
                <div class="label">全球通-钻石</div>
                <div class="value">{{ alertMeta.global_cnt?.diamond ?? 0 }}<span class="unit">单</span></div>
                <div class="sub">当月通报待施工</div>
              </div>
            </div>

            <!-- 明细 -->
            <div class="toolbar">
              <el-radio-group v-model="alertType" size="small" @change="loadAlertOrders">
                <el-radio-button value="install">待装明细</el-radio-button>
                <el-radio-button value="complaint">投诉待处理</el-radio-button>
                <el-radio-button value="book">预约异常</el-radio-button>
                <el-radio-button value="monitor">人员预警</el-radio-button>
              </el-radio-group>
              <span class="header-tip" v-if="monitorLevel">仅看{{ monitorLevel }}色告警</span>
            </div>

            <el-table v-if="alertType === 'install'" :data="alertRows" size="small" max-height="560">
              <el-table-column prop="district" label="区县" width="85" sortable />
              <el-table-column prop="order_no" label="定单编码" min-width="150" show-overflow-tooltip sortable />
              <el-table-column prop="status" label="工单状态" width="105" sortable>
                <template #default="{ row }"><el-tag size="small" :type="alertStTag(row.status)">{{ row.status || '-' }}</el-tag></template>
              </el-table-column>
              <el-table-column prop="order_type" label="工单类型" width="90" sortable />
              <el-table-column prop="accept_time" label="受理时间" width="150" sortable />
              <el-table-column prop="appoint_time" label="预约时间" width="150" sortable />
              <el-table-column prop="visit_time" label="上门时间" width="150" sortable />
              <el-table-column prop="handler" label="处理人" min-width="80" sortable />
              <el-table-column prop="grid" label="网格" min-width="95" show-overflow-tooltip sortable />
              <el-table-column prop="community" label="小区" min-width="110" show-overflow-tooltip sortable />
              <el-table-column prop="is_fttr" label="FTTR" width="70" sortable />
              <el-table-column prop="is_high_quality" label="高品质" width="70" sortable />
            </el-table>
            <el-table v-else-if="alertType === 'complaint'" :data="alertRows" size="small" max-height="560">
              <el-table-column prop="district" label="区县" width="85" sortable />
              <el-table-column prop="order_no" label="定单编码" min-width="150" show-overflow-tooltip sortable />
              <el-table-column prop="status" label="工单状态" width="105" sortable>
                <template #default="{ row }"><el-tag size="small" :type="alertStTag(row.status)">{{ row.status || '-' }}</el-tag></template>
              </el-table-column>
              <el-table-column prop="order_type" label="工单类型" width="90" sortable />
              <el-table-column prop="accept_time" label="受理时间" width="150" sortable />
              <el-table-column prop="appoint_time" label="预约时间" width="150" sortable />
              <el-table-column prop="handler" label="处理人" min-width="80" sortable />
              <el-table-column prop="grid" label="网格" min-width="95" show-overflow-tooltip sortable />
              <el-table-column prop="community" label="小区" min-width="110" show-overflow-tooltip sortable />
            </el-table>
            <el-table v-else-if="alertType === 'book'" :data="alertRows" size="small" max-height="560">
              <el-table-column prop="district" label="区县" width="85" sortable />
              <el-table-column prop="book_status" label="预约状态" width="110" sortable>
                <template #default="{ row }">
                  <el-tag size="small" :type="(row.book_status || '').includes('超时') || (row.book_status || '').includes('冲突') ? 'danger' : 'warning'">
                    {{ row.book_status || '-' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="order_no" label="定单编码" min-width="150" show-overflow-tooltip sortable />
              <el-table-column prop="status" label="定单状态" width="105" sortable />
              <el-table-column prop="appoint_time" label="预约时间" width="150" sortable />
              <el-table-column prop="visit_time" label="上门时间" width="150" sortable />
              <el-table-column prop="handler" label="处理人" min-width="80" sortable />
              <el-table-column prop="supervise_info" label="督办信息" width="100" sortable />
              <el-table-column prop="supervise_person" label="督办人员" min-width="85" sortable />
              <el-table-column prop="grid" label="网格" min-width="95" show-overflow-tooltip sortable />
            </el-table>
            <el-table v-else :data="monitorRows" size="small" max-height="560">
              <el-table-column prop="warn_level" label="预警级别" width="100" sortable>
                <template #default="{ row }">
                  <el-tag size="small" :type="(row.warn_level || '').includes('红') ? 'danger' : (row.warn_level || '').includes('黄') ? 'warning' : 'info'">
                    {{ row.warn_level || '-' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="handler" label="人员" min-width="85" sortable />
              <el-table-column prop="district" label="区县" width="100" sortable />
              <el-table-column prop="grid" label="网格" min-width="120" show-overflow-tooltip sortable />
              <el-table-column prop="order_count" label="当日工单量" width="100" align="right" sortable />
              <el-table-column prop="warn_time" label="预警时间" width="150" sortable />
            </el-table>
          </template>
          <el-empty v-else description="暂无预警数据，管理员点击右上角「同步邮件」拉取省公司最新邮件" />
        </el-card>
      </el-tab-pane>

      <!-- 站内消息通知：H5 超时未收回执（实时计算） -->
      <el-tab-pane label="站内消息通知" name="inner">
        <el-card shadow="never" class="page-card">
          <template #header>
            <div class="card-header">
              <span class="title">H5 超时未收回执</span>
              <span class="header-tip">实时计算：回单/受理超过 {{ h5.overdue_hours }} 小时未收回执</span>
            </div>
          </template>
          <el-table :data="h5.items" size="small" max-height="560">
            <el-table-column prop="order_no" label="工单编码" min-width="160" show-overflow-tooltip />
            <el-table-column prop="kind" label="类型" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="row.kind === 'install' ? 'primary' : 'warning'">
                  {{ row.kind === 'install' ? '装机' : '投诉' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="district" label="区县" width="90" />
            <el-table-column prop="grid" label="网格" min-width="100" show-overflow-tooltip />
            <el-table-column prop="handler" label="处理人" min-width="90" show-overflow-tooltip />
            <el-table-column prop="service_number" label="服务号码" width="120" />
            <el-table-column prop="back_time" label="回单时间" width="160" />
            <el-table-column label="超时时长" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="row.age_h >= 24 ? 'danger' : 'warning'">{{ row.age_h }} 小时</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!h5.items.length" description="当前无超时未收回执工单" />
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../store'

const store = useUserStore()
const isAdmin = computed(() => store.isAdmin)
const districtOptions = ['洪湖市', '监利市', '公安县', '荆州区', '江陵县', '松滋市', '沙市区', '石首市', '荆州开发区', '全部']

const tab = ref('workorder')
const alertMeta = ref({ has_data: false })
const alertType = ref('install')
const alertRows = ref([])
const alertSyncing = ref(false)
const alertDistrict = ref('')
const monitorLevel = ref('')
const h5 = ref({ overdue_hours: 2, count: 0, items: [] })

const alertStTag = s => (s || '').includes('施工') ? 'warning'
  : (s || '').includes('回访') || (s || '').includes('接收') ? 'success' : 'info'
const alertInstallTop = computed(() => {
  const st = alertMeta.value.install_status || []
  return st.length ? `最多：${st[0].status} ${st[0].count}` : ''
})
const alertComplaintTop = computed(() => {
  const st = alertMeta.value.complaint_status || []
  return st.length ? `最多：${st[0].status} ${st[0].count}` : ''
})
const monitorRows = computed(() =>
  monitorLevel.value ? alertRows.value.filter(r => (r.warn_level || '').includes(monitorLevel.value))
    : alertRows.value)

async function load() {
  if (tab.value === 'workorder') {
    await loadAlerts()
  } else {
    await loadH5()
  }
}

async function loadAlerts() {
  try {
    const s = await api.get('/alerts/summary', { params: { district: alertDistrict.value } })
    alertMeta.value = s.has_data ? s : { has_data: false }
    if (s.has_data) loadAlertOrders()
  } catch (e) { alertMeta.value = { has_data: false } }
}

async function loadAlertOrders() {
  try {
    const r = await api.get('/alerts/orders', {
      params: { type: alertType.value, district: alertDistrict.value, limit: 2000 },
    })
    alertRows.value = r.rows || []
  } catch (e) { alertRows.value = [] }
}

async function loadH5() {
  try {
    const r = await api.get('/h5/alerts', { params: { date: 'today' } })
    h5.value = { overdue_hours: r.overdue_hours ?? 2, count: r.count ?? 0, items: r.items || [] }
  } catch (e) { h5.value = { overdue_hours: 2, count: 0, items: [] } }
}

function pickAlert(type, level = '') {
  alertType.value = type
  monitorLevel.value = type === 'monitor' ? level : ''
  loadAlertOrders()
}

function onAlertDistrict() {
  loadAlerts()
}

async function syncAlert() {
  alertSyncing.value = true
  try {
    const r = await api.post('/alerts/fetch', null, { timeout: 120000 })
    if (r.ok) {
      ElMessage.success(`已同步预警快照：${r.mail_time}（待装${r.install} · 投诉${r.complaint} · 预约异常${r.book}）`)
      await loadAlerts()
    } else ElMessage.error(r.error || '同步失败')
  } catch (e) { ElMessage.error('同步失败：' + (e?.response?.data?.detail || e.message)) }
  finally { alertSyncing.value = false }
}

// 页面加载即拉 H5 实时超时（横幅需要），与当前 tab 无关
onMounted(() => { loadH5(); load() })
</script>
