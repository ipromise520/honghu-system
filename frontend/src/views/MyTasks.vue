<template>
  <el-card class="page-card">
    <div class="toolbar">
      <el-radio-group v-model="status" @change="load">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="assigned">待接单</el-radio-button>
        <el-radio-button value="accepted">进行中</el-radio-button>
        <el-radio-button value="submitted">待验收</el-radio-button>
        <el-radio-button value="completed">已验收</el-radio-button>
        <el-radio-button value="rejected">被驳回</el-radio-button>
      </el-radio-group>
    </div>
    <el-table :data="rows" size="small" max-height="600">
      <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
      <el-table-column prop="district" label="区县" min-width="90" />
      <el-table-column prop="status_name" label="状态" min-width="90">
        <template #default="{ row }">
          <el-tag :type="{ assigned: 'warning', accepted: 'primary', submitted: 'warning', completed: 'success', rejected: 'danger' }[row.status]" size="small">
            {{ row.status_name }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="priority" label="优先级" min-width="80" />
      <el-table-column prop="deadline" label="截止" min-width="100" />
      <el-table-column prop="creator" label="发布人" min-width="90" />
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'assigned' || row.status === 'rejected'" size="small" type="success" @click="accept(row)">接单</el-button>
          <el-button v-if="['assigned', 'accepted', 'rejected'].includes(row.status)" size="small" type="primary" @click="openSubmit(row)">提交验收</el-button>
          <el-button size="small" link type="info" @click="feedback(row)">进度反馈</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="submitDlg" title="提交验收" width="420px">
      <el-input v-model="comment" type="textarea" :rows="3" placeholder="完成情况说明" />
      <template #footer>
        <el-button @click="submitDlg = false">取消</el-button>
        <el-button type="primary" @click="submit">提交</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const rows = ref([])
const status = ref('')
const submitDlg = ref(false)
const current = ref(null)
const comment = ref('')

async function load() {
  rows.value = await api.get('/tasks/my/list', { params: { status: status.value } })
}

async function accept(row) {
  await api.post(`/tasks/my/${row.id}/accept`)
  ElMessage.success('接单成功')
  load()
}

function openSubmit(row) {
  current.value = row
  comment.value = ''
  submitDlg.value = true
}

async function submit() {
  await api.post(`/tasks/my/${current.value.id}/submit`, { comment: comment.value })
  ElMessage.success('已提交验收')
  submitDlg.value = false
  load()
}

async function feedback(row) {
  const { value } = await ElMessageBox.prompt('填写进度反馈', `反馈：${row.title}`, { inputType: 'textarea' })
  await api.post(`/tasks/my/${row.id}/feedbacks`, { comment: value })
  ElMessage.success('反馈已提交')
}

onMounted(load)
</script>
