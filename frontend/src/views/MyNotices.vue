<template>
  <el-card class="page-card">
    <el-table :data="rows" size="small" max-height="620">
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="category" label="分类" min-width="90" />
      <el-table-column prop="publish_time" label="发布时间" min-width="140" />
      <el-table-column prop="deadline" label="签收截止" min-width="110">
        <template #default="{ row }">
          <span v-if="row.deadline" :style="{ color: !row.signed && isOverdue(row.deadline) ? '#f56c6c' : '' }">{{ row.deadline }}</span>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="签收状态" min-width="110">
        <template #default="{ row }">
          <el-tag v-if="row.signed" type="success" size="small">已签收</el-tag>
          <el-tag v-else-if="row.deadline && isOverdue(row.deadline)" type="danger" size="small">逾期未签</el-tag>
          <el-tag v-else type="warning" size="small">未签收</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="view(row)">查看</el-button>
          <el-button v-if="!row.signed" size="small" type="success" @click="sign(row)">电子签收</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="viewDlg" :title="current?.title" width="560px">
      <p style="line-height:1.8; color:#606266">{{ current?.content }}</p>
      <template #footer>
        <el-button v-if="current && !current.signed" type="primary" @click="sign(current)">电子签收</el-button>
        <el-button @click="viewDlg = false">关闭</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const rows = ref([])
const viewDlg = ref(false)
const current = ref(null)

const isOverdue = d => new Date(d) < new Date()

async function load() {
  rows.value = await api.get('/notices/my/list')
}

function view(row) {
  current.value = row
  viewDlg.value = true
}

async function sign(row) {
  await api.post(`/notices/my/${row.id}/sign`)
  ElMessage.success('签收成功')
  viewDlg.value = false
  load()
}

onMounted(load)
</script>
