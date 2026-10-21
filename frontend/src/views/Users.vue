<template>
  <el-card class="page-card">
    <div class="toolbar">
      <el-select v-model="q.role" placeholder="角色" clearable style="width:150px" @change="load">
        <el-option label="市级管理员" value="super_admin" />
        <el-option label="区县管理员" value="district_admin" />
        <el-option label="装维人员" value="field_worker" />
      </el-select>
      <el-select v-model="q.district_id" placeholder="区县" clearable style="width:150px" @change="load">
        <el-option v-for="d in districts" :key="d.id" :label="d.name" :value="d.id" />
      </el-select>
      <el-input v-model="q.keyword" placeholder="用户名/姓名" clearable style="width:180px" @keyup.enter="load" />
      <el-button type="primary" @click="load">查询</el-button>
      <el-button type="success" @click="openCreate">新增用户</el-button>
    </div>

    <el-table :data="rows" size="small" max-height="580">
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="username" label="用户名" min-width="100" />
      <el-table-column prop="real_name" label="姓名" min-width="90" />
      <el-table-column prop="role_name" label="角色" min-width="110">
        <template #default="{ row }">
          <el-tag :type="{ super_admin: 'danger', district_admin: 'warning', field_worker: 'info' }[row.role]" size="small">
            {{ row.role_name }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="district" label="区县" min-width="90" />
      <el-table-column prop="team" label="班组" min-width="120" />
      <el-table-column prop="grid" label="网格" min-width="100" />
      <el-table-column prop="position" label="岗位" min-width="80" />
      <el-table-column label="状态" min-width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" link type="warning" @click="resetPwd(row)">重置密码</el-button>
          <el-button v-if="row.is_active" size="small" link type="danger" @click="deactivate(row)">停用</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination style="margin-top:12px" layout="total, prev, pager, next" :total="total"
                   v-model:current-page="q.page" v-model:page-size="q.page_size" @change="load" />

    <el-dialog v-model="dlg" :title="form.id ? '编辑用户' : '新增用户'" width="520px">
      <el-form label-width="90px">
        <el-form-item label="用户名"><el-input v-model="form.username" :disabled="!!form.id" /></el-form-item>
        <el-form-item v-if="!form.id" label="初始密码"><el-input v-model="form.password" placeholder="默认 123456" /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="form.real_name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width:100%">
            <el-option label="市级管理员" value="super_admin" />
            <el-option label="区县管理员" value="district_admin" />
            <el-option label="装维人员" value="field_worker" />
          </el-select>
        </el-form-item>
        <el-form-item label="区县">
          <el-select v-model="form.district_id" clearable style="width:100%">
            <el-option v-for="d in districts" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="班组"><el-input v-model="form.team" /></el-form-item>
        <el-form-item label="网格"><el-input v-model="form.grid" /></el-form-item>
        <el-form-item label="岗位"><el-input v-model="form.position" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const rows = ref([])
const total = ref(0)
const districts = ref([])
const dlg = ref(false)
const q = reactive({ role: '', district_id: null, keyword: '', page: 1, page_size: 50 })
const form = reactive({})

async function load() {
  const data = await api.get('/users', { params: q })
  rows.value = data.items
  total.value = data.total
}

function openCreate() {
  Object.keys(form).forEach(k => delete form[k])
  Object.assign(form, { username: '', password: '123456', real_name: '', role: 'field_worker', district_id: null, team: '', grid: '', position: '' })
  dlg.value = true
}

function openEdit(row) {
  Object.keys(form).forEach(k => delete form[k])
  Object.assign(form, row, { role: row.role })
  dlg.value = true
}

async function save() {
  if (form.id) {
    await api.put(`/users/${form.id}`, form)
  } else {
    await api.post('/users', form)
  }
  ElMessage.success('保存成功')
  dlg.value = false
  load()
}

async function resetPwd(row) {
  const { value } = await ElMessageBox.prompt(`重置 ${row.real_name} 的密码（至少 8 位，含字母和数字）`, '重置密码', { inputValue: 'Honghu@2026' })
  await api.post('/auth/reset-password', { user_id: row.id, new_password: value })
  ElMessage.success('已重置')
}

async function deactivate(row) {
  await ElMessageBox.confirm(`确认停用用户 ${row.real_name}？`, '提示', { type: 'warning' })
  await api.delete(`/users/${row.id}`)
  ElMessage.success('已停用')
  load()
}

onMounted(() => {
  load()
  api.get('/districts').then(d => districts.value = d)
})
</script>
