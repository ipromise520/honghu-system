<template>
  <el-card class="page-card">
    <div class="toolbar">
      <el-button type="primary" @click="createDlg = true">新建通知</el-button>
    </div>
    <el-table :data="rows" size="small" max-height="600">
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="category" label="分类" min-width="90" />
      <el-table-column prop="status" label="状态" min-width="90">
        <template #default="{ row }">
          <el-tag :type="{ draft: 'info', published: 'success', recalled: 'danger' }[row.status]" size="small">
            {{ { draft: '草稿', published: '已发布', recalled: '已撤回' }[row.status] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="signed_count" label="已签收" min-width="80" />
      <el-table-column prop="publish_time" label="发布时间" min-width="140" />
      <el-table-column prop="deadline" label="签收截止" min-width="110" />
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'draft'" size="small" type="success" @click="publish(row)">发布</el-button>
          <el-button v-if="row.status === 'published'" size="small" type="warning" plain @click="recall(row)">撤回</el-button>
          <el-button v-if="row.require_sign" size="small" link type="primary" @click="signStatus(row)">签收统计</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createDlg" title="新建通知" width="560px">
      <el-form label-width="80px">
        <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="分类">
          <el-select v-model="form.category" style="width:100%">
            <el-option label="综合" value="综合" /><el-option label="考核" value="考核" /><el-option label="盯控" value="盯控" />
          </el-select>
        </el-form-item>
        <el-form-item label="内容"><el-input v-model="form.content" type="textarea" :rows="4" /></el-form-item>
        <el-form-item label="需要签收"><el-switch v-model="form.require_sign" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDlg = false">取消</el-button>
        <el-button type="primary" @click="create">保存草稿</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="statDlg" title="签收统计" width="640px">
      <div v-if="stat">
        <el-row :gutter="16" style="margin-bottom:14px">
          <el-col :span="6"><el-statistic title="应签收" :value="stat.total" /></el-col>
          <el-col :span="6"><el-statistic title="已签收" :value="stat.signed" value-style="color:#67c23a" /></el-col>
          <el-col :span="6"><el-statistic title="未签收" :value="stat.unsigned" value-style="color:#e6a23c" /></el-col>
          <el-col :span="6"><el-statistic title="逾期未签" :value="stat.overdue" value-style="color:#f56c6c" /></el-col>
        </el-row>
        <el-alert v-if="stat.overdue > 0" type="error" :closable="false" show-icon
                  :title="`逾期未签收 ${stat.overdue} 人：${stat.overdue_names.join('、')}`" style="margin-bottom:10px" />
        <p style="color:#909399;font-size:13px">未签收：{{ stat.unsigned_names.join('、') || '无' }}</p>
      </div>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const rows = ref([])
const createDlg = ref(false)
const statDlg = ref(false)
const stat = ref(null)
const form = reactive({ title: '', content: '', category: '综合', require_sign: true })

async function load() {
  rows.value = await api.get('/notices')
}

async function create() {
  if (!form.title) return ElMessage.warning('请填写标题')
  await api.post('/notices', form)
  ElMessage.success('已保存草稿')
  createDlg.value = false
  load()
}

async function publish(row) {
  await api.post(`/notices/${row.id}/publish`)
  ElMessage.success('已发布')
  load()
}

async function recall(row) {
  await api.post(`/notices/${row.id}/recall`)
  ElMessage.success('已撤回')
  load()
}

async function signStatus(row) {
  stat.value = await api.get(`/notices/${row.id}/sign-status`)
  statDlg.value = true
}

onMounted(load)
</script>
