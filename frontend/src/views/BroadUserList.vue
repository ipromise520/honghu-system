<template>
  <el-card class="page-card">
    <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px"
      title="广义用户清单（6–11 月广义重复清单合并去重）"
      description="按工单编号唯一合并共 9094 条。已用 Web8011「责任田对应责任人」表与服务器现存 CID 智能网关（7-14 快照）做部分补齐，来源与未匹配原因逐行标注；指定的 CID智能网关20260801.csv 尚未接入，未匹配记录保留空值不臆造。" />

    <div class="stat-row">
      <div class="stat-box"><div class="stat-k">清单总条数</div><div class="stat-v">{{ fmt(stats.total) }}</div></div>
      <div class="stat-box"><div class="stat-k">覆盖月份</div><div class="stat-v">{{ stats.months?.length || 0 }} 个月</div></div>
      <div class="stat-box"><div class="stat-k">重复投诉工单</div><div class="stat-v">{{ fmt(stats.dup) }}</div></div>
      <div class="stat-box"><div class="stat-k">CID已匹配(7-14快照)</div><div class="stat-v">{{ fmt(stats.cid_matched) }}</div></div>
      <div class="stat-box"><div class="stat-k">仍待正式匹配</div><div class="stat-v warn">{{ fmt(stats.pending) }}</div></div>
      <div class="stat-box"><div class="stat-k">有联系电话</div><div class="stat-v">{{ fmt(stats.phone) }}</div></div>
    </div>

    <div class="toolbar">
      <el-select v-model="q.months" placeholder="全部月份" multiple collapse-tags
                 collapse-tags-tooltip clearable style="width:170px" @change="reload">
        <el-option v-for="m in months" :key="m" :label="m" :value="m" />
      </el-select>
      <el-select v-model="q.district" placeholder="全部区县" clearable :disabled="districtLocked"
                 style="width:130px" @change="reload">
        <el-option v-for="d in districts" :key="d.name" :label="d.name" :value="d.name" />
      </el-select>
      <el-select v-model="q.flag" placeholder="状态" clearable style="width:160px" @change="reload">
        <el-option label="全部状态" value="" />
        <el-option label="只看重复投诉" value="dup" />
        <el-option label="只看有联系电话" value="phone" />
        <el-option label="只看CID上报SN缺失" value="nosn" />
      </el-select>
      <el-input v-model="q.keyword" placeholder="工单号 / 上网账号 / 小区 / 责任人 / 区县"
                clearable style="width:280px" @keyup.enter="reload" />
      <el-button type="primary" @click="reload">查询</el-button>
      <span style="margin-left:auto;color:#909399;font-size:13px">
        共 {{ fmt(total) }} 条 / 全量 {{ fmt(stats.grand_total ?? stats.total) }} 条
      </span>
    </div>

    <el-table :data="rows" size="small" max-height="560" v-loading="loading" border>
      <el-table-column prop="序号" label="序号" width="66" />
      <el-table-column prop="来源月份" label="月份" width="70" />
      <el-table-column prop="工单编号" label="工单编号" min-width="195" show-overflow-tooltip />
      <el-table-column prop="工单上报时间" label="上报时间" width="108" />
      <el-table-column prop="归属区县" label="归属区县" width="92" />
      <el-table-column prop="用户上网账号(匹配键)" label="用户上网账号" min-width="140" show-overflow-tooltip />
      <el-table-column prop="用户联系电话" label="用户联系电话" width="122" />
      <el-table-column prop="CID上报SN" label="CID上报SN" width="120" />
      <el-table-column prop="归属小区" label="归属小区" min-width="140" show-overflow-tooltip />
      <el-table-column prop="责任人" label="责任人(备选)" min-width="160" show-overflow-tooltip />
      <el-table-column prop="最终回单网格" label="最终回单网格" min-width="120" show-overflow-tooltip />
      <el-table-column prop="是否重复投诉" label="重复投诉" width="88">
        <template #default="{ row }">
          <el-tag v-if="row['是否重复投诉'] === '是'" type="danger" size="small">是</el-tag>
          <span v-else>{{ row['是否重复投诉'] }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="匹配状态" label="匹配状态" min-width="180" show-overflow-tooltip />
      <el-table-column prop="未匹配原因" label="未匹配原因" min-width="300" show-overflow-tooltip />
    </el-table>

    <el-pagination style="margin-top:12px" layout="total, prev, pager, next, sizes"
                   :total="total" v-model:current-page="q.page" v-model:page-size="q.page_size"
                   :page-sizes="[20, 50, 100]" @change="load" />
  </el-card>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import api from '../api'
import { useUserStore } from '../store'

const store = useUserStore()
const months = ['6月', '7月', '8月', '9月', '10月', '11月']
const q = reactive({ page: 1, page_size: 20, months: [], district: '', flag: '', keyword: '' })
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const districts = ref([])
const stats = ref({ total: 0, dup: 0, phone: 0, pending: 0, months: [], grand_total: 0 })

// 区县管理员：默认选中本区县并锁定（服务端同样强制本区县，前端传参被忽略）
const districtLocked = computed(() => store.role === 'district_admin')

const fmt = n => (Number(n) || 0).toLocaleString('zh-CN')

// 列表与统计卡共用同一套筛选条件，避免口径不一致
function queryParams() {
  return {
    months: q.months.join(','), district: q.district, keyword: q.keyword,
    dup: q.flag === 'dup' ? 1 : 0,
    has_phone: q.flag === 'phone' ? 1 : 0,
    nosn: q.flag === 'nosn' ? 1 : 0,
  }
}

async function loadDistricts() {
  try {
    districts.value = await api.get('/districts')
    if (store.role === 'district_admin' && store.user?.district) {
      q.district = store.user.district
    }
  } catch (e) { /* 拦截器已提示 */ }
}

async function loadStats() {
  try { stats.value = await api.get('/broad-user-list/stats', { params: queryParams() }) }
  catch (e) { /* 拦截器已提示 */ }
}

async function load() {
  loading.value = true
  try {
    const res = await api.get('/broad-user-list', {
      params: { page: q.page, size: q.page_size, ...queryParams() }
    })
    rows.value = res.items || []
    total.value = res.total || 0
  } catch (e) { /* 拦截器已提示 */ }
  finally { loading.value = false }
}

function reload() { q.page = 1; load(); loadStats() }

onMounted(async () => {
  await loadDistricts()
  loadStats()
  load()
})
</script>

<style scoped>
.stat-row { display: flex; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.stat-box { flex: 1; min-width: 150px; background: #fff; border: 1px solid #e4e7ed;
            border-radius: 6px; padding: 12px 16px; }
.stat-k { font-size: 12px; color: #909399; }
.stat-v { font-size: 22px; font-weight: 700; color: #2f5597; margin-top: 6px; }
.stat-v.warn { color: #e00000; }
</style>
