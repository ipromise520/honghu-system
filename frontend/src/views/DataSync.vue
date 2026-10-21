<template>
  <div>
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
      <el-table :data="preview.preview" size="small" max-height="400">
        <el-table-column v-for="c in preview.columns" :key="c" :prop="c" :label="c" min-width="120" show-overflow-tooltip />
      </el-table>
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
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const modules = {
  completion_rate: {
    name: '竣工率指标', target: 'work_orders（批量 upsert 2000行/批）',
    columns: ['ORDER_ID', '区县', '网格', '定单状态', '受理时间', '宽带类型', '当前处理人或回单人'],
    note: '自动补全 status / accept_time / district_id / construction_dept',
  },
  extension: {
    name: '延伸服务指标', target: 'extension_orders + extension_members',
    columns: ['工单编号', '区县', '施工人', '受理时间', '业务项目', '收入'],
    note: '只入库「当月明细」；month 从受理时间推导，禁止硬编码',
  },
  h5_complaint: {
    name: 'H5 投诉工单', target: 'h5_complaint',
    columns: ['工单编号(HB-058前缀)', '工单状态', '施工人', '受理时间'],
    note: '家宽投诉综合工单列表',
  },
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
    name: 'H5满意度通报', target: 'h5_survey_districts + h5_survey_records + h5_low_scores',
    columns: ['H5数据统计：区县/回单量/H5测评数/满意度/Q24-Q29/参与率',
              '装机H5回复详单、投诉H5回复详单、累计装机/投诉低分核实'],
    note: '集团大音平台装维H5满意度通报；区县汇总/测评明细/低分核实全量替换，保留最新快照；展示在首页「重要通报 → H5满意度」',
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

async function upload(k, file) {
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.raw)
    preview.value = await api.post(`/sync/upload/${k}`, fd)
    ElMessage.success(`解析成功 ${preview.value.total_rows} 行，请核对后确认入库`)
  } catch (e) { /* 拦截器已提示 */ }
  finally { uploading.value = false }
}

async function confirm() {
  await ElMessageBox.confirm('确认将预览数据入库？入库后指标将自动重算。', '确认入库', { type: 'warning' })
  confirming.value = true
  try {
    const r = await api.post(`/sync/confirm/${preview.value.batch_id}`)
    ElMessage.success(r.msg)
    preview.value = null
    loadHistory()
  } catch (e) { /* handled */ }
  finally { confirming.value = false }
}

async function loadHistory() {
  history.value = await api.get('/sync/history')
}

onMounted(loadHistory)
</script>
