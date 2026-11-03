<template>
  <div>
    <!-- 批量上传：一次选多个文件，自动识别指标 -->
    <el-card class="page-card">
      <template #header>
        <div class="card-header">
          <span class="title">📦 批量上传（一次选多个文件，自动识别指标）</span>
        </div>
      </template>
      <el-upload drag multiple accept=".xlsx,.xls,.csv" :auto-upload="false"
                 :show-file-list="true" :file-list="batchFileList" :on-change="onBatchChange"
                 :on-remove="onBatchRemove">
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">把当日所有通报文件拖到这里，或 <em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">
            系统按「文件名 + sheet 名」自动判断归属指标（竣工率 / 延伸服务 / 装机·投诉宽表 / 装机·投诉综合查询 / H5满意度 / AI装维即访 / 质检五项），
            无需逐个选择标签页。质检五项可一次多选全部附件自动合并。
          </div>
        </template>
      </el-upload>
      <div style="margin-top:12px;display:flex;align-items:center;gap:12px">
        <el-button type="success" :loading="batchLoading" :disabled="batchFileList.length === 0"
                   @click="batchUpload">
          <el-icon><MagicStick /></el-icon>&nbsp;自动识别并解析预览
        </el-button>
        <span style="color:#909399">已选 {{ batchFileList.length }} 个文件</span>
      </div>
    </el-card>

    <el-card class="page-card">
      <template #header><div class="card-header"><span class="title">数据同步（上传 → 预览 → 确认入库）</span></div></template>
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom:14px"
                title="三条铁律：① 先预览后入库（上传只解析不入库）② 时间一律从受理时间推导 ③ 确认入库后自动重算指标" />

      <el-tabs v-model="metric" @tab-change="loadHistory">
        <el-tab-pane v-for="(m, k) in modules" :key="k" :label="m.name" :name="k">
          <el-descriptions :column="2" size="small" border style="margin-bottom:14px">
            <el-descriptions-item label="入库目标">{{ m.target }}</el-descriptions-item>
            <el-descriptions-item label="模板列">{{ m.columns.join(' / ') }}</el-descriptions-item>
            <el-descriptions-item label="说明" :span="2">{{ m.note }}</el-descriptions-item>
          </el-descriptions>
          <el-upload :show-file-list="false" accept=".xlsx,.xls,.csv" :auto-upload="false"
                     :on-change="f => upload(k, f)">
            <el-button type="primary" :loading="uploading">
              <el-icon><Upload /></el-icon>&nbsp;选择 xlsx / csv 文件（解析预览）
            </el-button>
          </el-upload>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-card v-if="preview" class="page-card">
      <template #header>
        <div class="card-header">
          <span class="title">
            预览批次 {{ preview.batch_id }} · {{ preview.filename }} · 共 {{ preview.total_rows }} 行
            <el-tag size="small" type="info" style="margin-left:8px">未入库</el-tag>
          </span>
          <el-button type="success" :loading="confirming" @click="confirm">确认入库</el-button>
        </div>
      </template>
      <!-- 后端格式告警（如 H5满意度通报旧版导出无人员明细）：入库前明确提示 -->
      <el-alert v-if="preview.warn" :title="preview.warn" type="warning" :closable="false" show-icon
                style="margin-bottom:12px" />
      <!-- 重复工单号告警（投诉综合查询等）：同键重复时入库自动保留最新记录 -->
      <el-alert v-if="preview.dup_warning" :title="preview.dup_warning" type="warning" :closable="false"
                show-icon style="margin-bottom:12px" />
      <el-table :data="preview.preview" size="small" max-height="400">
        <el-table-column v-for="c in preview.columns" :key="c" :prop="c" :label="c" min-width="120" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- 批量识别结果：按指标分组，各自预览 + 确认入库 -->
    <el-card v-if="batchResults.length" class="page-card">
      <template #header>
        <div class="card-header">
          <span class="title">批量识别结果（{{ batchResults.length }} 项 · 已识别 {{ batchStats.recognized }}/{{ batchStats.total }} 个文件）</span>
          <el-button size="small" @click="batchResults = []">清空</el-button>
        </div>
      </template>
      <div v-for="(r, idx) in batchResults" :key="idx" class="batch-group" :class="{ 'batch-err': r.error }">
        <div class="batch-group-head">
          <el-tag :type="r.error ? 'danger' : 'primary'" size="small">{{ r.name }}</el-tag>
          <span class="batch-files" :title="(r.files || []).join('，')">{{ (r.files || []).join('，') }}</span>
          <span v-if="r.total_rows != null" class="batch-rows">{{ r.total_rows }} 行</span>
          <template v-if="r.error">
            <span class="batch-warn">⚠ {{ r.error }}</span>
            <el-select v-if="r.metric == null" size="small" placeholder="手动指定指标" style="width:180px"
                       @change="m => onManual(r, m)">
              <el-option v-for="(m, k) in modules" :key="k" :label="m.name" :value="k" />
            </el-select>
          </template>
          <el-button v-else type="success" size="small" :loading="confirmingBatch"
                     :disabled="r.confirmed" @click="confirmBatch(r)">
            {{ r.confirmed ? '已入库' : '确认入库' }}
          </el-button>
        </div>
        <el-alert v-if="r.warn" :title="r.warn" type="warning" :closable="false" show-icon style="margin:8px 0" />
        <el-alert v-if="r.dup_warning" :title="r.dup_warning" type="warning" :closable="false"
                  show-icon style="margin:8px 0" />
        <el-table v-if="r.preview && r.preview.length" :data="r.preview" size="small" max-height="320"
                  style="margin-top:6px">
          <el-table-column v-for="c in r.columns" :key="c" :prop="c" :label="c" min-width="120" show-overflow-tooltip />
        </el-table>
      </div>
    </el-card>

    <el-card class="page-card">
      <template #header><div class="card-header"><span class="title">同步历史</span></div></template>
      <el-table :data="history" size="small" max-height="360">
        <el-table-column prop="batch_id" label="批次" min-width="100" />
        <el-table-column prop="metric" label="模块" min-width="120" />
        <el-table-column prop="filename" label="文件" min-width="180" show-overflow-tooltip />
        <el-table-column prop="rows" label="行数" min-width="80" />
        <el-table-column prop="status" label="状态" min-width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'confirmed' ? 'success' : 'info'" size="small">
              {{ row.status === 'confirmed' ? '已入库' : '已预览' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="上传时间" min-width="150" />
        <el-table-column prop="confirmed_at" label="入库时间" min-width="150" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Upload, UploadFilled, MagicStick } from '@element-plus/icons-vue'
import api from '../api'

const modules = {
  completion_rate: {
    name: '竣工率指标', target: 'completion_records（通报分析，按月替换）',
    columns: ['激励明细：装维人员/宽带收单/宽带回单/FTTR收单/FTTR回单/激励后金额/区县'],
    note: '白名单解析「激励明细」sheet，人员粒度按月替换；月份上传时指定（通报表内无时间列）；' +
          '竣工率页「工单口径」卡片在当月无系统工单时自动按装机宽表统计',
  },
  extension: {
    name: '延伸服务指标', target: 'extension_orders + extension_members',
    columns: ['工单编号', '区县', '施工人', '受理时间', '业务项目', '收入'],
    note: '只入库「当月明细」；month 从受理时间推导，禁止硬编码',
  },
  // v1.7.1 已下线「H5 投诉工单」上传入口：该表数据停在 2026-10-22，
  // 投诉 H5 应发口径早已改读「投诉宽表」(complaint_orders)，见 h5.py::_derive_complaint。
  // 表与历史数据仍在库里，只是不再有上传口，也不再参与计算。
  install_wide: {
    name: '装机宽表', target: 'install_orders',
    columns: ['数据1：定单编码/区县/受理时间/定单状态/回单人员/24小时及时/8小时及时/是否退单/一级退单原因'],
    note: '装机宽表月累计（维度全面，延迟约2小时）；解析「数据1」sheet，按月替换',
  },
  complaint_wide: {
    name: '投诉宽表', target: 'complaint_orders',
    columns: ['数据1：工单流水号/区县/投诉时间/智慧装维状态/投诉分类/是否维修完成/24小时是否及时'],
    note: '投诉宽表月累计（维度全面，延迟约2小时）；解析「数据1」sheet，按月替换',
  },
  install_query: {
    name: '装机综合查询', target: 'install_orders',
    columns: ['导出信息：定单编码/区域/受理时间/工单状态/施工人/是否FTTR/一级退单原因'],
    note: '实时数据（比宽表早约2小时）；解析「导出信息」sheet，按月替换',
  },
  complaint_query: {
    name: '投诉综合查询', target: 'complaint_orders',
    columns: ['家宽投诉综合工单列表：EOMS工单号/区域/工单状态/投诉时间/投诉分类'],
    note: '实时数据（比宽表早约2小时）；解析「家宽投诉综合工单列表」sheet，按月替换',
  },
  h5_survey: {
    name: 'H5满意度通报', target: 'h5_survey_districts + h5_survey_staff + h5_survey_records + h5_low_scores',
    columns: ['「装维H5满意度参与率」导出：装机/投诉参与率（人员）、装机/投诉回单、装机/投诉H5回复详单',
              '旧版「装维H5满意度（集团大音平台)」导出：H5数据统计（区县汇总）、装机/投诉H5回复详单、累计装机/投诉低分核实'],
    note: '集团大音平台装维H5满意度通报。「H5参与率通报」图1 人员明细必须使用「装维H5满意度参与率」导出' +
          '（含「装机参与率」「投诉参与率」sheet，人员粒度）；旧版导出仅含区县汇总（无人员明细、仅当月），' +
          '上传预览时会给出告警。新版导出由后端本地聚合出区县汇总（当日/当月）、人员参与明细、测评明细与低分核实，' +
          '全量替换保留最新快照；展示在「H5参与率通报」页面与首页「重要通报 → H5满意度」',
  },
  qc: {
    name: '人工质检明细', target: 'qc_records（质检通报分析）',
    columns: ['人工质检明细：区县/网格/定单编码/质检单完成时间/定单主题/定单类型/回单人工号/回单人姓名/质检人姓名/质检结果/不通过原因/不合格原因/质检考核/不结算金额',
              'FTTR（表头第 2 行）：同上', '企宽装机（表头第 2 行）：区县/网格/定单编码/质检时间/回单工号/回单人员/整体是否合格'],
    note: '三个明细 sheet 合一入库，按质检日期替换；「分析汇总」是透视表不入库——由明细重算，' +
          '与通报合计逐格核对；驱动「质检规范」页汇总卡片，并把不规范清单推送到装维端',
  },
  ai_visit: {
    name: 'AI装维即访', target: 'ai_visit_issues',
    columns: ['当月装机即访清单 / 当月投诉即访清单：责任人/不规范项/工单号/回单时间'],
    note: '解析「当月装机即访清单」「当月投诉即访清单」两个 sheet；只取「外呼成功」且勾选项答案为「不是」的记录，' +
          '按 工单×不规范项 于每月替换；展示在首页「AI装维即访-不规范」通报卡片',
  },
}

const metric = ref('completion_rate')
const preview = ref(null)
const history = ref([])
const uploading = ref(false)
const confirming = ref(false)

// 批量上传状态
const batchFileList = ref([])            // el-upload 文件列表（含 raw）
const batchRaw = ref({})                 // 文件名 -> File 原对象（手动重路由用）
const batchResults = ref([])
const batchLoading = ref(false)
const confirmingBatch = ref(false)
const batchStats = reactive({ total: 0, recognized: 0 })

function onBatchChange(file, fileList) {
  batchFileList.value = fileList
  if (file.raw) batchRaw.value[file.name] = file.raw
}
function onBatchRemove(file, fileList) {
  batchFileList.value = fileList
  delete batchRaw.value[file.name]
}

async function upload(k, file) {
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.raw)
    preview.value = await api.post(`/sync/upload/${k}`, fd, { timeout: 180000 })
    ElMessage.success(`解析成功 ${preview.value.total_rows} 行，请核对后确认入库`)
  } catch (e) { /* 拦截器已提示 */ }
  finally { uploading.value = false }
}

async function batchUpload() {
  if (!batchFileList.value.length) return
  batchLoading.value = true
  try {
    const fd = new FormData()
    for (const f of batchFileList.value) {
      if (f.raw) fd.append('files', f.raw)
    }
    const res = await api.post('/sync/upload-batch', fd, { timeout: 600000 })
    batchResults.value = res.results || []
    batchStats.total = res.total || 0
    batchStats.recognized = res.recognized || 0
    const ok = batchResults.value.filter(r => !r.error).length
    const bad = batchResults.value.length - ok
    ElMessage.success(`识别完成：成功 ${ok} 项${bad ? `，未识别/失败 ${bad} 项` : ''}（请逐项核对后确认入库）`)
  } catch (e) { /* 拦截器已提示 */ }
  finally { batchLoading.value = false }
}

// 未识别文件：手动指定指标后，按单文件接口重新解析
async function onManual(r, m) {
  const name = (r.files || [])[0]
  const raw = batchRaw.value[name]
  if (!raw) return
  try {
    const fd = new FormData()
    fd.append('file', raw)
    const res = await api.post(`/sync/upload/${m}`, fd, { timeout: 180000 })
    Object.assign(r, res, { metric: m, name: modules[m].name, files: [name], confirmed: false })
    ElMessage.success(`已按「${modules[m].name}」识别并解析`)
  } catch (e) { /* 拦截器已提示 */ }
}

async function confirm() {
  await ElMessageBox.confirm('确认将预览数据入库？入库后指标将自动重算。', '确认入库', { type: 'warning' })
  confirming.value = true
  try {
    const r = await api.post(`/sync/confirm/${preview.value.batch_id}`, null, { timeout: 180000 })
    ElMessage.success(r.msg)
    preview.value = null
    loadHistory()
  } catch (e) { /* handled */ }
  finally { confirming.value = false }
}

async function confirmBatch(r) {
  await ElMessageBox.confirm('确认将预览数据入库？入库后指标将自动重算。', '确认入库', { type: 'warning' })
  confirmingBatch.value = true
  try {
    const res = await api.post(`/sync/confirm/${r.batch_id}`, null, { timeout: 180000 })
    ElMessage.success(res.msg)
    r.confirmed = true
    loadHistory()
  } catch (e) { /* handled */ }
  finally { confirmingBatch.value = false }
}

async function loadHistory() {
  history.value = await api.get('/sync/history')
}

onMounted(loadHistory)
</script>

<style scoped>
.batch-group { border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; }
.batch-group.batch-err { border-color: var(--el-color-danger-light-5); background: var(--el-color-danger-light-9); }
.batch-group-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.batch-files { color: #606266; font-size: 13px; flex: 1; min-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.batch-rows { color: #909399; font-size: 13px; }
.batch-warn { color: var(--el-color-danger); font-size: 13px; }
</style>
