<template>
  <el-card class="page-card">
    <div class="toolbar">
      <el-select v-model="q.month" placeholder="月份" clearable style="width:140px" @change="load">
        <el-option v-for="m in months" :key="m" :label="m" :value="m" />
      </el-select>
      <el-select v-model="q.status" placeholder="状态" clearable style="width:140px" @change="load">
        <el-option v-for="s in statusOptions" :key="s" :label="s" :value="s" />
      </el-select>
      <el-select v-model="q.broadband_type" placeholder="宽带类型" clearable style="width:120px" @change="load">
        <el-option label="个人" value="个人" />
        <el-option label="政企" value="政企" />
      </el-select>
      <el-input v-model="q.keyword" placeholder="工单号/人员/主题" clearable style="width:200px" @keyup.enter="load" />
      <el-button type="primary" @click="load">查询</el-button>
      <el-button v-if="isAdmin" type="success" @click="dlg = true">新增工单</el-button>
      <span style="margin-left:auto;color:#909399;font-size:13px">
        已施工 {{ stats.completed }} / 总 {{ stats.total }} 单
      </span>
    </div>

    <el-table :data="rows" size="small" max-height="580">
      <el-table-column prop="order_id" label="工单编码" min-width="200" show-overflow-tooltip />
      <el-table-column prop="order_theme" label="主题" min-width="100" />
      <el-table-column prop="status" label="状态" min-width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === '已施工' ? 'success' : 'warning'" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="district" label="区县" min-width="90" />
      <el-table-column prop="construction_dept" label="处理人" min-width="90" />
      <el-table-column prop="broadband_type" label="宽带类型" min-width="90" />
      <el-table-column prop="accept_time" label="受理时间" min-width="150" />
      <el-table-column v-if="isAdmin" label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="edit(row)">编辑</el-button>
          <el-button size="small" link type="danger" @click="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination style="margin-top:12px" layout="total, prev, pager, next, sizes" :total="total"
                   v-model:current-page="q.page" v-model:page-size="q.page_size"
                   :page-sizes="[20, 50, 100]" @change="load" />

    <el-dialog v-model="dlg" :title="form.order_id && !form._new ? '编辑工单' : '新增工单'" width="520px">
      <el-form label-width="100px">
        <el-form-item label="工单编码">
          <el-input v-model="form.order_id" :disabled="!form._new" placeholder="27开头定单编码" />
        </el-form-item>
        <el-form-item label="主题"><el-input v-model="form.order_theme" /></el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width:100%">
            <el-option v-for="s in statusOptions" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="区县">
          <el-select v-model="form.district_id" style="width:100%" clearable>
            <el-option v-for="d in districtList" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="处理人"><el-input v-model="form.construction_dept" /></el-form-item>
        <el-form-item label="宽带类型">
          <el-radio-group v-model="form.broadband_type">
            <el-radio value="个人">个人</el-radio>
            <el-radio value="政企">政企</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="受理时间"><el-input v-model="form.accept_time" placeholder="YYYY-MM-DD HH:MM:SS" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useUserStore } from '../store'

const store = useUserStore()
const isAdmin = computed(() => store.isAdmin)
const rows = ref([])
const total = ref(0)
const stats = ref({ total: 0, completed: 0 })
const districtList = ref([])
const dlg = ref(false)
const form = reactive({ order_id: '', order_theme: '', status: '', district_id: null, construction_dept: '', broadband_type: '个人', accept_time: '', _new: true })

const months = ['2026-10', '2026-09', '2026-08']
const statusOptions = ['已施工', '处理中', '流程未启动', '待施工', '待预约', '待智能预约', '待地址确认', '驳回中']
const q = reactive({ month: '', status: '', broadband_type: '', keyword: '', page: 1, page_size: 20 })

async function load() {
  const [data, st] = await Promise.all([
    api.get('/work-orders', { params: q }),
    api.get('/work-orders/stats', { params: { month: q.month } }),
  ])
  rows.value = data.items
  total.value = data.total
  stats.value = st
}

function edit(row) {
  Object.assign(form, row, { _new: false, district_id: null })
  const d = districtList.value.find(d => d.name === row.district)
  if (d) form.district_id = d.id
  dlg.value = true
}

async function save() {
  if (form._new) {
    await api.post('/work-orders', form)
  } else {
    await api.put(`/work-orders/${form.order_id}`, form)
  }
  ElMessage.success('保存成功')
  dlg.value = false
  load()
}

async function del(row) {
  await ElMessageBox.confirm(`确认删除工单 ${row.order_id}？`, '提示', { type: 'warning' })
  await api.delete(`/work-orders/${row.order_id}`)
  ElMessage.success('已删除')
  load()
}

onMounted(async () => {
  load()
  districtList.value = await api.get('/districts')
})
</script>
