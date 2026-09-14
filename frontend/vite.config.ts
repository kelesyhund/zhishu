import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const backendTarget = env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [vue()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/api': backendTarget,
        '/media': backendTarget,
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return undefined
            if (id.includes('element-plus') || id.includes('@element-plus')) return 'element-plus'
            if (id.includes('/vue/') || id.includes('vue-router') || id.includes('pinia') || id.includes('@vueuse')) {
              return 'vue-vendor'
            }
            if (id.includes('/marked/')) return 'markdown'
            return 'vendor'
          },
        },
      },
    },
  }
})
