<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, date, kindLabels, locationLabel, post, relationLabels, stageLabels, type RecordDetail, type Source } from '../../api'
import { invalidate, notify, refreshVersion } from '../../state'
import Icon from '../../components/Icon.vue'
import StatusBadge from '../../components/StatusBadge.vue'
import MarkdownContent from '../../components/MarkdownContent.vue'

const route = useRoute(), router = useRouter()
const record = ref<RecordDetail | null>(null), error = ref(''), busy = ref(false), tab = ref('analysis')
const analysisId = ref(''), selectedAttachment = ref(''), editing = ref(false), highlighted = ref('')
const editForm = ref({ title: '', content: '', notes: '', purpose: 'auto' })
const media = ref<HTMLMediaElement | null>(null)
const inputStatus: Record<string, string> = { queued: '等待读取', succeeded: '已读取', failed: '读取失败', skipped: '未读取' }
const analysis = computed(() => record.value?.analyses.find(a => a.id === analysisId.value) || record.value?.analyses[0])
const currentRun = computed(() => record.value?.runs[0])
const attachment = computed(() => record.value?.attachments.find(a => a.id === selectedAttachment.value))
const visibleSources = computed(() => record.value?.sources.filter(s => s.version === record.value?.version || s.id === highlighted.value) || [])
const steps = ['extract', 'analyze', 'relate', 'wiki']
async function load() {
  try {
    record.value = await api<RecordDetail>('/records/' + route.params.id); error.value = ''
    if (!selectedAttachment.value) selectedAttachment.value = record.value.attachments[0]?.id || ''
    await openHash()
  } catch (e) { error.value = (e as Error).message }
}
async function openHash() {
  if (route.hash.startsWith('#source-')) {
    const sourceId = route.hash.substring(8)
    if (highlighted.value === sourceId) return
    highlighted.value = sourceId; tab.value = 'original'
    await nextTick(); document.getElementById('source-' + highlighted.value)?.scrollIntoView({ block: 'center' })
  }
}
async function jump(sourceId: string) {
  const source = record.value?.sources.find(s => s.id === sourceId) || await api<Source>('/sources/' + sourceId)
  if (source.record_id !== record.value?.id) { await router.push('/records/' + source.record_id + '#source-' + sourceId); return }
  highlighted.value = sourceId; tab.value = 'original'
  if (source.locator.attachment_id) selectedAttachment.value = source.locator.attachment_id
  await nextTick()
  if (source.locator.start !== undefined && media.value) media.value.currentTime = source.locator.start
  document.getElementById('source-' + sourceId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
async function action(name: string) {
  if (!record.value) return
  busy.value = true
  try { await post('/records/' + record.value.id + '/' + name); invalidate(); await load(); notify(name === 'archive' ? '归档状态已更新。' : '已提交，后台将继续处理。') }
  catch (e) { notify((e as Error).message, true) }
  finally { busy.value = false }
}
function beginEdit() {
  if (!record.value) return
  const { title, content, notes, purpose } = record.value
  editForm.value = { title, content, notes, purpose }; editing.value = true
}
async function saveEdit() {
  if (!record.value) return
  busy.value = true
  try {
    await api('/records/' + record.value.id, { method: 'PUT', body: JSON.stringify({ ...editForm.value, version: record.value.version }) })
    editing.value = false; invalidate(); await load(); notify('修改已保存，将整理新版本。')
  } catch (e) { notify((e as Error).message, true) }
  finally { busy.value = false }
}
async function feedback(id: string, value: string) {
  try { await api('/relations/' + id, { method: 'PATCH', body: JSON.stringify({ feedback: value }) }); await load() }
  catch (e) { notify((e as Error).message, true) }
}
async function retryInput(id: string) {
  if (!record.value) return
  busy.value = true
  try { await post(`/records/${record.value.id}/inputs/${id}/retry`); await load(); invalidate(); notify('已创建新的读取版本，只重试所选失败来源。') }
  catch (e) { notify((e as Error).message, true) }
  finally { busy.value = false }
}
async function insightFeedback(id: string, value: string) {
  try { await api('/insights/' + id, { method: 'PATCH', body: JSON.stringify({ feedback: value }) }); await load() }
  catch (e) { notify((e as Error).message, true) }
}
async function retryInsight(id: string) {
  try { await post('/insights/' + id + '/retry'); await load() }
  catch (e) { notify((e as Error).message, true) }
}
let timer: ReturnType<typeof setInterval>
onMounted(() => { void load(); timer = setInterval(() => { if (!document.hidden && !editing.value) void load() }, 5000) })
onUnmounted(() => clearInterval(timer))
watch(() => route.params.id, () => { record.value = null; analysisId.value = ''; selectedAttachment.value = ''; highlighted.value = ''; tab.value = 'analysis'; void load() })
watch(() => route.hash, openHash)
watch(refreshVersion, load)
</script>
<template>
  <RouterLink to="/" class="back-link"><Icon name="back" :size="16" />返回收藏</RouterLink>
  <div v-if="error" class="error-panel"><h2>无法打开这条记录</h2><p>{{ error }}</p><button class="button" @click="load">重试</button></div>
  <div v-else-if="!record" class="empty-state"><span class="spinner" /></div>
  <template v-else>
    <div class="detail-heading"><div><div class="record-meta"><span class="content-kind"><Icon :name="record.kind === 'link' ? 'link' : 'note'" :size="17" />{{ kindLabels[record.kind] }}</span><span>{{ date(record.created_at) }} · 原件 v{{ record.version }}</span><span v-if="record.archived" class="topic">已归档</span></div><h1>{{ record.title }}</h1><a v-if="record.url" :href="record.url" target="_blank" rel="noopener noreferrer" class="external-source">{{ record.url }}<Icon name="arrow" :size="14" /></a></div><button class="button" :disabled="record.archived" @click="beginEdit"><Icon name="edit" :size="16" />编辑记录</button></div>
    <section v-if="record.inputs?.length" class="paper source-inputs"><div class="section-title"><h2>本次收藏的来源</h2><span class="subtle">原件 v{{ record.version }}</span></div><div v-for="item in record.inputs" :key="item.id" class="source-input"><Icon :name="item.kind === 'url' ? 'link' : item.kind === 'file' ? 'file' : 'note'" :size="17" /><div><a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.label }}</a><strong v-else>{{ item.label }}</strong><small>{{ inputStatus[item.status] || item.status }}</small><p v-if="item.error" class="warning-text">{{ item.error }}</p><p v-for="detail in item.details" :key="detail" class="subtle">{{ detail }}</p></div><RouterLink v-if="item.origin !== 'notes'" :to="{ path: '/graph', query: { focus: 'resource:' + item.resource_id } }" class="text-link">时间线</RouterLink><button v-if="item.status === 'failed' && !record.archived" class="button small" :disabled="busy || ['queued', 'running', 'waiting_schedule'].includes(record.status)" @click="retryInput(item.id)">重试此来源</button></div></section>
    <div v-if="record.history_matches?.length" class="capture-history notice"><strong>你曾收藏过这里的内容</strong><RouterLink v-for="item in record.history_matches.slice(0, 5)" :key="item.resource_id + item.record_id" :to="'/records/' + item.record_id">{{ date(item.created_at) }} · {{ item.title }}{{ item.archived ? '（已归档）' : '' }}</RouterLink><p>每次主动收藏分别保留，这次的想法不会覆盖以前的表达。</p></div>
    <div class="detail-layout"><section>
      <div class="content-tabs"><button v-for="item in [{ id: 'analysis', label: '分析整理' }, { id: 'original', label: '原始内容' }, { id: 'relations', label: '发现联系' }]" :key="item.id" :class="{ selected: tab === item.id }" @click="tab = item.id">{{ item.label }}<span v-if="item.id === 'relations'">{{ record.relations.filter(r => r.feedback !== 'ignored').length }}</span></button></div>
      <div v-if="tab === 'analysis'" class="paper reading-paper">
        <template v-if="analysis"><div class="section-title"><span class="eyebrow">AI 整理 · 基于已读取的资料</span><select v-if="record.analyses.length > 1" v-model="analysisId" aria-label="选择分析历史"><option value="">最新分析</option><option v-for="item in record.analyses" :key="item.id" :value="item.id">原件 v{{ item.version }} · {{ date(item.created_at) }}</option></select></div><p v-if="analysis.version !== record.version" class="notice">这是原件第 {{ analysis.version }} 版的分析，当前原件已更新。</p><h2>内容概览</h2><p class="summary-text">{{ analysis.summary }}</p><div class="topic-row"><span v-for="topic in analysis.topics" :key="topic" class="topic">{{ topic }}</span></div><hr /><h2>{{ analysis.kind === 'interview' ? '面试问答与复盘' : '值得留下的要点' }}</h2><div v-for="(point, index) in analysis.points" :key="index" class="analysis-point"><span class="point-number">{{ String(index + 1).padStart(2, '0') }}</span><div><MarkdownContent :content="point.text" /><div class="citation-buttons"><button v-for="sid in point.source_ids" :key="sid" @click="jump(sid)"><Icon name="link" :size="13" />查看来源</button></div></div></div><template v-if="analysis.observations"><hr /><div class="inference-label"><Icon name="wiki" :size="16" />AI 分析与建议 · 包含推断</div><MarkdownContent :content="analysis.observations" /></template><p class="generation-note">{{ analysis.model }} · {{ date(analysis.created_at) }} · 请结合原始资料核对</p></template>
        <div v-else class="empty-state"><Icon name="wiki" :size="34" /><h2>内容已留下，知识正在生长</h2><p>{{ currentRun?.error || '后台将读取资料、分析重点，并查找相关的旧内容。' }}</p><RouterLink v-if="record.status === 'waiting_configuration'" to="/settings" class="button primary">配置整理服务</RouterLink><button v-else-if="['failed', 'budget_exceeded', 'cancelled'].includes(record.status)" class="button" :disabled="busy" @click="action('retry')">继续处理</button></div>
      </div>
      <div v-if="tab === 'original'" class="paper reading-paper"><div class="section-title"><h2>原始记录</h2><span class="subtle">v{{ record.version }}</span></div><p v-if="record.content" class="original-text">{{ record.content }}</p><div v-if="record.notes" class="personal-note"><span class="eyebrow">为什么收藏 · 个人备注</span><p>{{ record.notes }}</p></div><template v-if="record.attachments.length"><h3>附件素材</h3><div class="attachment-list"><button v-for="item in record.attachments" :key="item.id" class="button" :class="{ selected: selectedAttachment === item.id }" @click="selectedAttachment = item.id"><Icon name="file" :size="16" />{{ item.filename }}</button></div><div v-if="attachment" class="media-preview"><video v-if="attachment.media_type.startsWith('video/')" ref="media" :src="'/api/files/' + attachment.id" controls preload="metadata" /><audio v-else-if="attachment.media_type.startsWith('audio/')" ref="media" :src="'/api/files/' + attachment.id" controls preload="metadata" /><img v-else-if="attachment.media_type.startsWith('image/')" :src="'/api/files/' + attachment.id" :alt="attachment.filename" loading="lazy" /><a v-else :href="'/api/files/' + attachment.id" target="_blank" rel="noopener" class="text-link">打开 {{ attachment.filename }}<Icon name="arrow" :size="15" /></a></div></template><h3>已读取的证据片段 <span class="subtle">{{ visibleSources.length }}</span></h3><p v-if="!visibleSources.length" class="subtle">尚未取得可读片段，原始内容与附件已保留。</p><article v-for="source in visibleSources" :id="'source-' + source.id" :key="source.id" class="source-card" :class="{ highlighted: highlighted === source.id }"><div class="source-caption"><span>{{ locationLabel(source.locator) }} · {{ source.method }}</span><span v-if="source.stale" class="warning-text">历史原件 v{{ source.version }}</span><button v-if="source.locator.start !== undefined" class="text-link" @click="jump(source.id)">定位片段</button></div><p class="original-text">{{ source.text }}</p></article></div>
      <div v-if="tab === 'relations'" class="paper reading-paper"><h2>新内容，与旧积累相遇</h2><p class="subtle">每一条联系都有具体理由和两侧依据。你的纠正会被保留。</p><div v-if="!record.relations.length" class="empty-state"><Icon name="branch" :size="32" /><p>目前没有足够依据建立联系。<br />继续积累，合适的内容会在这里相遇。</p></div><article v-for="relation in record.relations" :key="relation.id" class="relation-card" :class="{ muted: relation.feedback === 'ignored' }"><div class="section-title"><span class="topic">{{ relationLabels[relation.kind] }}</span><span class="subtle">{{ relation.feedback === 'accepted' ? '已认可' : relation.feedback === 'ignored' ? '已忽略' : 'AI 建议' }}</span></div><RouterLink :to="'/records/' + relation.target_id"><h3>{{ relation.target_title }}</h3></RouterLink><p>{{ relation.explanation }}</p><p v-if="relation.stale" class="notice">来源已更新，请重新核对这条联系。</p><blockquote v-for="evidence in relation.evidence" :key="evidence.source_id"><p>{{ evidence.quote }}</p><button class="text-link" @click="jump(evidence.source_id)">核对来源<Icon name="arrow" :size="13" /></button></blockquote><div class="button-row"><button v-if="relation.feedback !== 'accepted'" class="button small" @click="feedback(relation.id, 'accepted')">认可联系</button><button class="button small" @click="feedback(relation.id, relation.feedback === 'ignored' ? 'suggested' : 'ignored')">{{ relation.feedback === 'ignored' ? '恢复显示' : '忽略联系' }}</button></div></article></div>
    </section><aside class="context-column"><div v-if="record.context_suggestions?.length" class="paper side-card"><h3>新内容与旧积累</h3><article v-for="item in record.context_suggestions" :key="item.relation_id" class="context-hint"><span class="subtle">{{ item.feedback === 'accepted' ? '已认可的联系' : '可能相关 · AI 建议' }}</span><RouterLink :to="'/records/' + item.record_id"><strong>{{ item.title }}</strong></RouterLink><p>{{ item.explanation }}</p><button v-for="evidence in item.evidence" :key="evidence.source_id" class="text-link" @click="jump(evidence.source_id)">核对依据</button></article></div><div v-for="insight in (record.insights || []).filter(item => !item.stale && item.status !== 'insufficient_evidence')" :key="insight.id" class="paper side-card"><span class="eyebrow">再次关注 · {{ insight.feedback === 'accepted' ? '已认可' : '可能相关' }}</span><template v-if="insight.status === 'ready'"><p v-if="insight.feedback !== 'ignored'">{{ insight.explanation }}</p><p v-else class="subtle">已忽略此提示，你的反馈会保留。</p><button v-for="evidence in insight.feedback === 'ignored' ? [] : insight.evidence" :key="evidence.source_id" class="text-link" @click="jump(evidence.source_id)">核对个人表达</button><div class="button-row"><button v-if="insight.feedback !== 'accepted' && insight.feedback !== 'ignored'" class="button small" @click="insightFeedback(insight.id, 'accepted')">认可</button><button class="button small" @click="insightFeedback(insight.id, insight.feedback === 'ignored' ? 'suggested' : 'ignored')">{{ insight.feedback === 'ignored' ? '恢复提示' : '不准确' }}</button></div></template><p v-else class="subtle">{{ insight.error || '正在比较两次明确表达，证据不足时不会生成推测。' }}</p><button v-if="['failed', 'waiting_configuration', 'budget_exceeded'].includes(insight.status)" class="button small" @click="retryInsight(insight.id)">重试解释</button></div><div class="paper side-card"><div class="section-title"><h3>整理状态</h3><StatusBadge :status="record.status" /></div><ol class="stage-list"><li v-for="(stage, index) in steps" :key="stage" :class="{ done: record.status === 'completed' || steps.indexOf(currentRun?.stage || '') > index, current: currentRun?.stage === stage }"><i><Icon v-if="record.status === 'completed' || steps.indexOf(currentRun?.stage || '') > index" name="check" :size="14" /><template v-else>{{ index + 1 }}</template></i>{{ stageLabels[stage] }}</li></ol><p v-if="currentRun?.error" class="notice">{{ currentRun.error }}</p><div class="stack-actions"><button v-if="!['queued', 'running', 'waiting_schedule'].includes(record.status) && !record.archived" class="button" :disabled="busy" @click="action(record.status === 'completed' ? 'reanalyze' : 'retry')">{{ record.status === 'completed' ? '重新分析' : '继续处理' }}</button><button v-if="!record.archived && !['queued', 'running', 'waiting_schedule'].includes(record.status)" class="button subtle-button" :disabled="busy" @click="action('reread')">重新读取原件</button><button class="button subtle-button" :disabled="busy" @click="action('archive')">{{ record.archived ? '恢复归档' : '归档这条记录' }}</button></div></div><div v-if="record.coverage.details?.length" class="coverage-card"><Icon name="file" :size="18" /><h3>实际读取范围</h3><p v-for="item in record.coverage.details" :key="item">{{ item }}</p></div><div class="side-section"><h3>参与形成的 Wiki</h3><RouterLink v-for="page in record.pages" :key="page.id" :to="'/wiki/' + page.id" class="wiki-mini"><Icon name="wiki" :size="18" /><span>{{ page.title }}<small>版本 {{ page.version }}</small></span><Icon name="arrow" :size="14" /></RouterLink><p v-if="!record.pages.length" class="side-empty">{{ record.coverage.wiki_note || '适合沉淀的知识会进入 Wiki，并保留来源。' }}</p></div></aside></div>
  </template>
  <div v-if="editing && record" class="modal-overlay"><form class="edit-panel" @submit.prevent="saveEdit"><div class="section-title"><h2>编辑原始记录</h2><button type="button" class="icon-button" aria-label="关闭编辑" @click="editing = false"><Icon name="close" /></button></div><label class="field">标题<input v-model="editForm.title" required maxlength="300" /></label><label class="field">原文<textarea v-model="editForm.content" rows="10" maxlength="200000" /></label><label class="field">个人备注<textarea v-model="editForm.notes" rows="3" /></label><label class="field">整理用途<select v-model="editForm.purpose"><option value="auto">自动判断</option><option value="learning">学习资料</option><option value="interview">面试复盘</option><option value="note">个人随笔</option></select></label><p class="subtle">保存后创建新的原件版本。旧引用继续保留，并标明历史版本。</p><button class="button primary" :disabled="busy">{{ busy ? '保存中…' : '保存并整理新版本' }}</button></form></div>
</template>
