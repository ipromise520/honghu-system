<template>
  <div>
    <el-tabs v-model="tab" @tab-change="load">
      <el-tab-pane label="待处理工单预警" name="workorder">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
                  title="生产环境经 POP3 实时拉取 139 邮箱预警邮件；本地版按库内工单超时规则（>48h 未处理）生成" />
        <el-table :data="workorders" size="small" max-height="560">
          <el-table-column prop="order_id" label="工单编码" min-width="200" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" min-width="100" />
          <el-table-column prop="handler" label="处理人" min-width="90" />
          <el-table-column prop="accept_time" label="受理时间" min-width="150" />
          <el-table-column label="滞留时长" min-width="110">
            <template #default="{ row }">
              <el-tag :type="row.level === 'danger' ? 'danger' : 'warning'" size="small">{{ row.hours }} 小时</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="source" label="来源" min-width="180" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="站内预警通知" name="inner">
        <el-table :data="inner" size="small" max-height="560">
          <el-table-column prop="level" label="级别" width="90">
            <template #default="{ row }">
              <el-tag :type="row.level === 'danger' ? 'danger' : 'warning'" size="small">
                {{ row.level === 'danger' ? '严重' : '警告' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="content" label="内容" min-width="420" />
          <el-table-column prop="created_at" label="时间" min-width="150" />
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button v-if="!row.is_resolved" size="small" type="primary" link @click="resolve(row)">标记处理</el-button>
              <el-tag v-else size="small" type="success">已处理</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'

const tab = ref('workorder')
const workorders = ref([])
const inner = ref([])

async function load() {
  if (tab.value === 'workorder') {
    workorders.value = await api.get('/alerts/pending-workorder')
  } else {
    inner.value = await api.get('/dashboard/alerts', { params: { is_resolved: false } })
  }
}

async function resolve(row) {
  await api.post(`/dashboard/alerts/${row.id}/resolve`)
  load()
}

onMounted(load)
</script>
