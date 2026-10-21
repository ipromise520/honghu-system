<template>
  <el-card class="page-card">
    <template #header><div class="card-header"><span class="title">短板区县（综合得分中存在 &lt;60 分项）</span></div></template>
    <el-table :data="rows" size="small">
      <el-table-column prop="rank" label="排名" width="70" />
      <el-table-column prop="district" label="区县" min-width="100" />
      <el-table-column prop="total_score" label="综合得分" min-width="90" sortable />
      <el-table-column label="短板项" min-width="140">
        <template #default="{ row }">
          <el-tag v-for="s in row.shortcomings" :key="s" type="danger" size="small" style="margin-right:6px">{{ s }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="明细" min-width="360">
        <template #default="{ row }">
          <span v-for="(v, k) in row.detail" :key="k" class="detail-item">
            {{ k }}：<b :style="{ color: v < 60 ? '#f56c6c' : '#67c23a' }">{{ v }}</b>
          </span>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'

const rows = ref([])

onMounted(async () => {
  rows.value = await api.get('/metrics/dashboard/shortcomings', {
    params: { month: new Date().toISOString().slice(0, 7) }
  })
})
</script>

<style scoped>
.detail-item { margin-right: 18px; font-size: 13px; color: #606266; }
</style>
