import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const backendTarget = env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [
      vue(),
      Components({ dts: false, resolvers: [ElementPlusResolver({ directives: true })] }),
    ],
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
            if (id.includes('/vue/') || id.includes('vue-router') || id.includes('pinia') || id.includes('@vueuse')) {
              return 'vue-vendor'
            }
            if (id.includes('/marked/')) return 'markdown'
            // Element Plus is intentionally left to Rollup's graph-based splitting.
            // Forcing every component used by every lazy route into one shared chunk
            // made the login page download the whole UI library up front.
            return undefined
          },
        },
      },
    },
  }
})
