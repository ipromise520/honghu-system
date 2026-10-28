<template>
  <div class="hh-tile" :class="`hh-tile--${tone}`">
    <div class="hh-tile__top">
      <span class="hh-tile__label">{{ label }}</span>
      <span v-if="icon" class="hh-tile__icon"><el-icon :size="15"><component :is="icon" /></el-icon></span>
    </div>
    <div class="hh-tile__value num">
      {{ value }}<span v-if="unit" class="hh-tile__unit">{{ unit }}</span>
    </div>
    <div v-if="trend" class="hh-tile__trend" :class="trendTone">
      <el-icon :size="13"><component :is="trendIcon" /></el-icon>{{ trend }}
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { CaretTop, CaretBottom } from '@element-plus/icons-vue'

const props = defineProps({
  label: { type: String, default: '' },
  value: { type: [String, Number], default: '' },
  unit: { type: String, default: '' },
  icon: { type: [String, Object], default: '' },
  tone: { type: String, default: 'default' }, // default | primary | ok | bad | warn
  trend: { type: String, default: '' },
  trendDir: { type: String, default: '' }, // up | down | ''
})

const trendIcon = computed(() => (props.trendDir === 'down' ? CaretBottom : CaretTop))
const trendTone = computed(() => (props.trendDir === 'down' ? 'is-down' : 'is-up'))
</script>

<style scoped>
.hh-tile {
  background: var(--hh-surface);
  border: 1px solid var(--hh-border);
  border-left: 3px solid var(--hh-border-strong);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  box-shadow: var(--shadow-1);
  transition: box-shadow var(--dur-base) var(--ease-out), transform var(--dur-base) var(--ease-out);
}
.hh-tile:hover { box-shadow: var(--shadow-2); transform: translateY(-2px); }
.hh-tile--primary { border-left-color: var(--hh-primary); }
.hh-tile--ok { border-left-color: var(--hh-ok); }
.hh-tile--bad { border-left-color: var(--hh-bad); }
.hh-tile--warn { border-left-color: var(--hh-warn); }
.hh-tile__top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.hh-tile__label { color: var(--hh-muted); font-size: var(--fs-sm); }
.hh-tile__icon { color: var(--hh-faint); display: inline-flex; }
.hh-tile__value { font-size: 26px; font-weight: 700; color: var(--hh-text); margin-top: 6px; line-height: var(--lh-tight); }
.hh-tile__unit { font-size: var(--fs-sm); color: var(--hh-muted); margin-left: 4px; font-weight: 400; }
.hh-tile__trend { display: inline-flex; align-items: center; gap: 2px; margin-top: 6px; font-size: var(--fs-xs); font-weight: 600; }
.hh-tile__trend.is-up { color: var(--hh-ok); }
.hh-tile__trend.is-down { color: var(--hh-bad); }
@media (max-width: 768px) {
  .hh-tile { padding: 12px; }
  .hh-tile__value { font-size: 20px; }
}
</style>
