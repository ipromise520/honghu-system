<template>
  <div class="hh-state" :class="`hh-state--${state}`">
    <!-- 加载：骨架屏 -->
    <template v-if="state === 'loading'">
      <div v-for="n in rows" :key="n" class="hh-skel" :style="{ width: skelWidth(n) }"></div>
    </template>

    <!-- 空 -->
    <template v-else-if="state === 'empty'">
      <el-icon :size="40" class="hh-state__ic"><DocumentDelete /></el-icon>
      <p class="hh-state__msg">{{ message || '暂无数据' }}</p>
    </template>

    <!-- 错误 -->
    <template v-else-if="state === 'error'">
      <el-icon :size="40" class="hh-state__ic is-bad"><WarningFilled /></el-icon>
      <p class="hh-state__msg">{{ message || '加载失败' }}</p>
      <el-button v-if="retryable" size="small" type="primary" plain @click="$emit('retry')">重试</el-button>
    </template>
  </div>
</template>

<script setup>
import { DocumentDelete, WarningFilled } from '@element-plus/icons-vue'
defineProps({
  state: { type: String, default: 'loading' }, // loading | empty | error
  message: { type: String, default: '' },
  rows: { type: Number, default: 4 },
  retryable: { type: Boolean, default: true },
})
defineEmits(['retry'])
function skelWidth(n) {
  const w = [100, 92, 96, 88, 94, 90, 98, 86]
  return (w[(n - 1) % w.length]) + '%'
}
</script>

<style scoped>
.hh-state { padding: 28px 16px; text-align: center; }
.hh-skel {
  height: 14px; margin: 0 auto 12px; border-radius: 6px;
  background: linear-gradient(90deg, var(--hh-surface-2) 25%, var(--hh-border) 37%, var(--hh-surface-2) 63%);
  background-size: 400% 100%;
  animation: hhSkel 1.3s ease-in-out infinite;
}
@keyframes hhSkel { 0% { background-position: 100% 0 } 100% { background-position: 0 0 } }
.hh-state__ic { color: var(--hh-faint); }
.hh-state__ic.is-bad { color: var(--hh-bad); }
.hh-state__msg { color: var(--hh-muted); font-size: var(--fs-sm); margin: 10px 0 12px; }
@media (prefers-reduced-motion: reduce) { .hh-skel { animation: none; } }
</style>
