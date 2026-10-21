<template>
  <div>
    <el-card class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" @change="load" />
      </div>
      <div ref="compRef" style="height: 380px" />
    </el-card>
    <el-card class="page-card">
      <template #header><div class="card-header"><span class="title">区县综合得分排名</span></div></template>
      <el-table :data="ranking" size="small">
        <el-table-column prop="rank" label="#" width="50" />
        <el-table-column prop="district" label="区县" min-width="100" />
        <el-table-column prop="total_score" label="综合得分" min-width="90" sortable />
        <el-table-column label="竣工率" min-width="90"><template #default="{ row }">{{ row.completion_rate }}%</template></el-table-column>
        <el-table-column label="延伸完成占比" min-width="110"><template #default="{ row }">{{ row.ext_ratio }}%</template></el-table-column>
        <el-table-column label="H5 已收" min-width="100"><template #default="{ row }">{{ row.h5_done }}/{{ row.h5_sent }}</template></el-table-column>
        <el-table-column label="短板" min-width="150">
          <template #default="{ row }">
            <el-tag v-if="row.is_shortcoming" type="danger" size="small">{{ row.shortcomings.join('、') }}</el-tag>
            <el-tag v-else type="success" size="small">无</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import api from '../api'

const month = ref(new Date().toISOString().slice(0, 7))
const ranking = ref([])
const compRef = ref()
let chart = null

async function load() {
  const [districts, rank] = await Promise.all([
    api.get('/metrics/completion-rate/districts', { params: { month: month.value } }),
    api.get('/metrics/dashboard/district-ranking', { params: { month: month.value } }),
  ])
  ranking.value = rank
  await nextTick()
  if (!chart) chart = echarts.init(compRef.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['竣工率(%)', '延伸完成占比(%)'] },
    grid: { left: 50, right: 20, top: 50, bottom: 80 },
    xAxis: { type: 'category', data: districts.map(d => d.district), axisLabel: { rotate: 30 } },
    yAxis: { type: 'value', max: 100 },
    series: [
      { name: '竣工率(%)', type: 'bar', data: districts.map(d => d.rate), itemStyle: { color: '#1a6fdb' } },
      { name: '延伸完成占比(%)', type: 'bar', data: rank.map(r => Math.min(r.ext_ratio, 120)), itemStyle: { color: '#e6a23c' } },
    ],
  })
}

onMounted(load)
onBeforeUnmount(() => chart?.dispose())
</script>
