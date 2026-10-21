<template>
  <el-card class="page-card">
    <div class="toolbar">
      <el-radio-group v-model="status" @change="load">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button v-for="(n, k) in STATUS_NAME" :key="k" :value="k">{{ n }}</el-radio-button>
      </el-radio-group>
      <el-button type="primary" @click="openCreate">新建任务</el-button>
    </div>

    <el-table :data="rows" size="small" max-height="600">
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
      <el-table-column prop="district" label="区县" min-width="90" />
      <el-table-column prop="assignee" label="执行人" min-width="90">
        <template #default="{ row }">
          <el-button v-if="row.status === 'created'" size="small" type="warning" @click="openAssign(row)">派单</el-button>
          <span v-else>{{ row.assignee }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="status_name" label="状态" min-width="90">
        <template #default="{ row }">
          <el-tag :type="tagType(row.status)" size="small">{{ row.status_name }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="priority" label="优先级" min-width="80">
        <template #default="{ row }">
          <el-tag :type="{ urgent: 'danger', high: 'warning', normal: 'info', low: 'info' }[row.priority]" size="small">
            {{ { urgent: '紧急', high: '高', normal: '普通', low: '低' }[row.priority] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="deadline" label="截止" min-width="100" />
      <el-table-column prop="created_at" label="创建时间" min-width="140" />
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="detail(row)">详情</el-button>
          <el-button v-if="row.status === 'submitted'" size="small" link type="success" @click="openAcceptance(row)">验收</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createDlg" title="新建任务" width="560px">
      <el-form label-width="80px">
        <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="内容"><el-input v-model="form.content" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="区县">
          <el-select v-model="form.district_id" clearable style="width:100%" @change="loadPersonnel">
            <el-option v-for="d in districts" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="执行人">
          <el-select v-model="form.assignee_id" clearable filterable style="width:100%">
            <el-option v-for="p in personnel" :key="p.id" :label="`${p.real_name}（${p.district}）`" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority" style="width:100%">
            <el-option label="低" value="low" /><el-option label="普通" value="normal" />
            <el-option label="高" value="high" /><el-option label="紧急" value="urgent" />
          </el-select>
        </el-form-item>
        <el-form-item label="截止日期"><el-date-picker v-model="form.deadline" value-format="YYYY-MM-DD" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDlg = false">取消</el-button>
        <el-button type="primary" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="assignDlg" title="派单" width="420px">
      <el-select v-model="assignTo" filterable style="width:100%">
        <el-option v-for="p in personnel" :key="p.id" :label="`${p.real_name}（${p.district}）`" :value="p.id" />
      </el-select>
      <template #footer>
        <el-button @click="assignDlg = false">取消</el-button>
        <el-button type="primary" @click="doAssign">确认派单</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="accDlg" title="验收" width="420px">
      <el-input v-model="accComment" type="textarea" :rows="2" placeholder="验收意见（驳回时必填建议）" />
      <template #footer>
        <el-button type="danger" @click="doAcceptance('reject')">驳 回</el-button>
        <el-button type="primary" @click="doAcceptance('pass')">通过</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dtlDlg" :title="dtl?.title" width="640px">
      <div v-if="dtl">
        <p>{{ dtl.content }}</p>
        <el-descriptions :column="3" size="small" border style="margin-bottom:12px">
          <el-descriptions-item label="状态">{{ dtl.status_name }}</el-descriptions-item>
          <el-descriptions-item label="执行人">{{ dtl.assignee || '未派单' }}</el-descriptions-item>
          <el-descriptions-item label="截止">{{ dtl.deadline || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-timeline>
          <el-timeline-item v-for="(l, i) in dtl.logs" :key="i" :timestamp="`${l.time} · ${l.user}`">
            <b>{{ l.action }}</b> <span v-if="l.comment">{{ l.comment }}</span>
          </el-timeline-item>
        </el-timeline>
      </div>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const STATUS_NAME = { created: '待派单', assigned: '已派单', accepted: '进行中', submitted: '待验收', completed: '已验收', rejected: '已驳回' }
const rows = ref([])
const status = ref('')
const districts = ref([])
const personnel = ref([])
const createDlg = ref(false)
const assignDlg = ref(false)
const accDlg = ref(false)
const dtlDlg = ref(false)
const form = reactive({ title: '', content: '', district_id: null, assignee_id: null, priority: 'normal', deadline: '' })
const current = ref(null)
const assignTo = ref(null)
const accComment = ref('')
const dtl = ref(null)

const tagType = s => ({ created: 'info', assigned: 'warning', accepted: 'primary', submitted: 'warning', completed: 'success', rejected: 'danger' }[s] || 'info')

async function load() {
  rows.value = await api.get('/tasks', { params: { status: status.value } })
}

async function loadPersonnel() {
  const params = form.district_id ? { district_id: form.district_id } : {}
  const all = await api.get('/personnel/list', { params })
  personnel.value = all.filter(p => p.position && p.position !== '区县经理')
}

function openCreate() {
  Object.assign(form, { title: '', content: '', district_id: null, assignee_id: null, priority: 'normal', deadline: '' })
  personnel.value = []
  createDlg.value = true
  loadPersonnel()
}

async function create() {
  if (!form.title) return ElMessage.warning('请填写标题')
  await api.post('/tasks', form)
  ElMessage.success('创建成功')
  createDlg.value = false
  load()
}

function openAssign(row) {
  current.value = row
  assignTo.value = null
  loadPersonnel()
  assignDlg.value = true
}

async function doAssign() {
  if (!assignTo.value) return ElMessage.warning('请选择执行人')
  await api.post(`/tasks/${current.value.id}/assign`, { assignee_id: assignTo.value })
  ElMessage.success('派单成功')
  assignDlg.value = false
  load()
}

function openAcceptance(row) {
  current.value = row
  accComment.value = ''
  accDlg.value = true
}

async function doAcceptance(result) {
  if (result === 'reject' && !accComment.value) return ElMessage.warning('驳回请填写意见')
  await api.post(`/tasks/${current.value.id}/acceptance`, { result, comment: accComment.value })
  ElMessage.success(result === 'pass' ? '验收通过' : '已驳回')
  accDlg.value = false
  load()
}

async function detail(row) {
  dtl.value = await api.get(`/tasks/${row.id}`)
  dtlDlg.value = true
}

onMounted(() => {
  load()
  api.get('/districts').then(d => districts.value = d)
})
</script>
