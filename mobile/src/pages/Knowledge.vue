<template>
  <div class="sheet">
    <div class="bar-top">
      <button class="icon-btn" @click="goBack"><Icon name="back" /></button>
      <h1>知识库</h1>
      <span style="width:34px"></span>
    </div>

    <div class="body">
      <div class="search-wrap">
        <input
          v-model.trim="kw"
          class="kb-search"
          type="search"
          placeholder="搜索装维规范、流程、口径…"
          @input="onInput"
        />
        <span class="search-ico"><Icon name="search" :size="18" /></span>
      </div>

      <p class="note" v-if="loading">检索中…</p>

      <div v-else-if="rows.length === 0" class="empty">
        <div class="ico"><Icon name="book" :size="54" /></div>
        <h3>知识库待接入</h3>
        <p v-if="kw">未检索到「{{ kw }}」相关内容。</p>
        <p v-else>知识条目正在整理入库，<br />上线后即可在此检索查阅。</p>
      </div>

      <div v-else>
        <div v-for="(r, i) in rows" :key="i" class="row" :style="{ '--i': i }">
          <div class="row-h"><span class="row-no" style="font-family:inherit;font-size:14px">{{ r.title }}</span></div>
          <div class="row-kv"><span style="color:var(--ink-2)">{{ r.summary || r.content }}</span></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { mp } from '../api'
import Icon from '../components/Icon.vue'

const router = useRouter()
const kw = ref('')
const rows = ref([])
const loading = ref(false)
let timer = null

function onInput() {
  clearTimeout(timer)
  timer = setTimeout(search, 300)
}
async function search() {
  loading.value = true
  try {
    const d = await mp.knowledge(kw.value)
    rows.value = d.rows || []
  } catch (e) {
    if (e.message !== '登录已过期') alert(e.message || '检索失败')
  } finally {
    loading.value = false
  }
}
function goBack() { router.back() }
onMounted(search)
</script>

<style scoped>
.search-wrap { position: relative; }
.kb-search {
  width: 100%; height: 44px; border-radius: 12px;
  border: 1px solid var(--line); padding: 0 14px 0 40px;
  font: inherit; font-size: 14px; color: var(--ink);
  background: rgba(6, 14, 28, .7); outline: none;
  transition: border-color .18s, box-shadow .18s, background .18s;
}
.kb-search::placeholder { color: var(--ink-3); }
.kb-search:focus {
  border-color: rgba(56, 189, 248, .7);
  background: rgba(7, 18, 36, .92);
  box-shadow: 0 0 0 3px rgba(56, 189, 248, .12);
}
.search-ico { position: absolute; left: 13px; top: 13px; color: var(--ink-3); pointer-events: none; }
</style>
