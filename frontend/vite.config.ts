import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { loadProjectConfig } from './project-config'

const config = loadProjectConfig()

export default defineConfig({
  plugins: [vue()],
  // 禁用 Vite 自动加载 .env；浏览器构建不需要服务端密钥。
  envDir: false,
  preview: { host: config.host, port: config.port, strictPort: true,
    proxy: { '/api': config.apiUrl } },
  server: {
    host: config.host,
    port: config.port,
    strictPort: true,
    // 开发时与生产环境统一使用 /api，避免在浏览器保存服务端地址和密钥。
    proxy: { '/api': config.apiUrl },
  },
})
