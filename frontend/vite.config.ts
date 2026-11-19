import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 开发时与生产环境统一使用 /api，避免在浏览器保存服务端地址和密钥。
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
