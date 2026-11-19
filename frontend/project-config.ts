// 仅供 Node/Vite 使用，不导入浏览器入口，不读取含密钥的 .env。
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse } from 'yaml'

export function loadProjectConfig() {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const filename = process.env.WIKI_CONFIG_FILE || resolve(root, 'config.yaml')
  let config: unknown
  try { config = parse(readFileSync(filename, 'utf8'), { uniqueKeys: true }) }
  catch { throw new Error('无法读取 config.yaml，请检查文件和 YAML 格式。') }
  const services = (config as { services?: Record<string, unknown> })?.services
  if (!services || typeof services !== 'object') throw new Error('config.yaml 配置错误：services')
  const serviceKeys = ['api_host', 'api_port', 'frontend_host', 'frontend_port', 'extra_cors_origins',
    'model_bind_host', 'ocr_port', 'asr_port', 'embedding_port', 'container_host', 'model_http_port']
  for (const key of Object.keys(services)) {
    if (!serviceKeys.includes(key)) throw new Error(`config.yaml 配置错误：services.${key}`)
  }
  for (const key of ['api_port', 'frontend_port']) {
    const value = services[key]
    if (!Number.isInteger(value) || Number(value) < 1 || Number(value) > 65535)
      throw new Error(`config.yaml 配置错误：services.${key}`)
  }
  for (const key of ['api_host', 'frontend_host']) {
    if (typeof services[key] !== 'string' || !services[key])
      throw new Error(`config.yaml 配置错误：services.${key}`)
  }
  const apiHost = String(services.api_host)
  const connectHost = apiHost === '0.0.0.0' ? '127.0.0.1' : apiHost === '::' ? '::1' : apiHost
  return {
    host: String(services.frontend_host), port: Number(services.frontend_port),
    apiUrl: `http://${connectHost.includes(':') ? `[${connectHost}]` : connectHost}:${services.api_port}`,
  }
}
