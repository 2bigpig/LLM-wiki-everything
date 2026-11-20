<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, date, locationLabel, post, type WikiPage } from '../../api'
import { invalidate, notify } from '../../state'
import Icon from '../../components/Icon.vue'
import MarkdownContent from '../../components/MarkdownContent.vue'

const route = useRoute(), page = ref<WikiPage | null>(null), error = ref(''), personal = ref('')
const editing = ref(false), busy = ref(false)
const previous = ref<{ version: number; generated: string; personal: string } | null>(null)
async function load() {
  try { page.value = await api<WikiPage>('/wiki/' + route.params.id); if (!editing.value) personal.value = page.value.personal; error.value = '' }
  catch (e) { error.value = (e as Error).message }
}
async function save() {
  if (!page.value) return
  busy.value = true
  try {
    await api('/wiki/' + page.value.id, { method: 'PUT', body: JSON.stringify({ version: page.value.version, personal: personal.value }) })
    editing.value = false; await load(); invalidate(); notify('个人笔记已保存，自动整理会保留你的内容。')
  } catch (e) { notify((e as Error).message, true) }
  finally { busy.value = false }
}
async function viewVersion(version: number) {
  try { previous.value = await api('/wiki/' + route.params.id + '/revisions/' + version) }
  catch (e) { notify((e as Error).message, true) }
}
async function restore() {
  if (!page.value || !previous.value) return
  if (!window.confirm(`恢复第 ${previous.value.version} 版？当前内容会保留在历史中。`)) return
  busy.value = true
  try {
    await post('/wiki/' + page.value.id + '/restore', { version: page.value.version, revision: previous.value.version })
    previous.value = null; editing.value = false; await load(); invalidate(); notify('已恢复，并创建新的历史版本。')
  } catch (e) { notify((e as Error).message, true) }
  finally { busy.value = false }
}
onMounted(load)
watch(() => route.params.id, () => { page.value = null; editing.value = false; previous.value = null; void load() })
</script>
<template>
  <RouterLink to="/wiki" class="back-link"><Icon name="back" :size="16" />全部 Wiki</RouterLink>
  <div v-if="error" class="error-panel"><p>{{ error }}</p><button class="button" @click="load">重试</button></div>
  <div v-else-if="!page" class="empty-state"><span class="spinner" /></div>
  <template v-else><div class="detail-heading"><div><span class="eyebrow">知识页面 · 持续更新</span><h1>{{ page.title }}</h1><p class="subtle">{{ page.source_ids.length }} 份证据片段 · 版本 {{ page.version }} · {{ date(page.updated_at) }} 更新</p></div><a :href="'/api/wiki/' + page.id + '/export'" class="button"><Icon name="download" :size="17" />导出 Markdown</a></div><div class="detail-layout"><section><article class="paper reading-paper"><div class="section-title"><span class="inference-label"><Icon name="wiki" :size="16" />AI 综合整理</span><span class="subtle">点击引用，可核对原文</span></div><MarkdownContent :content="page.generated" /></article><section class="paper reading-paper personal-section"><div class="section-title"><h2>个人笔记</h2><button v-if="!editing" class="button small" @click="editing = true"><Icon name="edit" :size="15" />编辑</button></div><p class="subtle">你的表达独立保留，自动更新不会覆盖这里的内容。</p><form v-if="editing" @submit.prevent="save"><textarea v-model="personal" class="markdown-editor" rows="10" aria-label="个人笔记 Markdown" maxlength="100000" /><div class="button-row"><button class="button primary" :disabled="busy">{{ busy ? '保存中…' : '保存笔记' }}</button><button type="button" class="button" @click="editing = false; personal = page.personal">取消</button></div></form><MarkdownContent v-else-if="page.personal" :content="page.personal" /><p v-else class="side-empty">写下自己的理解、补充或尚未解决的问题。</p></section></section><aside class="context-column"><section class="paper side-card"><div class="section-title"><h3>知识从哪里来</h3><Icon name="link" :size="18" /></div><RouterLink v-for="source in page.sources" :key="source.id" :to="'/records/' + source.record_id + '#source-' + source.id" class="source-mini"><strong>{{ source.record_title }}</strong><span>{{ locationLabel(source.locator) }}<em v-if="source.stale"> · 历史原件</em><em v-if="source.archived"> · 已归档</em></span></RouterLink></section><section class="paper side-card"><div class="section-title"><h3>页面更新历史</h3><Icon name="clock" :size="18" /></div><button v-for="revision in page.revisions" :key="revision.id" class="revision-item" @click="viewVersion(revision.version)"><span class="revision-dot" /><span><strong>v{{ revision.version }} · {{ revision.actor === 'ai' ? '自动整理' : revision.actor === 'restore' ? '恢复历史' : '个人编辑' }}</strong><small>{{ date(revision.created_at) }}</small></span><Icon name="arrow" :size="14" /></button></section></aside></div></template>
  <div v-if="previous" class="modal-overlay"><section class="revision-panel"><div class="section-title"><h2>查看历史 · 第 {{ previous.version }} 版</h2><button class="icon-button" aria-label="关闭历史版本" @click="previous = null"><Icon name="close" /></button></div><div class="revision-content"><MarkdownContent :content="previous.generated" /><h2>当时的个人笔记</h2><MarkdownContent :content="previous.personal || '尚无个人笔记。'" /></div><div class="dialog-footer"><span>恢复会创建新版本，保留现有历史。</span><button class="button primary" :disabled="busy || previous.version === page?.version" @click="restore">恢复此版本</button></div></section></div>
</template>
