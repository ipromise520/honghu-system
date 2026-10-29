<template>
  <div class="sheet" v-if="!loading && doc">
    <div class="bar-top">
      <button class="icon-btn" @click="goBack"><Icon name="back" /></button>
      <h1>通知详情</h1>
      <span style="width:34px"></span>
    </div>

    <div class="body">
      <div class="doc">
        <h2>{{ doc.title }}</h2>
        <div class="meta">
          <span>分类：{{ doc.category }}</span>
          <span>发布：{{ doc.time }}</span>
        </div>
        <div class="content">{{ doc.content }}</div>
      </div>

      <div class="sign-bar">
        <div v-if="doc.signed" class="sign-done">
          ✓ 已于 {{ doc.signed_at || '刚刚' }} 完成电子签收
        </div>
        <button v-else-if="doc.require_sign" class="btn-primary" :disabled="signing" @click="doSign">
          {{ signing ? '签收中…' : '确认签收' }}
        </button>
        <div v-else class="sign-done">该通知无需签收</div>
      </div>
    </div>
  </div>

  <div class="sheet" v-else-if="loading">
    <div class="bar-top"><span style="width:34px"></span><h1>通知详情</h1><span style="width:34px"></span></div>
    <div class="skeleton"><div class="sk" v-for="n in 4" :key="n"></div></div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { mp } from '../api'
import Icon from '../components/Icon.vue'

const route = useRoute()
const router = useRouter()
const doc = ref(null)
const loading = ref(true)
const signing = ref(false)

async function load() {
  loading.value = true
  try {
    doc.value = await mp.notice(route.params.id)
  } catch (e) {
    if (e.message !== '登录已过期') alert(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}
async function doSign() {
  signing.value = true
  try {
    const d = await mp.sign(route.params.id)
    doc.value = { ...doc.value, signed: true, signed_at: d.signed_at || '刚刚' }
  } catch (e) {
    alert(e.message || '签收失败')
  } finally {
    signing.value = false
  }
}
function goBack() { router.back() }
onMounted(load)
</script>
