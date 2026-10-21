<template>
  <div class="h5-page">
    <!-- 顶部工具栏 -->
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <div class="toolbar-left">
          <span class="page-title">📱 H5 短信盯控</span>
          <el-tag type="warning" effect="light" size="small">投诉 HB-058 · 装机个人宽带 · 回单后 2h 未收</el-tag>
        </div>
        <div class="toolbar-right">
          <el-select v-model="district" placeholder="全部区县" size="small" clearable style="width:130px" @change="loadAll">
            <el-option v-for="d in districtOptions" :key="d" :label="d || '全部区县'" :value="d" />
          </el-select>
          <el-radio-group v-model="dateMode" size="small" @change="onDateModeChange">
            <el-radio-button value="today">今天</el-radio-button>
            <el-radio-button value="month">当月</el-radio-button>
            <el-radio-button value="day">某月某天</el-radio-button>
          </el-radio-group>
          <el-date-picker
            v-if="dateMode === 'day'"
            v-model="dateValue"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            size="small"
            style="width:140px"
            @change="loadAll" />
          <el-date-picker
            v-else-if="dateMode === 'month'"
            v-model="dateValue"
            type="month"
            value-format="YYYY-MM"
            placeholder="选择月份"
            size="small"
            style="width:130px"
            @change="loadAll" />
          <el-button size="small" type="primary" :loading="loading" @click="loadAll">🔄 刷新</el-button>
          <el-button size="small" @click="importOpen = !importOpen">📥 数据导入</el-button>
        </div>
      </div>
    </el-card>

    <!-- 数据导入（折叠区） -->
    <el-card v-if="importOpen" shadow="never" class="import-card">
      <div class="import-grid">
        <div class="import-item">
          <div class="import-name">① 投诉单综合（H5投诉）</div>
          <el-upload :auto-upload="false" :limit="1" :accept="ACCEPT_TYPES"
            :on-change="f => onFileChange('complaint', f)" :file-list="fileList.complaint"
            @remove="() => onFileRemove('complaint')">
            <el-button size="small">📎 选择文件</el-button>
          </el-upload>
          <el-checkbox v-model="replaceComplaint" size="small">替换全部历史批次</el-checkbox>
          <el-button size="small" type="primary" :disabled="!files.complaint"
            :loading="loadingUp.complaint" @click="uploadFile('complaint')">上传并入库</el-button>
          <span v-if="result.complaint" class="up-res">✅ 入库 {{ result.complaint.inserted }} 条</span>
        </div>
        <div class="import-item">
          <div class="import-name">② 装机待施工综合工单列表</div>
          <el-upload :auto-upload="false" :limit="1" :accept="ACCEPT_TYPES"
            :on-change="f => onFileChange('install', f)" :file-list="fileList.install"
            @remove="() => onFileRemove('install')">
            <el-button size="small">📎 选择文件</el-button>
          </el-upload>
          <el-button size="small" type="primary" :disabled="!files.install"
            :loading="loadingUp.install" @click="uploadFile('install')">上传并入库</el-button>
          <span v-if="result.install" class="up-res">✅ 入库 {{ result.install.inserted }} 条</span>
        </div>
        <div class="import-item">
          <div class="import-name">③ 宽表查询-投诉</div>
          <el-upload :auto-upload="false" :limit="1" :accept="ACCEPT_TYPES"
            :on-change="f => onFileChange('wide', f)" :file-list="fileList.wide"
            @remove="() => onFileRemove('wide')">
            <el-button size="small">📎 选择文件</el-button>
          </el-upload>
          <el-button size="small" type="primary" :disabled="!files.wide"
            :loading="loadingUp.wide" @click="uploadWide">上传并匹配</el-button>
          <span v-if="result.wide" class="up-res">✅ 匹配 {{ result.wide.rows }} 条 / 回填 {{ result.wide.updated_complaint }} 条</span>
        </div>
        <div class="import-item">
          <div class="import-name">④ 宽表查询-装机</div>
          <el-upload :auto-upload="false" :limit="1" :accept="ACCEPT_TYPES"
            :on-change="f => onFileChange('wideInstall', f)" :file-list="fileList.wideInstall"
            @remove="() => onFileRemove('wideInstall')">
            <el-button size="small">📎 选择文件</el-button>
          </el-upload>
          <el-button size="small" type="primary" :disabled="!files.wideInstall"
            :loading="loadingUp.wideInstall" @click="uploadWideInstall">上传并匹配</el-button>
          <span v-if="result.wideInstall" class="up-res">✅ 匹配 {{ result.wideInstall.rows }} 条 / 回填 {{ result.wideInstall.matched }} 条</span>
        </div>
      </div>
      <div class="import-hint">💡 家宽投诉/装机表生成应发清单；宽表-投诉补齐投诉工单的小区名称、联系人、故障号码；宽表-装机按工单编码匹配回填最新状态、所属网格与缺失字段</div>
    </el-card>

    <!-- 2 张统计卡片 -->
    <el-row :gutter="12" class="cards-row">
      <el-col :xs="24" :sm="12">
        <el-card shadow="never" class="stat-card" body-style="padding:14px">
          <div class="card-head"><span class="card-title">🏠 投诉 H5（家庭市场 HB-058）</span></div>
          <div class="card-grid">
            <div class="cell"><div class="num">{{ stats.complaint.total }}</div><div class="lab">应发</div></div>
            <div class="cell ok"><div class="num">{{ stats.complaint.received }}</div><div class="lab">已收</div></div>
            <div class="cell warn"><div class="num">{{ stats.complaint.pending }}</div><div class="lab">未收</div></div>
            <div class="cell"><div class="num">{{ stats.complaint.ratio }}%</div><div class="lab">收取率</div></div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12">
        <el-card shadow="never" class="stat-card" body-style="padding:14px">
          <div class="card-head"><span class="card-title">📶 装机 H5（个人宽带）</span></div>
          <div class="card-grid">
            <div class="cell"><div class="num">{{ stats.install.total }}</div><div class="lab">应发</div></div>
            <div class="cell ok"><div class="num">{{ stats.install.received }}</div><div class="lab">已收</div></div>
            <div class="cell warn"><div class="num">{{ stats.install.pending }}</div><div class="lab">未收</div></div>
            <div class="cell"><div class="num">{{ stats.install.ratio }}%</div><div class="lab">收取率</div></div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 超时预警 -->
    <el-card v-if="alerts.count > 0" shadow="never" class="alert-card">
      <div class="alert-head" @click="alertOpen = !alertOpen">
        <span class="alert-title">⚠️ 超时未收预警（回单后 2h）· {{ alerts.count }} 单</span>
        <el-tag size="small" type="danger">{{ alerts.overdue_hours }}h 阈值</el-tag>
      </div>
      <div v-if="alertOpen" class="alert-body">
        <el-table :data="alerts.items.slice(0, 30)" border size="small" max-height="240">
          <el-table-column prop="kind" label="类型" min-width="70">
            <template #default="{ row }"><el-tag size="small" :type="row.kind === 'complaint' ? 'warning' : 'primary'">{{ row.kind === 'complaint' ? '投诉' : '装机' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="工单号" min-width="180">
            <template #default="{ row }">{{ row.kind === 'install' ? (row.service_number || row.order_no) : row.order_no }}</template>
          </el-table-column>
          <el-table-column prop="handler" label="施工人" min-width="110" />
          <el-table-column prop="back_time" label="回单时间" min-width="150" />
          <el-table-column prop="age_h" label="已超时" min-width="80">
            <template #default="{ row }"><span style="color:#f56c6c;font-weight:600">{{ row.age_h }}h</span></template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>

    <!-- 待收清单 -->
    <el-card shadow="never" class="list-card">
      <div class="list-head">
        <span class="card-title">
          📋 待收清单（未收 H5）
          <span class="list-stat" style="margin-left:10px;font-weight:normal;font-size:13px;color:#606266">
            · 待参评
            <el-tag size="small" type="primary" effect="plain" style="margin-left:4px">装机 {{ stats.install.pending }}</el-tag>
            <el-tag size="small" type="warning" effect="plain" style="margin-left:4px">投诉 {{ stats.complaint.pending }}</el-tag>
            <span style="margin-left:6px;color:#909399">件</span>
          </span>
        </span>
        <el-checkbox v-model="showUnfinished" size="small" @change="loadPending">
          显示未完工工单（<span class="status-tag">#待预约、待施工、待地址确认、待调度</span>）
        </el-checkbox>
        <el-radio-group v-model="kind" size="small" @change="loadPending">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="complaint">投诉</el-radio-button>
          <el-radio-button value="install">装机</el-radio-button>
        </el-radio-group>
        <span class="time-range">
          <el-date-picker v-model="timeStart" type="datetime" placeholder="开始时间" size="small"
            value-format="YYYY-MM-DD HH:mm:ss" format="MM-DD HH:mm" style="width:160px"
            :clearable="true" @change="onTimeRangeChange" />
          <span class="time-sep">至</span>
          <el-date-picker v-model="timeEnd" type="datetime" placeholder="结束时间" size="small"
            value-format="YYYY-MM-DD HH:mm:ss" format="MM-DD HH:mm" style="width:160px"
            :clearable="true" @change="onTimeRangeChange" />
          <el-button v-if="timeStart || timeEnd" size="small" plain @click="clearTimeRange">清空</el-button>
        </span>
        <span class="list-tip">展开分组 → 批量标记「已参评」</span>
        <span class="list-tools">
          <el-button size="small" plain @click="expandAll">{{ allExpanded ? '⤵️ 全部折叠' : '⤴️ 全部展开' }}</el-button>
        </span>
      </div>
      <el-table :data="groups" border size="small" style="width:100%" row-key="key"
        :expand-row-keys="expanded" @expand-change="onExpandVal">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="detail-box">
              <div class="detail-head">
                <span>{{ row.handler }}-H5待回收 明细（{{ row.count }}件）</span>
                <el-button size="small" type="success" plain @click="markAll(row)">✅ 全组标记已参评</el-button>
              </div>
              <el-table :data="row.items" border size="small">
                <el-table-column label="工单号" min-width="169">
                  <template #default="{ row: it }">{{ it.kind === 'install' ? (it.service_number || it.order_no) : it.order_no }}</template>
                </el-table-column>
                <el-table-column prop="community_name" label="小区名称" min-width="180" show-overflow-tooltip />
                <el-table-column prop="back_time" label="回单时间" min-width="157" />
                <el-table-column prop="accept_time" label="受理时间" min-width="157" />
                <el-table-column prop="contact_person" label="联系人" min-width="117" />
                <el-table-column prop="fault_number" label="故障号码" min-width="126" />
                <el-table-column prop="status" label="状态" min-width="98" />
                <el-table-column label="区县" min-width="104">
                  <template #default="{ row: it }">
                    <span v-if="it.district">{{ it.district }}</span>
                    <el-tag v-else size="small" type="info" effect="plain">归属待确认</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="操作" min-width="110">
                  <template #default="{ row: it }">
                    <el-button size="small" type="primary" plain @click="markOne(it)">标记已参评</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="类型" min-width="90">
          <template #default="{ row }">
            <span v-if="row.kinds">
              <el-tag v-if="row.kinds.install" size="small" type="primary" style="margin-right:2px">装机{{ row.kinds.install }}</el-tag>
              <el-tag v-if="row.kinds.complaint" size="small" type="warning">投诉{{ row.kinds.complaint }}</el-tag>
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="handler" label="施工人" min-width="130" />
        <el-table-column label="区县" min-width="104">
          <template #default="{ row }">
            <span v-if="row.district">{{ row.district }}</span>
            <el-tag v-else size="small" type="info" effect="plain">归属待确认</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="grid" label="所属网格" min-width="140">
          <template #default="{ row }">
            <span v-if="row.grid">{{ row.grid }}</span>
            <span v-else style="color:#c0c4cc">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="count" label="待收数" min-width="70" align="center" />
        <el-table-column prop="max_age_h" label="最长未收" min-width="90">
          <template #default="{ row }"><span :style="{ color: row.max_age_h >= 2 ? '#f56c6c' : '#909399' }">{{ row.max_age_h }}h</span></template>
        </el-table-column>
        <el-table-column label="操作" min-width="150">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="markAll(row)">✅ 全组已参评</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="groups.length === 0" class="empty-tip">🎉 暂无待收工单</div>
    </el-card>

    <!-- 已收清单 -->
    <el-card shadow="never" class="list-card">
      <div class="list-head">
        <span class="card-title">✅ 已收清单（已参评 H5）</span>
        <el-radio-group v-model="receiptKind" size="small" @change="loadReceipts">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="complaint">投诉</el-radio-button>
          <el-radio-button value="install">装机</el-radio-button>
        </el-radio-group>
      </div>
      <el-table :data="receipts" border size="small" style="width:100%" max-height="300">
        <el-table-column label="类型" min-width="70">
          <template #default="{ row }"><el-tag size="small" :type="row.kind === 'complaint' ? 'warning' : 'primary'">{{ row.kind === 'complaint' ? '投诉' : '装机' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="工单号" min-width="190">
          <template #default="{ row }">{{ row.kind === 'install' ? (row.service_number || row.order_no) : row.order_no }}</template>
        </el-table-column>
        <el-table-column prop="handler" label="施工人" min-width="130" />
        <el-table-column prop="community_name" label="小区名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="grid" label="所属网格" min-width="140" show-overflow-tooltip />
        <el-table-column label="状态" min-width="80">
          <template #default><el-tag size="small" type="success">已参评</el-tag></template>
        </el-table-column>
        <el-table-column prop="updated_at" label="标记时间" min-width="150" />
        <el-table-column label="操作" min-width="90">
          <template #default="{ row }">
            <el-button size="small" type="danger" plain @click="cancelReceipt(row)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="receipts.length === 0" class="empty-tip">暂无已收工单</div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const ACCEPT_TYPES = '.xls,.xlsx,.csv'

const district = ref('')
const dateMode = ref('today')
const dateValue = ref('')
const districtOptions = ['洪湖市', '监利市', '石首市', '松滋市', '公安县', '江陵县', '荆州区', '沙市区', '荆州开发区']
const kind = ref('all')
const receiptKind = ref('all')
// 勾选 = 显示未完工工单（待预约/待施工/待地址确认/待调度），默认勾选
const showUnfinished = ref(true)
const loading = ref(false)
const importOpen = ref(false)
const alertOpen = ref(false)
const expanded = ref([])
const allExpanded = ref(false)
// 起止时间过滤（与「日期模式」互斥：任一非空即走自定义窗口）
const timeStart = ref('')
const timeEnd = ref('')

const stats = reactive({ install: { total: 0, received: 0, pending: 0, ratio: 0 }, complaint: { total: 0, received: 0, pending: 0, ratio: 0 } })
const alerts = reactive({ count: 0, overdue_hours: 2, items: [] })
const groups = ref([])
const receipts = ref([])

// ---- 数据导入 ----
const files = reactive({ complaint: null, install: null, wide: null, wideInstall: null })
const fileList = reactive({ complaint: [], install: [], wide: [], wideInstall: [] })
const loadingUp = reactive({ complaint: false, install: false, wide: false, wideInstall: false })
const result = reactive({ complaint: null, install: null, wide: null, wideInstall: null })
const replaceComplaint = ref(false)

function errText(e) {
  const d = e?.response?.data
  const detail = d?.error || d?.detail || (typeof d === 'string' && d) || ''
  return String(detail || e?.message || e)
}
function onFileChange(key, file) { files[key] = file?.raw || file }
function onFileRemove(key) { files[key] = null; fileList[key] = [] }

async function uploadFile(k) {
  if (!files[k]) { ElMessage.warning('请先选择文件'); return }
  if (k === 'complaint' && replaceComplaint.value) {
    try { await ElMessageBox.confirm('将清空全部历史投诉批次、仅保留本次导入，确定？', '替换历史批次', { type: 'warning' }) } catch { return }
  }
  loadingUp[k] = true
  try {
    const fd = new FormData()
    fd.append('file', files[k])
    if (k === 'complaint' && replaceComplaint.value) fd.append('replace_all', 'true')
    const res = await api.post(`/h5/upload/${k}`, fd, { headers: { 'Content-Type': undefined }, timeout: 180000 })
    if (res?.error) ElMessage.error(res.error)
    else { result[k] = res; ElMessage.success(`入库完成：${res.inserted} 条`); await loadAll() }
  } catch (e) { ElMessage.error('上传失败：' + errText(e)) }
  finally { loadingUp[k] = false }
}

async function uploadWide() {
  if (!files.wide) { ElMessage.warning('请先选择文件'); return }
  loadingUp.wide = true
  try {
    const fd = new FormData()
    fd.append('file', files.wide)
    const res = await api.post('/h5/upload/wide', fd, { headers: { 'Content-Type': undefined }, timeout: 180000 })
    if (res?.error) ElMessage.error(res.error)
    else { result.wide = res; ElMessage.success(`宽表匹配完成：${res.rows} 条 / 回填 ${res.updated_complaint} 条`); await loadAll() }
  } catch (e) { ElMessage.error('上传失败：' + errText(e)) }
  finally { loadingUp.wide = false }
}

async function uploadWideInstall() {
  if (!files.wideInstall) { ElMessage.warning('请先选择文件'); return }
  loadingUp.wideInstall = true
  try {
    const fd = new FormData()
    fd.append('file', files.wideInstall)
    const res = await api.post('/h5/upload/wide_install', fd, { headers: { 'Content-Type': undefined }, timeout: 180000 })
    if (res?.error) ElMessage.error(res.error)
    else { result.wideInstall = res; ElMessage.success(`宽表-装机匹配完成：${res.rows} 条 / 回填 ${res.matched} 条`); await loadAll() }
  } catch (e) { ElMessage.error('上传失败：' + errText(e)) }
  finally { loadingUp.wideInstall = false }
}

// ---- 盯控数据 ----
function getDateParam() {
  if (dateMode.value === 'today') return 'today'
  if (dateMode.value === 'month') return dateValue.value || new Date().toISOString().slice(0, 7)
  return dateValue.value || new Date().toISOString().slice(0, 10)
}
function onDateModeChange() {
  if (dateMode.value === 'today') {
    dateValue.value = ''
  } else if (!dateValue.value) {
    const now = new Date()
    dateValue.value = dateMode.value === 'month' ? now.toISOString().slice(0, 7) : now.toISOString().slice(0, 10)
  }
  // 切换日期模式时清空自定义起止时间，避免自定义窗口覆盖日期模式口径
  timeStart.value = ''
  timeEnd.value = ''
  loadAll()
}
function getTimeParams() {
  const p = {}
  if (timeStart.value || timeEnd.value) { p.start_dt = timeStart.value || ''; p.end_dt = timeEnd.value || '' }
  return p
}

async function loadAll() {
  loading.value = true
  try {
    const dParam = getDateParam()
    const tp = getTimeParams()
    const [d, a] = await Promise.all([
      api.get('/h5/dashboard', { params: { date: dParam, district: district.value, ...tp } }),
      api.get('/h5/alerts', { params: { date: dParam, district: district.value, ...tp } }),
    ])
    stats.install = d?.kinds?.install || { total: 0, received: 0, pending: 0, ratio: 0 }
    stats.complaint = d?.kinds?.complaint || { total: 0, received: 0, pending: 0, ratio: 0 }
    alerts.count = a?.count || 0
    alerts.items = a?.items || []
    alerts.overdue_hours = a?.overdue_hours || 2
  } catch (e) { ElMessage.error('加载看板失败：' + (e?.message || e)) }
  finally { loading.value = false }
  await loadPending()
  await loadReceipts()
}

async function loadReceipts() {
  try {
    receipts.value = await api.get('/h5/receipt-list', {
      params: { kind: receiptKind.value, date: getDateParam(), district: district.value, ...getTimeParams() }
    })
  } catch (e) { ElMessage.error('加载已收清单失败：' + (e?.message || e)) }
}

async function loadPending() {
  try {
    const params = {
      kind: kind.value,
      date: getDateParam(),
      district: district.value,
      hide_unfinished: showUnfinished.value ? 0 : 1,
      ...getTimeParams(),
    }
    const res = await api.get('/h5/pending', { params })
    groups.value = (res || []).map((g, i) => ({ ...g, key: `g${i}` }))
    expanded.value = []
    allExpanded.value = false
  } catch (e) { ElMessage.error('加载待收清单失败：' + (e?.message || e)) }
}

function onTimeRangeChange() {
  if (timeStart.value && timeEnd.value && timeStart.value > timeEnd.value) {
    ElMessage.warning('开始时间不能晚于结束时间，已清空两个时间')
    timeStart.value = ''
    timeEnd.value = ''
  }
  loadAll()
}
function clearTimeRange() {
  timeStart.value = ''
  timeEnd.value = ''
  loadAll()
}

// 全部展开/折叠
function expandAll() {
  if (groups.value.length === 0) { ElMessage.warning('当前无待收工单'); return }
  if (allExpanded.value) {
    expanded.value = []
    allExpanded.value = false
  } else {
    expanded.value = groups.value.map(g => g.key)
    allExpanded.value = true
  }
}
function onExpandVal(row, val) {
  if (Array.isArray(val)) expanded.value = val.map(r => r.key)
  else if (typeof val === 'boolean' && val && row?.key) expanded.value = [row.key]
}

async function saveReceipts(items, successMsg) {
  if (!items.length) return
  try {
    const res = await api.patch('/h5/receipts', { items })
    const updated = Number(res?.updated ?? 0)
    if (updated === 0) {
      ElMessage.error('标记未生效：未匹配到任何工单（请刷新后重试）')
      await loadAll()
      return
    }
    ElMessage.success(successMsg || `已标记 ${updated} 单为已参评`)
    await loadAll()
  } catch (e) { ElMessage.error('操作失败：' + (e?.message || e)) }
}

function markOne(item) {
  saveReceipts([{ order_no: item.order_no, kind: item.kind, h5_received: '已参评' }], '已标记 1 单为已参评')
}
function markAll(group) {
  saveReceipts(group.items.map(it => ({ order_no: it.order_no, kind: it.kind, h5_received: '已参评' })))
}
async function cancelReceipt(row) {
  try {
    await api.patch('/h5/receipts', { items: [{ order_no: row.order_no, kind: row.kind, h5_received: '' }] })
    ElMessage.success('已取消该工单的已参评标记，已回到下方待收清单')
    await loadAll()
  } catch (e) { ElMessage.error('取消失败：' + (e?.message || e)) }
}

onMounted(() => {
  loadAll()
})
</script>

<style scoped>
.h5-page { display: flex; flex-direction: column; gap: 10px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.toolbar-left { display: flex; align-items: center; gap: 10px; }
.page-title { font-size: 17px; font-weight: 700; }
.toolbar-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.import-grid { display: flex; flex-direction: column; gap: 12px; }
.import-item { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.import-name { font-weight: 600; width: 210px; }
.import-hint { margin-top: 8px; font-size: 12px; color: #909399; }
.stat-card { }
.card-head { margin-bottom: 10px; }
.card-title { font-weight: 700; font-size: 14px; }
.card-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.cell { background: #f5f7fa; border-radius: 6px; padding: 10px 6px; text-align: center; }
.cell .num { font-size: 22px; font-weight: 700; color: #303133; }
.cell .lab { font-size: 12px; color: #909399; margin-top: 2px; }
.cell.ok .num { color: #67c23a; }
.cell.warn .num { color: #e6a23c; }
.alert-card { border: 1px solid #f56c6c !important; }
.alert-head { display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
.alert-title { color: #f56c6c; font-weight: 700; font-size: 14px; }
.alert-body { margin-top: 8px; }
.list-head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.list-head .el-checkbox { font-weight: normal; }
.list-head .status-tag { color: #909399; }
.list-tip { font-size: 12px; color: #909399; }
.list-tools { margin-left: auto; display: flex; gap: 6px; }
.time-range { display: inline-flex; align-items: center; gap: 6px; margin-left: 4px; }
.time-sep { color: #909399; font-size: 12px; }
.detail-box { padding: 8px 12px 12px 24px; background: #fafafa; }
.detail-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 600; }
.empty-tip { text-align: center; color: #909399; padding: 30px; }
.up-res { font-size: 12px; color: #67c23a; }
:deep(.el-table .cell) { white-space: nowrap; }
:deep(.el-table) { table-layout: auto; }
:deep(.el-table__body-wrapper) { overflow-x: auto; }
@media (max-width: 768px) {
  .import-name { width: auto; }
  .list-tools { margin-left: 0; }
  .card-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
