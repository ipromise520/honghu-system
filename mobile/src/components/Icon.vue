<template>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path v-for="(d, i) in paths" :key="i" :d="d" />
  </svg>
</template>

<script setup>
import { computed } from 'vue'

// 线性图标集（24×24，stroke），避免引入图标库拖大包体
const MAP = {
  box: ['M3 8.5 12 4l9 4.5v7L12 20l-9-4.5z', 'M3 8.5 12 13l9-4.5', 'M12 13v7'],
  wrench: ['M14.5 6.5a3.5 3.5 0 1 0 4 4L20 9l-4-4z', 'M13 11 4.5 19.5l1 1L14 12'],
  clock: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 7.5V12l3 2'],
  diamond: ['M6 4h12l3 5-9 11L3 9z', 'M3 9h18', 'M9 4l-3 5 6 11 6-11-3-5'],
  medal: ['M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z'],
  check: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M8.5 12.2l2.4 2.4 4.6-4.8'],
  alert: ['M10.3 4.3 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z', 'M12 9.5v4', 'M12 17h.01'],
  repeat: ['M4 10V8a3 3 0 0 1 3-3h10', 'M14 2.5 17 5.5 14 8.5', 'M20 14v2a3 3 0 0 1-3 3H7', 'M10 21.5 7 18.5 10 15.5'],
  phone: ['M15.5 21c-7 0-12.5-5.5-12.5-12.5A2.5 2.5 0 0 1 5.5 6h2l1.5 4-2 1.5a11 11 0 0 0 5.5 5.5l1.5-2 4 1.5v2a2.5 2.5 0 0 1-2.5 2.5z'],
  star: ['M12 3.5l2.6 5.4 5.9.8-4.3 4.2 1 5.9-5.2-2.8-5.2 2.8 1-5.9L3.5 9.7l5.9-.8z'],
  search: ['M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z', 'M16.2 16.2 21 21'],
  plug: ['M9 3v5', 'M15 3v5', 'M6 8h12v4a6 6 0 0 1-12 0z', 'M12 18v3'],
  building: ['M4 21V6.5L12 3l8 3.5V21', 'M9 21v-5h6v5', 'M8 10h1.5M14.5 10H16M8 13.5h1.5M14.5 13.5H16'],
  heart: ['M12 20s-7-4.4-7-9.2A4 4 0 0 1 12 8a4 4 0 0 1 7 2.8C19 15.6 12 20 12 20z'],
  tool: ['M4 20h16', 'M6.5 20V11l4-3 4 3v9', 'M13 9.5V5.5h3.5V9'],
  bell: ['M6 9a6 6 0 1 1 12 0c0 4 1.5 5.5 1.5 5.5H4.5S6 13 6 9z', 'M10 18.5a2 2 0 0 0 4 0'],
  book: ['M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5z', 'M4 20.5A2.5 2.5 0 0 1 6.5 18H19v3H6.5A2.5 2.5 0 0 1 4 20.5z'],
  back: ['M15 5l-7 7 7 7'],
  refresh: ['M20 12a8 8 0 1 1-2.6-5.9', 'M20 4.5V10h-5.5'],
  right: ['M9 5l7 7-7 7'],
  inbox: ['M3 12h4l1.5 3h7L17 12h4', 'M5 6h14l2 6v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6z'],
}

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
})

const paths = computed(() => MAP[props.name] || MAP.inbox)
</script>
