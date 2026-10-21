<template>
  <div>
    <!-- 工具栏 + 统计卡 -->
    <el-card class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" @change="load" />
        <el-select v-model="districtSel" style="width:150px" @change="onDistrictChange">
          <el-option label="全部区县" value="全部" />
          <el-option v-for="d in DISTRICT_ORDER" :key="d" :label="d" :value="d" />
        </el-select>
        <el-button v-if="isAdmin" type="primary" @click="openUpload">
          <el-icon><Upload /></el-icon>&nbsp;上传回访清单
        </el-button>
        <el-tag v-if="rates.date" size="small" type="info">当日数据：{{ rates.date }}</el-tag>
        <span class="tip">口径：规范率 =（接通量 − 有问题工单量）/ 接通量 · 低于 99% 标红 · 不规范仅统计「外呼成功」且勾选项为「不是」</span>
      </div>

      <el-row :gutter="12" class="stat-row">
        <el-col v-for="c in statCards" :key="c.label" :xs="12" :sm="8" :md="4">
          <el-card class="stat-card" shadow="never">
            <div class="stat-label">{{ c.label }}</div>
            <div class="stat-value" :style="{ color: c.color || '' }">{{ c.value }}</div>
            <div class="stat-sub">{{ c.sub }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-tabs v-model="tab">
        <!-- ============ 区县规范率宽表 ============ -->
        <el-tab-pane label="区县规范率宽表" name="wide">
          <el-radio-group v-model="wideKind" size="small" style="margin-bottom:10px">
            <el-radio-button value="装机">装机即访</el-radio-button>
            <el-radio-button value="投诉">投诉即访</el-radio-button>
          </el-radio-group>
          <template v-if="!hasRate">
            <el-empty description="暂无规范率通报，请点击右上角「上传回访清单」" />
          </template>
          <template v-else>
            <template v-for="p in ['当日', '当月']" :key="p">
            <div class="block-title">{{ blockLabel(p) }}</div>
            <el-table :data="orderIn(wideKind, p)" size="small" border max-height="460" class="wide-table">
              <el-table-column prop="district" label="区县" width="90" fixed />
              <el-table-column prop="outbound" label="外呼量" width="80" align="right" sortable />
              <el-table-column prop="connected" label="接通量" width="80" align="right" sortable />
              <el-table-column prop="problem" label="有问题工单量" width="105" align="right" sortable>
                <template #default="{ row }">
                  <span :class="{ 'gp-red': row.problem > 0 }">{{ row.problem }}</span>
                </template>
              </el-table-column>
              <el-table-column label="问题单占比" width="95" align="right">
                <template #default="{ row }">{{ row.problem_rate }}%</template>
              </el-table-column>
              <el-table-column label="规范率" width="110" align="right" sortable sort-by="rate">
                <template #default="{ row }">
                  <span :class="{ 'gp-red': row.rate < 99 }" style="font-weight:600">{{ row.rate }}%</span>
                </template>
              </el-table-column>
              <el-table-column v-for="nm in metricNames" :key="nm" :label="nm" align="center">
                <el-table-column :label="coverLabel" width="76" align="right">
                  <template #default="{ row }">{{ mv(row, nm).cover }}</template>
                </el-table-column>
                <el-table-column label="不是" width="60" align="right">
                  <template #default="{ row }">
                    <span :class="{ 'gp-red': mv(row, nm).wrong > 0 }">{{ mv(row, nm).wrong }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="占比" width="74" align="right">
                  <template #default="{ row }">{{ mv(row, nm).rate }}%</template>
                </el-table-column>
              </el-table-column>
            </el-table>
            </template>
          </template>
        </el-tab-pane>

        <!-- ============ 不规范明细 ============ -->
        <el-tab-pane label="不规范明细" name="detail">
          <div class="toolbar" style="margin-bottom:10px">
            <el-input v-model="kw" placeholder="搜索责任人/工单号/区县" clearable style="width:230px" />
            <el-radio-group v-model="detailKind" size="small">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="装机">装机</el-radio-button>
              <el-radio-button value="投诉">投诉</el-radio-button>
            </el-radio-group>
            <span class="tip">共 {{ filteredIssues.length }} 条 · 按责任人排序</span>
          </div>
          <el-table :data="filteredIssues" size="small" max-height="480"
                    :default-sort="{ prop: 'handler', order: 'ascending' }"
                    empty-text="当前月份/区县暂无「不规范」记录">
            <el-table-column prop="district" label="区县" width="95" sortable />
            <el-table-column prop="handler" label="责任人" min-width="110" sortable />
            <el-table-column prop="item" label="不规范项" min-width="110" sortable>
              <template #default="{ row }">
                <el-tag size="small" type="danger">{{ row.item }}</el-tag>
                <el-tag size="small" :type="row.kind === '装机' ? 'primary' : 'warning'" style="margin-left:6px">{{ row.kind }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="order_no" label="工单号" min-width="180" show-overflow-tooltip sortable />
            <el-table-column prop="completion_time" label="回单时间" width="160" sortable />
          </el-table>
        </el-tab-pane>

        <!-- ============ 图表分析 ============ -->
        <el-tab-pane label="图表分析" name="chart">
          <el-row :gutter="16">
            <el-col :xs="24" :md="14">
              <div class="block-title">区县规范率对比（当月 · 红色虚线 = 99%）</div>
              <div ref="rateRef" class="chart-mid" />
            </el-col>
            <el-col :xs="24" :md="10">
              <div class="block-title">不规范项分布（当月）</div>
              <div ref="itemRef" class="chart-mid" />
            </el-col>
          </el-row>
          <el-row :gutter="16" style="margin-top:8px">
            <el-col :xs="24" :md="14">
              <div class="block-title">区县有问题工单量（当月）</div>
              <div ref="problemRef" class="chart-mid" />
            </el-col>
            <el-col :xs="24" :md="10">
              <div class="block-title">指标「不是」数量（当月）</div>
              <div ref="metricRef" class="chart-mid" />
            </el-col>
          </el-row>
        </el-tab-pane>

        <!-- ============ 指标维度下钻 ============ -->
        <el-tab-pane label="指标维度下钻" name="drill">
          <div class="toolbar" style="margin-bottom:10px">
            <el-radio-group v-model="drillKind" size="small">
              <el-radio-button value="装机">装机即访</el-radio-button>
              <el-radio-button value="投诉">投诉即访</el-radio-button>
            </el-radio-group>
            <el-radio-group v-model="drillPeriod" size="small">
              <el-radio-button value="当日">当日</el-radio-button>
              <el-radio-button value="当月">当月</el-radio-button>
            </el-radio-group>
            <span class="tip">点击单元格「不是」数量可下钻到对应的不规范明细</span>
          </div>
          <el-table :data="drillRows" size="small" border max-height="480" empty-text="暂无数据">
            <el-table-column prop="district" label="区县" width="100" fixed />
            <el-table-column label="规范率" width="100" align="right">
              <template #default="{ row }">
                <span :class="{ 'gp-red': row.rate < 99 }" style="font-weight:600">{{ row.rate }}%</span>
              </template>
            </el-table-column>
            <el-table-column v-for="nm in metricNames" :key="nm" :label="nm" width="104" align="center">
              <template #default="{ row }">
                <span class="cell-link" :class="{ 'gp-red': mv(row, nm).wrong > 0 }"
                      :title="`调研量 ${mv(row, nm).cover} · 占比 ${mv(row, nm).rate}%`"
                      @click="drillTo(row.district, nm)">{{ mv(row, nm).wrong }}</span>
              </template>
            </el-table-column>
            <el-table-column label="合计" width="90" align="center" fixed="right">
              <template #default="{ row }">
                <span :class="{ 'gp-red': row.problem > 0 }">{{ row.problem }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 指标下钻明细对话框 -->
    <el-dialog v-model="drillDlg" :title="drillTitle" width="900px" top="8vh" destroy-on-close>
      <el-table :data="drillIssues" size="small" max-height="460" empty-text="该指标在当前月份/区县无对应明细记录">
        <el-table-column prop="district" label="区县" width="95" />
        <el-table-column prop="handler" label="责任人" min-width="110" sortable />
        <el-table-column prop="item" label="不规范项" min-width="110">
          <template #default="{ row }"><el-tag size="small" type="danger">{{ row.item }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="order_no" label="工单号" min-width="180" show-overflow-tooltip />
        <el-table-column prop="completion_time" label="回单时间" width="160" />
      </el-table>
    </el-dialog>

    <!-- 上传回访清单对话框 -->
    <el-dialog v-model="dlg" title="上传 AI装维即访 回访清单" width="600px">
      <el-form label-width="90px">
        <el-form-item label="说明">
          <span class="tip">解析「装后即访-通报表」「投诉即访-通报表」（区县规范率）+「当月装机/投诉即访清单」（不规范明细），按月替换入库。</span>
        </el-form-item>
        <el-form-item label="回访清单">
          <el-upload drag :show-file-list="false" accept=".xlsx,.xls" :auto-upload="false" :on-change="onFileChange">
            <div class="el-upload__text" style="padding:10px 0">
              <el-icon size="28" color="#1a6fdb"><UploadFilled /></el-icon>
              <div>{{ uploading ? '解析中…' : '拖拽或点击选择《AI装维即访回访清单》文件' }}</div>
            </div>
          </el-upload>
        </el-form-item>
      </el-form>
      <el-alert v-if="parsed" type="success" :closable="false"
                :title="`已解析：不规范明细 ${(parsed.装机 || 0) + (parsed.投诉 || 0)} 条（装机${parsed.装机 || 0} · 投诉${parsed.投诉 || 0}），规范率通报 ${parsed.stat_rows || 0} 行，数据日期 ${parsed.date || '-'}`"
                :description="rateSummary" />
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :disabled="!parsed" :loading="importing" @click="doImport">确认入库</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, onBeforeUnmount, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../store'

const store = useUserStore()
const isAdmin = computed(() => store.isAdmin)

const DISTRICT_ORDER = ['公安县', '洪湖市', '监利市', '江陵县', '荆州开发区', '荆州区', '沙市区', '石首市', '松滋市', '荆州']
const month = ref(new Date().toISOString().slice(0, 7))
const districtSel = ref('全部')
const tab = ref('wide')

const rates = ref({ has_data: false, date: '', rows: [] })
const issues = ref([])
const loading = ref(false)

// ---------------- 上传 ----------------
const dlg = ref(false)
const parsed = ref(null)
const uploading = ref(false)
const importing = ref(false)

// ---------------- 数据组织 ----------------
const byKey = computed(() => {
  const m = {}
  for (const r of rates.value.rows) {
    ;(m[r.kind] = m[r.kind] || {}), (m[r.kind][r.period] = m[r.kind][r.period] || {})
    m[r.kind][r.period][r.district] = r
  }
  return m
})
const orderIn = (kind, period) =>
  DISTRICT_ORDER.filter(d => byKey.value[kind]?.[period]?.[d]).map(d => byKey.value[kind][period][d])

const totalOf = (kind, period) => byKey.value[kind]?.[period]?.['荆州'] || null

const hasRate = computed(() => rates.value.rows.some(r => r.kind === wideKind.value))
function blockLabel(p) {
  const head = wideKind.value === '装机' ? 'AI装后即访' : 'AI投诉即访'
  if (p === '当月') return `当月累计-${head}`
  const d = rates.value.date
  return d ? `${Number(d.slice(8))}日-${head}` : `当日-${head}`
}

const coverLabel = computed(() => (wideKind.value === '装机' ? '调研量' : '覆盖量'))

const statCards = computed(() => {
  const card = (kind, period) => {
    const t = totalOf(kind, period)
    const rate = t ? t.rate : 0
    return {
      label: `${kind}·${period}规范率`, value: t ? `${t.rate}%` : '-',
      sub: t ? `问题 ${t.problem} 单 / 接通 ${t.connected} 单` : '暂无数据',
      color: t && rate >= 99 ? '#67c23a' : '#f56c6c',
    }
  }
  const day = rates.value.date
  return [
    card('装机', '当日'), card('装机', '当月'), card('投诉', '当日'), card('投诉', '当月'),
    { label: '不规范记录', value: issues.value.length, sub: `${month.value} · 工单×不规范项`, color: '#e6a23c' },
  ]
})

async function load() {
  loading.value = true
  try {
    const [rt, iss] = await Promise.all([
      api.get('/dashboard/ai-visit-rates', { params: { month: month.value } }),
      api.get('/dashboard/ai-visit-issues', { params: { month: month.value } }),
    ])
    rates.value = rt
    issues.value = iss.rows || []
  } catch (e) {
    rates.value = { has_data: false, date: '', rows: [] }
    issues.value = []
  } finally { loading.value = false }
  renderCharts()
}

function onDistrictChange() { renderCharts() }

// ---------------- 宽表 ----------------
const wideKind = ref('装机')
const metricNames = computed(() => {
  const first = (rates.value.rows.find(r => r.kind === wideKind.value) || {}).metrics || []
  return first.map(m => m.name)
})
const mv = (row, name) => (row.metrics || []).find(m => m.name === name) || { cover: 0, wrong: 0, rate: 0 }

// ---------------- 不规范明细 ----------------
const kw = ref('')
const detailKind = ref('')
const filteredIssues = computed(() => {
  let list = issues.value
  const d = districtSel.value
  if (d !== '全部') list = list.filter(r => r.district === d)
  if (detailKind.value) list = list.filter(r => r.kind === detailKind.value)
  const k = kw.value.trim()
  if (k) list = list.filter(r => (r.handler || '').includes(k) || (r.district || '').includes(k) || (r.order_no || '').includes(k))
  return [...list].sort((a, b) => (a.handler || '').localeCompare(b.handler || ''))
})

// ---------------- 指标维度下钻 ----------------
const drillKind = ref('装机')
const drillPeriod = ref('当月')
const drillRows = computed(() => orderIn(drillKind.value, drillPeriod.value))
const drillDlg = ref(false)
const drillCtx = ref({ district: '', item: '' })
const drillTitle = computed(() => `${drillCtx.value.district} · ${drillCtx.value.item} · 不规范明细`)
const drillIssues = computed(() => issues.value.filter(r =>
  r.kind === drillKind.value && r.district === drillCtx.value.district && r.item === drillCtx.value.item))
function drillTo(district, item) {
  drillCtx.value = { district, item }
  drillDlg.value = true
}

// ---------------- 图表 ----------------
const rateRef = ref(); const itemRef = ref(); const problemRef = ref(); const metricRef = ref()
let rateChart = null, itemChart = null, problemChart = null, metricChart = null

function renderCharts() {
  nextTick(() => {
    // 图表容器在非「图表分析」标签页时为隐藏状态（宽度为 0），此时初始化会得到空图；
    // 仅在图表标签页激活时渲染，配合 watch(tab) 在切换时补渲染
    if (tab.value !== 'chart') return
    const cur = districtSel.value
    const ins = orderIn('装机', '当月'), cps = orderIn('投诉', '当月')
    const dists = DISTRICT_ORDER.filter(d => d !== '荆州' && (cur === '全部' || d === cur))
    const pick = (arr, d) => (arr.find(x => x.district === d) || {})
    // 1) 区县规范率对比
    if (!rateChart && rateRef.value) rateChart = echarts.init(rateRef.value)
    rateChart?.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['装机规范率', '投诉规范率'] },
      grid: { left: 50, right: 20, top: 40, bottom: 40 },
      xAxis: { type: 'category', data: dists, axisLabel: { interval: 0, rotate: 30 } },
      yAxis: { type: 'value', min: 95, max: 100, name: '%' },
      series: [
        { name: '装机规范率', type: 'bar', data: dists.map(d => pick(ins, d).rate ?? null),
          itemStyle: { color: '#1a6fdb' },
          markLine: { silent: true, symbol: 'none', label: { formatter: '99%' },
            lineStyle: { color: '#f56c6c', type: 'dashed' }, data: [{ yAxis: 99 }] } },
        { name: '投诉规范率', type: 'bar', data: dists.map(d => pick(cps, d).rate ?? null),
          itemStyle: { color: '#e6a23c' } },
      ],
    })
    // 2) 不规范项分布
    if (!itemChart && itemRef.value) itemChart = echarts.init(itemRef.value)
    const itemMap = {}
    for (const r of issues.value) {
      if (cur !== '全部' && r.district !== cur) continue
      itemMap[r.item] = (itemMap[r.item] || 0) + 1
    }
    const itemData = Object.entries(itemMap).sort((a, b) => b[1] - a[1])
    itemChart?.setOption({
      tooltip: { trigger: 'item' },
      series: [{ type: 'pie', radius: ['40%', '68%'],
        data: itemData.map(([name, value]) => ({ name, value })),
        label: { formatter: '{b}\n{c}条' } }],
    })
    // 3) 区县有问题工单量
    if (!problemChart && problemRef.value) problemChart = echarts.init(problemRef.value)
    problemChart?.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['装机', '投诉'] },
      grid: { left: 45, right: 20, top: 40, bottom: 40 },
      xAxis: { type: 'category', data: dists, axisLabel: { interval: 0, rotate: 30 } },
      yAxis: { type: 'value', name: '单' },
      series: [
        { name: '装机', type: 'bar', stack: 't', data: dists.map(d => pick(ins, d).problem ?? 0),
          itemStyle: { color: '#1a6fdb' }, label: { show: true, position: 'inside', fontSize: 9 } },
        { name: '投诉', type: 'bar', stack: 't', data: dists.map(d => pick(cps, d).problem ?? 0),
          itemStyle: { color: '#e6a23c' }, label: { show: true, position: 'inside', fontSize: 9 } },
      ],
    })
    // 4) 指标「不是」数量
    if (!metricChart && metricRef.value) metricChart = echarts.init(metricRef.value)
    const names = ((ins[0] || {}).metrics || []).map(m => m.name)
    const sumWrong = (arr, nm) => arr.reduce(
      (s, r) => s + ((r.metrics || []).find(m => m.name === nm)?.wrong || 0), 0)
    metricChart?.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['装机', '投诉'] },
      grid: { left: 90, right: 20, top: 40, bottom: 30 },
      xAxis: { type: 'value', name: '条' },
      yAxis: { type: 'category', data: names.slice().reverse() },
      series: [
        { name: '装机', type: 'bar', data: names.slice().reverse().map(nm => sumWrong(ins, nm)),
          itemStyle: { color: '#1a6fdb' } },
        { name: '投诉', type: 'bar', data: names.slice().reverse().map(nm => sumWrong(cps, nm)),
          itemStyle: { color: '#e6a23c' } },
      ],
    })
    for (const c of [rateChart, itemChart, problemChart, metricChart]) c?.resize()
  })
}

const onRz = () => { rateChart?.resize(); itemChart?.resize(); problemChart?.resize(); metricChart?.resize() }

// ---------------- 上传流程 ----------------
const rateSummary = computed(() => {
  const rt = parsed.value?.rates || {}
  const parts = []
  for (const [k, v] of Object.entries(rt)) {
    if (v) parts.push(`${k} 规范率 ${v.rate}%（问题 ${v.problem} / 接通 ${v.connected}）`)
  }
  return parts.length ? '合计行：' + parts.join('；') : '入库将按月替换（同月重复上传直接覆盖）。'
})
function openUpload() { parsed.value = null; dlg.value = true }
async function onFileChange(file) {
  uploading.value = true
  parsed.value = null
  try {
    const fd = new FormData()
    fd.append('file', file.raw)
    parsed.value = await api.post('/sync/upload/ai_visit', fd)
    ElMessage.success(`解析成功：规范率 ${parsed.value.stat_rows} 行，不规范明细 ${(parsed.value.装机 || 0) + (parsed.value.投诉 || 0)} 条`)
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
    load()
  } catch (e) { /* 拦截器已提示 */ }
  finally { importing.value = false }
}

watch(tab, v => { if (v === 'chart') renderCharts() })

onMounted(() => { load(); window.addEventListener('resize', onRz) })
onBeforeUnmount(() => {
  window.removeEventListener('resize', onRz)
  rateChart?.dispose(); itemChart?.dispose(); problemChart?.dispose(); metricChart?.dispose()
})
</script>

<style scoped>
.tip { color: #909399; font-size: 12px; margin-left: 8px; }
.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.stat-row { margin-bottom: 8px; }
.stat-card { text-align: center; }
.stat-label { font-size: 12px; color: #909399; }
.stat-value { font-size: 22px; font-weight: 600; margin: 4px 0 2px; }
.stat-sub { font-size: 12px; color: #909399; }
.block-title { font-weight: 600; font-size: 14px; margin: 6px 0 8px; }
.gp-red { color: #f56c6c; font-weight: 600; }
.cell-link { cursor: pointer; }
.cell-link:hover { text-decoration: underline; }
.chart-mid { height: 340px; }
.wide-table :deep(.el-table__header th) { background: #f5f7fa; }
@media (max-width: 768px) {
  .stat-value { font-size: 18px; }
  .step-tip { display: none; }
  .chart-mid { height: 260px; }
  .page-card :deep(.el-card__body) { padding: 12px; }
}
</style>
