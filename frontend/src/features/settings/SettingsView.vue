<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, date, type Capabilities, type Usage } from '../../api'
import ModelSettings from './ModelSettings.vue'
import QuickModelSetup from './QuickModelSetup.vue'
import LLMSchedule from './LLMSchedule.vue'
import type { Overview } from './types'
const settingsRevision = ref(0), advanced = ref(false), overview = ref<Overview | null>(null)
const capabilities = ref<Capabilities | null>(null), usage = ref<Usage | null>(null), error = ref(''), loading = ref(true)
// 路由离开后忽略迟到响应，开发阶段直接加载配置，不设置临时登录状态。
let generation = 0
const percent = computed(() => usage.value ? Math.min(100, usage.value.used_tokens / usage.value.limit * 100) : 0)
async function load() {
  const request = ++generation
  try {
    const result = await api<Overview>('/model-settings')
    if (request !== generation) return
    overview.value = result; error.value = ''; loading.value = false
    const results = await Promise.allSettled([api<Capabilities>('/capabilities'), api<Usage>('/usage')])
    if (request !== generation) return
    if (results[0].status === 'fulfilled') capabilities.value = results[0].value
    if (results[1].status === 'fulfilled') usage.value = results[1].value
  } catch (e) { if (request === generation) error.value = (e as Error).message }
  finally { if (request === generation) loading.value = false }
}
function changed() { settingsRevision.value++; void load() }
onBeforeUnmount(() => { generation++ })
onMounted(load)
</script>
<template>
  <div class="page-heading settings-heading"><div><h1>设置与用量</h1><p>接入模型，让收藏开始自动整理</p></div></div>
  <div v-if="error" class="error-panel"><p>{{ error }}</p><button class="button" @click="load">重新连接</button></div>
  <p v-if="loading" class="subtle" role="status">正在读取配置…</p>
  <template v-if="overview">
    <QuickModelSetup :data="overview" @changed="changed" />
    <LLMSchedule />
    <div class="secondary-status">
      <section class="compact-usage"><h2>模型用量</h2><p v-if="usage">{{ usage.used_tokens.toLocaleString() }} / {{ usage.limit.toLocaleString() }} Tokens<span v-if="usage.has_estimates"> · 含保守预留</span></p><p v-else>暂未读取用量</p><div v-if="usage" class="usage-bar"><i :style="{ width: percent + '%' }" /></div></section>
      <section v-if="capabilities" class="compact-services"><h2>当前整理能力</h2><div><span v-for="item in [{ name: 'LLM', enabled: capabilities.llm }, { name: 'OCR · PaddleOCR', enabled: capabilities.ocr }, { name: 'ASR', enabled: capabilities.asr }, { name: '语义检索 · BGE-M3', enabled: capabilities.embeddings }]" :key="item.name"><i class="service-dot" :class="{ ready: item.enabled }" />{{ item.name }} · {{ item.enabled ? '已配置' : '未启用' }}</span></div><p>Embedding 与 OCR 为项目内置模型，无需配置；状态仅代表已配置</p></section>
    </div>
    <details class="advanced-settings" @toggle="advanced = ($event.target as HTMLDetailsElement).open"><summary>高级设置<span>LLM 连接、阶段用途与调用记录</span></summary><template v-if="advanced"><ModelSettings :key="settingsRevision" @changed="changed" /><section class="paper settings-card recent-calls"><h2>最近的模型调用</h2><div v-if="usage?.calls.length" class="usage-list"><div v-for="call in usage.calls.slice(0, 20)" :key="call.id"><span><strong>{{ call.model }}</strong><small>{{ call.stage }} · {{ date(call.created_at) }}</small></span><span>{{ call.charged_tokens.toLocaleString() }} Tokens<small>{{ call.status === 'succeeded' ? '返回成功' : '含预留或异常调用' }}</small></span></div></div><p v-else class="subtle">尚无模型调用</p><p v-if="capabilities" class="subtle">{{ capabilities.support_note }} · 单次上传上限 {{ capabilities.max_upload_mb }} MB · 音视频时长上限 {{ capabilities.max_media_minutes }} 分钟</p></section></template></details>
  </template>
</template>

<style scoped>
.settings-heading { margin-bottom:24px; }
.secondary-status { display:grid; grid-template-columns:1fr 1fr; gap:32px; padding:24px 0; }.secondary-status h2 { font-size:14px; margin:0 0 8px; }.secondary-status p { font-size:12px; color:var(--muted); margin:0; }.compact-usage .usage-bar { max-width:320px; margin-top:12px; height:3px; }.compact-services > div { display:flex; gap:8px 16px; flex-wrap:wrap; font-size:12px; color:var(--secondary); }.compact-services span { display:flex; gap:6px; align-items:center; }.compact-services p { margin-top:8px; }
.advanced-settings { border-top:1px solid var(--line); }.advanced-settings > summary { padding:20px 0; font-size:14px; color:var(--ink); }.advanced-settings > summary span { font-size:12px; color:var(--muted); margin-left:12px; }.recent-calls { margin-top:24px; }.recent-calls > .subtle { margin-top:16px; }
@media(max-width:600px) { .settings-heading { gap:12px; flex-wrap:wrap; margin-bottom:20px; }.secondary-status { grid-template-columns:1fr; gap:20px; padding:20px 0; }.advanced-settings > summary span { display:block; margin:8px 0 0 18px; } }
</style>
