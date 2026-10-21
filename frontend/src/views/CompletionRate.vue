<template>
  <div>
    <!-- 竣工率激励通报分析（上传通报表后自动展示） -->
    <el-card class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" @change="load" />
        <el-select v-model="districtSel" style="width:140px" @change="load">
          <el-option label="全部区县" value="全部" />
          <el-option v-for="d in districtOptions" :key="d" :label="d" :value="d" />
        </el-select>
        <el-button v-if="isAdmin" type="primary" @click="openUpload">上传通报表</el-button>
        <el-button v-if="isAdmin" type="success" @click="openWideUpload">上传装机宽表</el-button>
        <span class="tip">通报口径：宽带竣工率 = 回单 / 收单</span>
        <span v-if="report.latest_upload" class="tip">通报上传：{{ report.latest_upload }}</span>
      </div>

      <template v-if="report.has_data">
        <el-row :gutter="12" class="stat-row">
          <el-col v-for="c in statCards" :key="c.label" :xs="12" :sm="8" :md="4">
            <el-card class="stat-card" shadow="never">
              <div class="stat-label">{{ c.label }}</div>
              <div class="stat-value" :style="{ color: c.color || '' }">{{ c.value }}</div>
              <div class="stat-sub">{{ c.sub }}</div>
            </el-card>
          </el-col>
        </el-row>

        <el-tabs v-model="rdim">
          <el-tab-pane label="区县汇总" name="districts">
            <el-table :data="viewDistricts" size="small" max-height="420">
              <el-table-column prop="rank" label="#" width="50" />
              <el-table-column prop="district" label="区县" min-width="100" />
              <el-table-column prop="people" label="人数" min-width="70" sortable />
              <el-table-column prop="bb_orders" label="宽带收单" min-width="90" sortable />
              <el-table-column prop="bb_done" label="宽带回单" min-width="90" sortable />
              <el-table-column label="宽带竣工率" min-width="150">
                <template #default="{ row }">
                  <el-progress :percentage="row.rate" :color="rateColor(row.rate)" :stroke-width="10" />
                </template>
              </el-table-column>
              <el-table-column prop="fttr_rate" label="FTTR竣工率" min-width="90" sortable>
                <template #default="{ row }">{{ row.fttr_rate }}%</template>
              </el-table-column>
              <el-table-column prop="incentive" label="激励金额(元)" min-width="100" sortable />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="人员明细" name="personal">
            <el-input v-model="kw" placeholder="搜索人员/区县" clearable style="width:200px;margin-bottom:10px" />
            <el-table :data="filteredPersonal" size="small" max-height="420">
              <el-table-column type="index" label="#" width="50" :index="i => i + 1" />
              <el-table-column prop="handler" label="人员" min-width="90" />
              <el-table-column prop="district" label="区县" min-width="90" />
              <el-table-column prop="bb_orders" label="宽带收单" min-width="85" sortable />
              <el-table-column prop="bb_done" label="宽带回单" min-width="85" sortable />
              <el-table-column label="宽带竣工率" min-width="140">
                <template #default="{ row }">
                  <el-progress :percentage="row.bb_rate" :color="rateColor(row.bb_rate)" :stroke-width="10" />
                </template>
              </el-table-column>
              <el-table-column prop="fttr_orders" label="FTTR收单" min-width="85" sortable />
              <el-table-column prop="fttr_done" label="FTTR回单" min-width="85" sortable />
              <el-table-column prop="fttr_rate" label="FTTR竣工率" min-width="90" sortable>
                <template #default="{ row }">{{ row.fttr_rate }}%</template>
              </el-table-column>
              <el-table-column label="激励(元)" min-width="90" sortable prop="incentive">
                <template #default="{ row }">
                  <el-tag :type="row.incentive > 0 ? 'success' : 'info'" size="small">{{ row.incentive }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
      <el-alert v-else type="info" :closable="false"
                title="本月暂无通报数据" description="点击右上角「上传通报表」导入竣工率激励预通报表（解析「激励明细」sheet），上传后此处自动生成分析卡片。" />
    </el-card>

    <!-- 宽表口径（装机宽表，月累计） -->
    <el-card v-if="wide.has_data" class="page-card">
      <template #header>
        <div class="card-header" style="display:flex;align-items:center;gap:12px">
          <span class="title">宽表口径（装机宽表 · {{ month }} 月累计）</span>
          <el-tag size="small" type="info">数据较综合查询延迟约 2 小时</el-tag>
        </div>
      </template>
      <el-row :gutter="12" class="stat-row">
        <el-col v-for="c in wideCards" :key="c.label" :xs="12" :sm="8" :md="4">
          <el-card class="stat-card" shadow="never">
            <div class="stat-label">{{ c.label }}</div>
            <div class="stat-value" :style="{ color: c.color || '' }">{{ c.value }}</div>
            <div class="stat-sub">{{ c.sub }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="16">
        <el-col :xs="24" :md="10">
          <div class="sub-title">区县竣工率排名</div>
          <el-table :data="wideDistricts" size="small" max-height="360">
            <el-table-column prop="rank" label="#" width="50" />
            <el-table-column prop="district" label="区县" min-width="90" />
            <el-table-column prop="total" label="受理" width="70" sortable />
            <el-table-column prop="done" label="已施工" width="70" sortable />
            <el-table-column label="竣工率" min-width="140">
              <template #default="{ row }">
                <el-progress :percentage="row.rate" :color="rateColor(row.rate)" :stroke-width="10" />
              </template>
            </el-table-column>
          </el-table>
        </el-col>
        <el-col :xs="24" :md="14">
          <div class="sub-title">退单原因 TOP10</div>
          <el-table :data="wide.refund_reasons || []" size="small" max-height="360">
            <el-table-column type="index" label="#" width="50" />
            <el-table-column prop="reason" label="一级退单原因" />
            <el-table-column prop="count" label="次数" width="80" sortable />
          </el-table>
        </el-col>
      </el-row>

      <div class="sub-title" style="margin-top:16px">装机人员竣工率 TOP20</div>
      <el-table :data="wideUsers" size="small" max-height="360">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="handler" label="回单人员" min-width="100" />
        <el-table-column prop="total" label="受理" width="80" sortable />
        <el-table-column prop="done" label="已施工" width="80" sortable />
        <el-table-column label="竣工率" min-width="140">
          <template #default="{ row }">
            <el-progress :percentage="row.rate" :color="rateColor(row.rate)" :stroke-width="10" />
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 工单口径（work_orders 实时计算） -->
    <el-card class="page-card">
      <template #header>
        <div class="card-header" style="display:flex;align-items:center;gap:12px">
          <span class="title">工单口径（系统工单实时计算）</span>
          <span class="tip" v-if="report.latest_order_time">数据截止：{{ report.latest_order_time.slice(0, 16) }}</span>
        </div>
      </template>
      <div class="toolbar">
        <el-radio-group v-model="dim" @change="load">
          <el-radio-button value="districts">区县排名</el-radio-button>
          <el-radio-button value="users">人员排名</el-radio-button>
          <el-radio-button value="daily">每日趋势</el-radio-button>
        </el-radio-group>
      </div>

      <el-table v-if="dim === 'districts'" :data="viewOrderDistricts" size="small" max-height="480">
        <el-table-column prop="rank" label="#" width="50" />
        <el-table-column prop="district" label="区县" min-width="100" />
        <el-table-column prop="total" label="个人受理" min-width="90" sortable />
        <el-table-column prop="completed" label="已施工" min-width="90" sortable />
        <el-table-column label="竣工率" min-width="160">
          <template #default="{ row }">
            <el-progress :percentage="row.rate" :color="rateColor(row.rate)" :stroke-width="12" />
          </template>
        </el-table-column>
      </el-table>

      <el-table v-else-if="dim === 'users'" :data="users" size="small" max-height="480">
        <el-table-column prop="rank" label="#" width="50" />
        <el-table-column prop="handler" label="人员" min-width="90" />
        <el-table-column prop="total" label="个人受理" min-width="90" sortable />
        <el-table-column prop="completed" label="已施工" min-width="90" sortable />
        <el-table-column label="竣工率" min-width="160">
          <template #default="{ row }">
            <el-progress :percentage="row.rate" :color="rateColor(row.rate)" :stroke-width="12" />
          </template>
        </el-table-column>
      </el-table>

      <div v-else ref="dailyRef" style="height: 500px" />
    </el-card>

    <el-card class="page-card">
      <template #header><div class="card-header"><span class="title">退单 / 改约盯控</span></div></template>
      <el-row :gutter="16">
        <el-col :xs="24" :md="8">
          <el-table :data="cr.by_type" size="small">
            <el-table-column prop="type" label="类型" />
            <el-table-column prop="count" label="数量" />
          </el-table>
        </el-col>
        <el-col :xs="24" :md="8">
          <el-table :data="cr.by_district" size="small" max-height="300">
            <el-table-column prop="district" label="区县" />
            <el-table-column prop="count" label="退改单量" sortable />
          </el-table>
        </el-col>
        <el-col :xs="24" :md="8">
          <el-table :data="cr.top_reasons" size="small" max-height="300">
            <el-table-column prop="reason" label="原因" />
            <el-table-column prop="count" label="次数" sortable />
          </el-table>
        </el-col>
      </el-row>
    </el-card>

    <!-- 上传通报表对话框 -->
    <el-dialog v-model="dlg" title="上传竣工率激励通报表" width="560px">
      <el-form label-width="80px">
        <el-form-item label="通报月份">
          <el-date-picker v-model="upMonth" type="month" value-format="YYYY-MM" :clearable="false" />
        </el-form-item>
        <el-form-item label="通报文件">
          <el-upload drag :show-file-list="false" accept=".xlsx,.xlsm" :auto-upload="false"
                     :on-change="onFileChange">
            <div class="el-upload__text" style="padding:10px 0">
              <el-icon size="28" color="#1a6fdb"><UploadFilled /></el-icon>
              <div>{{ uploading ? '解析中…' : '拖拽或点击选择《竣工率激励预通报》xlsx' }}</div>
            </div>
          </el-upload>
        </el-form-item>
      </el-form>
      <el-alert v-if="parsed" type="success" :closable="false"
                :title="`已解析 ${parsed.people} 名人员，激励合计 ${parsed.incentive_total} 元，月份 ${parsed.months[0]}`"
                description="入库将按月替换（同月重复上传直接覆盖）。请核对后确认。" />
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :disabled="!parsed" :loading="importing" @click="doImport">确认入库</el-button>
      </template>
    </el-dialog>

    <!-- 上传装机宽表对话框 -->
    <el-dialog v-model="dlgWide" title="上传装机宽表" width="560px">
      <el-form label-width="80px">
        <el-form-item label="说明">
          <span class="tip">解析「数据1」sheet，月份由受理时间自动推导，按月替换入库。</span>
        </el-form-item>
        <el-form-item label="宽表文件">
          <el-upload drag :show-file-list="false" accept=".xlsx,.xls" :auto-upload="false" :on-change="onWideFileChange">
            <div class="el-upload__text" style="padding:10px 0">
              <el-icon size="28" color="#67c23a"><UploadFilled /></el-icon>
              <div>{{ wideUploading ? '解析中…' : '拖拽或点击选择《宽表查询》文件' }}</div>
            </div>
          </el-upload>
        </el-form-item>
      </el-form>
      <el-alert v-if="wideParsed" type="success" :closable="false"
                :title="`已解析 ${wideParsed.orders} 条工单，月份 ${wideParsed.months?.join('、')}，已施工 ${wideParsed.done}，退单 ${wideParsed.refund}`"
                description="入库将按月替换（同月重复上传直接覆盖）。" />
      <template #footer>
        <el-button @click="dlgWide = false">取消</el-button>
        <el-button type="success" :disabled="!wideParsed" :loading="wideImporting" @click="doWideImport">确认入库</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, onBeforeUnmount, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import api from '../api'
import { useUserStore } from '../store'

const store = useUserStore()
const isAdmin = computed(() => store.isAdmin)
const month = ref(new Date().toISOString().slice(0, 7))
const dim = ref('districts')
const districts = ref([])
const users = ref([])
const cr = ref({ by_type: [], by_district: [], top_reasons: [] })
const dailyRef = ref()
let chart = null

// 通报数据
const report = ref({ has_data: false, summary: {}, districts: [], personal: [] })
const rdim = ref('districts')
const kw = ref('')
const districtSel = ref('全部')                       // 区县筛选，默认全部
const dlg = ref(false)
const upMonth = ref(month.value)
const parsed = ref(null)
const uploading = ref(false)
const importing = ref(false)

// 宽表口径
const wide = ref({ has_data: false, install: {}, refund_reasons: [] })
const dlgWide = ref(false)
const wideParsed = ref(null)
const wideUploading = ref(false)
const wideImporting = ref(false)

const wideCards = computed(() => {
  const i = wide.value.install || {}
  const rate24 = i.total ? +((1 - (i.timeout_24h || 0) / i.total) * 100).toFixed(1) : 0
  const rate8 = i.total ? +((1 - (i.timeout_8h || 0) / i.total) * 100).toFixed(1) : 0
  const perfRate = i.total ? +((i.perform || 0) / i.total * 100).toFixed(1) : 0
  const refundRate = i.total ? +((i.refund || 0) / i.total * 100).toFixed(1) + '%' : '0%'
  return [
    { label: '受理量', value: i.total || 0, sub: '单' },
    { label: '完工量', value: i.done || 0, sub: `竣工率 ${i.rate || 0}%`, color: rateColor(i.rate || 0) },
    { label: '待装', value: i.pending || 0, sub: '单' },
    { label: '退撤单', value: i.refund || 0, sub: '单', color: '#e6a23c' },
    { label: '24h及时率', value: `${rate24}%`, sub: `超时 ${i.timeout_24h || 0} 单`, color: rateColor(rate24) },
    { label: '8h及时率', value: `${rate8}%`, sub: `超时 ${i.timeout_8h || 0} 单`, color: rateColor(rate8) },
    { label: '履约率', value: `${perfRate}%`, sub: `履约 ${i.perform || 0} 单`, color: rateColor(perfRate) },
    { label: '退单率', value: refundRate, sub: `${i.refund || 0} 单`, color: '#f56c6c' },
  ]
})

const wideDistricts = computed(() => {
  let list = (wide.value.install?.by_district) || []
  if (districtSel.value !== '全部') list = list.filter(d => d.district === districtSel.value)
  return list
})

const wideUsers = computed(() => {
  return ((wide.value.install?.by_handler) || []).slice(0, 20)
})

const rateColor = r => (r >= 70 ? '#67c23a' : r >= 50 ? '#e6a23c' : '#f56c6c')

// 区县下拉选项：通报区县 + 工单口径区县 合并去重
const districtOptions = computed(() => {
  const names = new Set()
  ;(report.value.districts || []).forEach(d => names.add(d.district))
  ;(districts.value || []).forEach(d => names.add(d.district))
  return [...names].filter(Boolean).sort()
})

// 区县汇总表：按所选区县过滤（保留原排名）
const viewDistricts = computed(() => {
  const list = report.value.districts || []
  return districtSel.value === '全部' ? list : list.filter(d => d.district === districtSel.value)
})

// 人员明细：区县 + 关键字过滤
const filteredPersonal = computed(() => {
  const k = kw.value.trim()
  let list = report.value.personal || []
  if (districtSel.value !== '全部') list = list.filter(p => p.district === districtSel.value)
  return k ? list.filter(p => p.handler.includes(k) || (p.district || '').includes(k)) : list
})

// 统计卡：基于过滤后的人员明细实时重算（区县筛选联动）
const statCards = computed(() => {
  const list = filteredPersonal.value
  const sum = k => list.reduce((s, p) => s + (Number(p[k]) || 0), 0)
  const bbO = sum('bb_orders'), bbD = sum('bb_done'), ftO = sum('fttr_orders'), ftD = sum('fttr_done')
  const inc = +sum('incentive').toFixed(2)
  const bbRate = bbO ? +(bbD / bbO * 100).toFixed(1) : 0
  const ftRate = ftO ? +(ftD / ftO * 100).toFixed(1) : 0
  return [
    { label: '参与人数', value: list.length, sub: `${list.filter(p => p.incentive <= 0).length} 人暂无激励` },
    { label: '宽带收单', value: bbO, sub: '单' },
    { label: '宽带回单', value: bbD, sub: '单' },
    { label: '宽带竣工率', value: `${bbRate}%`, sub: '回单/收单', color: rateColor(bbRate) },
    { label: 'FTTR竣工率', value: `${ftRate}%`, sub: `${ftD}/${ftO} 单` },
    { label: '激励总额', value: inc, sub: '元', color: '#67c23a' },
  ]
})

async function load() {
  const params = { month: month.value }
  const dparam = { month: month.value, district: districtSel.value === '全部' ? '' : districtSel.value }
  const [rp, d, u, c, w] = await Promise.all([
    api.get('/metrics/completion-rate/report', { params }),
    api.get('/metrics/completion-rate/districts', { params }),
    api.get('/metrics/completion-rate/users', { params: dparam }),
    api.get('/metrics/completion-rate/cancel-reschedule', { params }),
    api.get('/metrics/wide/summary', { params }).catch(() => ({ has_data: false })),
  ])
  report.value = rp
  districts.value = d
  users.value = u
  cr.value = c
  wide.value = w.has_data ? w : { has_data: false, install: {}, refund_reasons: [] }
  if (dim.value === 'daily') await drawDaily()
}

function openWideUpload() {
  wideParsed.value = null
  dlgWide.value = true
}

async function onWideFileChange(file) {
  wideUploading.value = true
  wideParsed.value = null
  try {
    const fd = new FormData()
    fd.append('file', file.raw)
    wideParsed.value = await api.post('/sync/upload/install_wide', fd)
    ElMessage.success(`解析成功：${wideParsed.value.orders} 条工单`)
  } catch (e) { /* 拦截器已提示 */ }
  finally { wideUploading.value = false }
}

async function doWideImport() {
  wideImporting.value = true
  try {
    const r = await api.post(`/sync/confirm/${wideParsed.value.batch_id}`)
    ElMessage.success(r.msg)
    dlgWide.value = false
    wideParsed.value = null
    load()
  } catch (e) { /* 拦截器已提示 */ }
  finally { wideImporting.value = false }
}

function openUpload() {
  upMonth.value = month.value
  parsed.value = null
  dlg.value = true
}

async function onFileChange(file) {
  uploading.value = true
  parsed.value = null
  try {
    const fd = new FormData()
    fd.append('file', file.raw)
    parsed.value = await api.post(`/sync/upload/completion_rate?month=${upMonth.value}`, fd)  // 只解析预览，不入库
    ElMessage.success(`解析成功：${parsed.value.people} 名人员，激励合计 ${parsed.value.incentive_total} 元`)
  } catch (e) { /* 拦截器已提示 */ }
  finally { uploading.value = false }
}

async function doImport() {
  importing.value = true
  try {
    const r = await api.post(`/sync/confirm/${parsed.value.batch_id}`)
    ElMessage.success(r.msg)
    dlg.value = false
    parsed.value = null
    month.value = upMonth.value
    load()                                                        // 入库后指标实时生效，卡片自动刷新
  } catch (e) { /* 拦截器已提示 */ }
  finally { importing.value = false }
}

async function drawDaily() {
  const daily = await api.get('/metrics/completion-rate/daily', { params: { month: month.value } })
  await nextTick()
  if (!chart) chart = echarts.init(dailyRef.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['受理', '已施工', '待处理', '竣工率(%)'] },
    grid: { left: 50, right: 50, top: 50, bottom: 40 },
    xAxis: { type: 'category', data: daily.map(d => d.day.slice(8)) },
    yAxis: [{ type: 'value', name: '单数' }, { type: 'value', name: '%', max: 100 }],
    series: [
      { name: '受理', type: 'bar', data: daily.map(d => d.total), itemStyle: { color: '#1a6fdb' },
        label: { show: true, position: 'top', fontSize: 10, color: '#1a6fdb' } },
      { name: '已施工', type: 'bar', data: daily.map(d => d.done), itemStyle: { color: '#67c23a' },
        label: { show: true, position: 'top', fontSize: 10, color: '#529b2e' } },
      { name: '待处理', type: 'bar', data: daily.map(d => d.pending), itemStyle: { color: '#e6a23c' },
        label: { show: true, position: 'top', fontSize: 10, color: '#b88230' } },
      { name: '竣工率(%)', type: 'line', yAxisIndex: 1, data: daily.map(d => d.rate), smooth: true,
        itemStyle: { color: '#f56c6c' },
        label: { show: true, position: 'top', fontSize: 10, color: '#f56c6c', formatter: v => v.value + '%' } },
    ],
  })
}

// 工单口径区县排名：同样按所选区县过滤（保留原排名）
const viewOrderDistricts = computed(() => {
  const list = districts.value || []
  return districtSel.value === '全部' ? list : list.filter(d => d.district === districtSel.value)
})

watch(dim, v => { if (v === 'daily') drawDaily() })
const onRz = () => chart?.resize()
onMounted(() => { load(); window.addEventListener('resize', onRz) })
onBeforeUnmount(() => { window.removeEventListener('resize', onRz); chart?.dispose() })
</script>

<style scoped>
.tip { color: #909399; font-size: 12px; margin-left: 8px; }
.stat-row { margin-bottom: 6px; }
.stat-card { text-align: center; }
.stat-label { font-size: 12px; color: #909399; }
.stat-value { font-size: 22px; font-weight: 600; margin: 4px 0 2px; }
.stat-sub { font-size: 12px; color: #909399; }
@media (max-width: 768px) {
  .toolbar { flex-wrap: wrap; row-gap: 8px; }
  .toolbar .tip { display: none; }
  .stat-value { font-size: 18px; }
  .sub-title { font-size: 13px; }
  .page-card :deep(.el-card__body) { padding: 12px; }
}
</style>
