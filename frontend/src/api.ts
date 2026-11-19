/** 产品接口类型与统一请求处理；服务端模型密钥始终留在后端。 */
export interface RecordItem {
  id: string; kind: string; title: string; content: string; url: string | null
  notes: string; purpose: string; version: number; status: string; archived: boolean
  coverage: { status?: string; details?: string[]; source_count?: number; wiki_note?: string }
  created_at: string; updated_at: string; summary: string; topics: string[]
}
export interface Source {
  id: string; record_id: string; version: number; text: string; method: string
  record_title: string; stale: boolean; archived: boolean
  locator: { kind?: string; filename?: string; attachment_id?: string; page?: number
    paragraph?: number; start?: number; end?: number; url?: string; frame?: string }
}
export interface Attachment { id: string; filename: string; media_type: string; size: number }
export interface Analysis {
  id: string; version: number; kind: string; summary: string; topics: string[]
  points: { text: string; source_ids: string[] }[]; observations: string; model: string; created_at: string
}
export interface Run {
  id: string; record_id: string; record_title?: string; version: number; status: string
  stage: string; attempts: number; error: string | null; created_at: string; updated_at: string
}
export interface Relation {
  id: string; kind: string; explanation: string; feedback: string
  target_id: string; target_title: string; archived: boolean; stale: boolean
  evidence: { source_id: string; quote: string }[]
}
export interface WikiPage {
  id: string; title: string; generated: string; personal: string; version: number
  source_ids: string[]; updated_at: string; sources?: Source[]
  revisions?: { id: string; version: number; actor: string; change_note: string; created_at: string }[]
}
export interface RecordDetail extends RecordItem {
  sources: Source[]; analyses: Analysis[]; runs: Run[]; relations: Relation[]
  attachments: Attachment[]; pages: Pick<WikiPage, 'id' | 'title' | 'version' | 'updated_at'>[]
}
export interface Capabilities {
  llm: boolean; llm_model: string; ocr: boolean; asr: boolean; embeddings: boolean
  embedding_model: string; access_token_required: boolean; max_upload_mb: number
  max_media_minutes: number; monthly_token_limit: number; platforms: string[]; support_note: string
}
export interface Usage {
  used_tokens: number; limit: number; has_estimates: boolean
  calls: { id: string; stage: string; model: string; status: string; charged_tokens: number; created_at: string }[]
}
export interface SearchHit {
  id: string; record_id: string; record_title: string; record_kind: string
  text: string; locator: Source['locator']; score: number
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public code = '') { super(message) }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  const token = sessionStorage.getItem('wiki-access-token')
  if (token) headers.set('X-API-Key', token)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetch('/api' + path, { ...options, headers }) }
  catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('无法连接服务，请检查后端是否已启动。', 0)
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const message = typeof data.detail === 'string' ? data.detail
      : Array.isArray(data.detail) ? data.detail.map((d: { msg?: string }) => d.msg).join('；')
        : `请求失败（${response.status}）`
    throw new ApiError(message, response.status, data.code)
  }
  return response.json() as Promise<T>
}

export const post = <T>(path: string, body?: unknown) => api<T>(path, {
  method: 'POST', body: body === undefined ? undefined : JSON.stringify(body),
})

export const statusLabels: Record<string, string> = {
  queued: '等待整理', running: '正在整理', completed: '整理完成', failed: '处理失败',
  waiting_configuration: '等待配置', budget_exceeded: '额度不足', cancelled: '已停止',
}
export const stageLabels: Record<string, string> = {
  queued: '排队', extract: '读取内容', analyze: '分析总结', relate: '发现联系', wiki: '维护 Wiki', completed: '完成',
}
export const relationLabels: Record<string, string> = {
  same_topic: '同一主题', complements: '相互补充', extends: '知识延伸', contradicts: '不同观点',
  experience: '经验对应', duplicate: '内容重复',
}
export const kindLabels: Record<string, string> = { text: '随手记', link: '链接', file: '附件', mixed: '混合内容' }
export const date = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
}).format(new Date(value))
export function locationLabel(locator: Source['locator']): string {
  if (locator.start !== undefined) return `${Math.floor(locator.start / 60)}:${String(Math.floor(locator.start % 60)).padStart(2, '0')}`
  if (locator.page) return `第 ${locator.page} 页`
  if (locator.paragraph) return `第 ${locator.paragraph} 段`
  return locator.filename || (locator.kind === 'personal_note' ? '个人备注' : '原始内容')
}
