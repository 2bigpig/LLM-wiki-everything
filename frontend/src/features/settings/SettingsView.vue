<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, date, post, type Capabilities, type Usage } from '../../api'
import { invalidate, notify } from '../../state'
import Icon from '../../components/Icon.vue'
import ModelSettings from './ModelSettings.vue'
const settingsRevision = ref(0)
const capabilities = ref<Capabilities | null>(null), usage = ref<Usage | null>(null), error = ref('')
const token = ref(sessionStorage.getItem('wiki-access-token') || ''), busy = ref(false)
const percent = computed(() => usage.value ? Math.min(100, usage.value.used_tokens / usage.value.limit * 100) : 0)
async function load() {
  try { [capabilities.value, usage.value] = await Promise.all([api<Capabilities>('/capabilities'), api<Usage>('/usage')]); error.value = '' }
  catch (e) { error.value = (e as Error).message }
}
async function connect() {
  busy.value = true
  const previous = sessionStorage.getItem('wiki-access-token')
  sessionStorage.setItem('wiki-access-token', token.value)
  try { await post('/session'); await load(); settingsRevision.value++; invalidate(); notify('本地访问口令已验证。') }
  catch (e) { if (previous === null) sessionStorage.removeItem('wiki-access-token'); else sessionStorage.setItem('wiki-access-token', previous); notify((e as Error).message, true) }
  finally { busy.value = false }
}
async function disconnect() {
  try { await post('/session/logout') } catch { /* 已失效的会话也允许清除浏览器口令。 */ }
  sessionStorage.removeItem('wiki-access-token'); token.value = ''; settingsRevision.value++; invalidate(); await load()
}
onMounted(load)
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">个人配置</span><h1>设置与用量</h1><p>管理访问会话，查看服务状态与模型用量</p></div></div>
  <div v-if="error" class="error-panel"><p>{{ error }}</p><button class="button" @click="load">重新连接</button></div>
  <div class="settings-layout"><section class="paper settings-card"><div class="section-title"><h2>本地访问口令</h2><Icon name="settings" /></div><p class="subtle">如果部署时设置了访问口令，在这里验证。口令仅保留在当前浏览器会话中。</p><form @submit.prevent="connect"><label class="field">访问口令<input v-model="token" type="password" autocomplete="off" placeholder="未设置口令时留空" /></label><div class="button-row"><button class="button primary" :disabled="busy">{{ busy ? '验证中…' : '验证并连接' }}</button><button type="button" class="button" @click="disconnect">清除会话</button></div></form></section>
  <section class="paper settings-card"><div class="section-title"><h2>模型用量</h2><Icon name="tasks" /></div><template v-if="usage"><div class="usage-number">{{ usage.used_tokens.toLocaleString() }}<span>/ {{ usage.limit.toLocaleString() }} Tokens</span></div><div class="usage-bar"><i :style="{ width: percent + '%' }" /></div><p class="subtle">本月调用使用量{{ usage.has_estimates ? '包含未确认请求的保守预留' : '以模型服务返回的 usage 为准' }}。达到上限后保留资料和已有结果，等待你调整额度或下月重试。</p></template><p v-else class="subtle">连接服务后查看本月用量。</p></section>
  <section v-if="capabilities" class="paper settings-card wide"><div class="section-title"><h2>当前整理能力</h2><span class="subtle">配置状态 · 实际连接以任务结果为准</span></div><div class="capability-grid"><div v-for="item in [{ label: '分析与 Wiki 综合', enabled: capabilities.llm, note: capabilities.llm_model || '等待配置模型 API' }, { label: '图片文字识别', enabled: capabilities.ocr, note: 'PaddleOCR · 仅识别文字' }, { label: '音视频转写', enabled: capabilities.asr, note: 'faster-whisper · 保留时间位置' }, { label: '语义检索', enabled: capabilities.embeddings, note: capabilities.embeddings ? capabilities.embedding_model : '当前使用关键词检索' }]" :key="item.label" class="capability-item"><span class="service-dot" :class="{ ready: item.enabled }" /><div><strong>{{ item.label }}</strong><small>{{ item.note }}</small></div><span class="topic">{{ item.enabled ? '已配置' : '未启用' }}</span></div></div><div class="settings-note"><strong>模型连接可在下方管理</strong><p>厂商密钥加密保存在服务端，页面修改对新任务生效；部署参数仍由 config.yaml 管理。</p><p>{{ capabilities.support_note }}</p><p>单次上传上限 {{ capabilities.max_upload_mb }} MB · 音视频时长上限 {{ capabilities.max_media_minutes }} 分钟</p></div></section>
  <section v-if="usage?.calls.length" class="paper settings-card wide"><h2>最近的模型调用</h2><div class="usage-list"><div v-for="call in usage.calls.slice(0, 20)" :key="call.id"><span><strong>{{ call.model }}</strong><small>{{ call.stage }} · {{ date(call.created_at) }}</small></span><span>{{ call.charged_tokens.toLocaleString() }} Tokens<small>{{ call.status === 'succeeded' ? '返回成功' : '含预留或异常调用' }}</small></span></div></div></section></div>
  <ModelSettings :key="settingsRevision" @changed="load" />
</template>
