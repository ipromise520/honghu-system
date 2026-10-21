import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    // Element Plus 按需引入：组件模板自动注册、ElMessage 等 API 导入自动改写为按需路径
    AutoImport({
      resolvers: [ElementPlusResolver()],
      dts: false,
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: false,
    }),
  ],
  define: {
    // 注入构建时间，页面显示用于确认浏览器加载的版本（排查缓存）
    __BUILD_TIME__: JSON.stringify(new Date().toISOString().slice(5, 16).replace('T', ' '))
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true }
    }
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      output: {
        // 依赖分包：框架与图表各自独立缓存，发版只失效业务 chunk
        // 注意：不要按包名分包 element-plus（会破坏 tree-shaking 把整个库拖进来），
        // 按需引入后各组件已自动切成独立小 chunk
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia'],
          echarts: ['echarts'],
        }
      }
    }
  }
})
