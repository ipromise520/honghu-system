<template>
  <div class="login">
    <div class="login-card">
      <div class="login-brand">
        <span class="dot"></span>
        <div>
          <h1>装维人员端</h1>
        </div>
      </div>
      <p class="sub">洪湖支撑服务中心 · 工单与绩效随身看</p>

      <form @submit.prevent="submit">
        <div class="field">
          <label>手机号 / 账号</label>
          <input v-model.trim="username" type="text" inputmode="numeric" placeholder="请输入账号" autocomplete="username" />
        </div>
        <div class="field">
          <label>密码</label>
          <input v-model="password" type="password" placeholder="请输入密码" autocomplete="current-password" />
        </div>
        <p v-if="err" class="err">{{ err }}</p>
        <button class="btn-primary" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>

      <button class="ghost" type="button" @click="fillDemo">填入演示账号</button>
    </div>

    <p class="tip">
      演示账号 <code>15171201240</code> / <code>Aa123456</code><br />
      微信内打开请使用「不校验合法域名」调试模式
    </p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api'

const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const err = ref('')

function fillDemo() {
  username.value = '15171201240'
  password.value = 'Aa123456'
}

async function submit() {
  if (!username.value || !password.value) {
    err.value = '请填写账号和密码'
    return
  }
  err.value = ''
  loading.value = true
  try {
    await login(username.value, password.value)
    router.replace('/')
  } catch (e) {
    err.value = e.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>
