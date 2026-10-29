import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 移动端（装维人员端）构建产物由后端 app/main.py 挂到 /m
export default defineConfig({
  base: '/m/',
  plugins: [vue()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'assets',
    chunkSizeWarningLimit: 900,
  },
})
