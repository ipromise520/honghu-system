<template>
  <div class="login-wrap">
    <div class="login-card">
      <h2>洪湖支撑服务中心</h2>
      <p class="sub">装维支撑管理平台 · Web8011</p>
      <el-form @keyup.enter="doLogin">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" size="large">
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" show-password placeholder="密码" size="large">
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-button type="primary" size="large" style="width:100%" :loading="loading" @click="doLogin">登 录</el-button>
      </el-form>
      <el-divider />
      <div class="tips">
        <div class="t">演示账号：</div>
        <div>市级管理员 admin / admin123</div>
        <div>区县管理员 d01admin / 123456</div>
        <div>装维人员 w01111 / 123456（见用户管理）</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '../store'

const router = useRouter()
const store = useUserStore()
const loading = ref(false)
const form = ref({ username: 'admin', password: 'admin123' })

async function doLogin() {
  if (!form.value.username || !form.value.password) return ElMessage.warning('请输入用户名和密码')
  loading.value = true
  try {
    await store.login(form.value.username, form.value.password)
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } catch (e) { /* 拦截器已提示 */ }
  finally { loading.value = false }
}
</script>

<style scoped>
.login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 16px;
  background: radial-gradient(120% 120% at 50% 0%, #1d3a63 0%, #16263a 55%, #0f1b2c 100%); }
.login-card { width: min(380px, 92vw); background: #fff; border-radius: var(--radius-lg); padding: clamp(24px, 6vw, 36px); box-shadow: var(--shadow-3); }
h2 { margin: 0 0 4px; text-align: center; color: #1d2b3a; }
.sub { text-align: center; color: #909399; font-size: 13px; margin-bottom: 24px; }
.tips { font-size: 12px; color: #909399; line-height: 1.9; }
.t { font-weight: 600; color: #606266; }
@media (max-width: 768px) {
  .login-card { border-radius: var(--radius-md); }
  h2 { font-size: 20px; }
}
</style>
