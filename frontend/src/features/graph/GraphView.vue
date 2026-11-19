<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import cytoscape, { type Core, type StylesheetJson } from 'cytoscape'
import { api, type GraphData, type GraphDetail, type GraphEdge, type GraphNode, type Source } from '../../api'
import { notify, refreshVersion } from '../../state'
import Icon from '../../components/Icon.vue'

const route = useRoute(), router = useRouter(), canvas = ref<HTMLDivElement>()
const data = ref<GraphData | null>(null), detail = ref<GraphDetail | null>(null)
const selected = ref<GraphNode | null>(null), selectedEdge = ref<GraphEdge | null>(null)
const q = ref(''), focus = ref(String(route.query.focus || '')), hops = ref<number | undefined>()
const since = ref(''), until = ref(''), archived = ref(false), kinds = ref(['record', 'resource', 'topic', 'wiki'])
const mode = ref(window.matchMedia('(max-width: 640px)').matches ? 'list' : 'graph')
const loading = ref(false), error = ref('')
const kindLabels: Record<string, string> = { record: '收藏事件', resource: '内容资源', topic: '主题', wiki: 'Wiki' }
const relationLabels: Record<string, string> = { same_topic: '同一主题', complements: '相互补充', extends: '知识延伸', contradicts: '不同观点', experience: '经验对应', duplicate: '内容重复' }
let graph: Core | undefined, observer: ResizeObserver | undefined, controller: AbortController | undefined

async function load() {
  controller?.abort(); controller = new AbortController()
  loading.value = true; error.value = ''
  try {
    const params = new URLSearchParams({ q: q.value, include_archived: String(archived.value), kinds: kinds.value.join(',') })
    if (focus.value) params.set('focus', focus.value)
    if (hops.value) params.set('hops', String(hops.value))
    if (since.value) params.set('since', new Date(since.value + 'T00:00:00').toISOString())
    if (until.value) params.set('until', new Date(until.value + 'T23:59:59.999').toISOString())
    data.value = await api<GraphData>('/graph?' + params, { signal: controller.signal })
    hops.value = data.value.limits.hops
    selected.value = null; selectedEdge.value = null; detail.value = null
    await nextTick(); draw()
  } catch (e) {
    if (!(e instanceof DOMException && e.name === 'AbortError')) error.value = (e as Error).message
  } finally { loading.value = false }
}
function draw() {
  if (!canvas.value || !data.value) return
  graph?.destroy()
  const style: StylesheetJson = [
    { selector: 'node', style: { label: 'data(label)', 'font-size': 11, 'text-wrap': 'ellipsis',
      'text-max-width': '100px', 'text-valign': 'bottom', 'text-margin-y': 7,
      color: '#445438', 'background-color': '#8ba573', width: 26, height: 26 } },
    { selector: 'node[kind = "record"]', style: { shape: 'round-rectangle', 'background-color': '#a4b68d' } },
    { selector: 'node[kind = "resource"]', style: { shape: 'ellipse', 'background-color': '#779a69' } },
    { selector: 'node[kind = "topic"]', style: { shape: 'diamond', 'background-color': '#c0a675', width: 32, height: 32 } },
    { selector: 'node[kind = "wiki"]', style: { shape: 'hexagon', 'background-color': '#708e88', width: 34, height: 34 } },
    { selector: 'edge', style: { width: 1.3, 'line-color': '#bcc8af', 'target-arrow-color': '#bcc8af',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.7 } },
    { selector: 'edge[feedback = "suggested"]', style: { 'line-style': 'dashed' } },
    { selector: ':selected', style: { 'border-width': 3, 'border-color': '#456237', 'line-color': '#456237' } },
  ]
  graph = cytoscape({ container: canvas.value,
    elements: [...data.value.nodes.map(node => ({ data: node })), ...data.value.edges.map(edge => ({ data: edge }))],
    style, layout: { name: 'cose', animate: false, fit: true, padding: 45, nodeDimensionsIncludeLabels: true },
    minZoom: 0.15, maxZoom: 3,
  })
  graph.on('tap', 'node', event => { void pickNode(event.target.data() as GraphNode) })
  graph.on('tap', 'edge', event => pickEdge(event.target.data() as GraphEdge))
}
async function pickNode(node: GraphNode) {
  selected.value = node; selectedEdge.value = null; detail.value = null
  try {
    const [kind, id] = node.id.split(':')
    const result = await api<GraphDetail>(`/graph/nodes/${kind}/${id}?include_archived=${archived.value}`)
    if (selected.value?.id === node.id) detail.value = result
  } catch (e) { notify((e as Error).message, true) }
}
function pickEdge(edge: GraphEdge) { selectedEdge.value = edge; selected.value = null; detail.value = null }
async function edgeFeedback(feedback: string) {
  const edge = selectedEdge.value
  if (!edge?.reference_id) return
  const path = edge.kind === 'topic' ? '/graph/topics/' : '/relations/'
  try { await api(path + edge.reference_id, { method: 'PATCH', body: JSON.stringify({ feedback }) }); await load() }
  catch (e) { notify((e as Error).message, true) }
}
async function openSource(id: string) {
  try { const source = await api<Source>('/sources/' + id); await router.push('/records/' + source.record_id + '#source-' + id) }
  catch (e) { notify((e as Error).message, true) }
}
function expand() {
  if (!selected.value || !data.value) return
  focus.value = selected.value.id
  hops.value = Math.min((hops.value || 1) + 1, data.value.limits.max_hops)
  void load()
}
function search() { focus.value = ''; void load() }
function label(id: string) { return data.value?.nodes.find(node => node.id === id)?.label || id }
watch(mode, async () => { await nextTick(); graph?.resize(); graph?.fit(undefined, 45) })
watch(refreshVersion, load)
watch(() => route.query.focus, value => { focus.value = String(value || ''); void load() })
onMounted(() => {
  void load()
  observer = new ResizeObserver(() => { graph?.resize() })
  if (canvas.value) observer.observe(canvas.value)
})
onUnmounted(() => { controller?.abort(); observer?.disconnect(); graph?.destroy() })
</script>

<template>
  <div class="page-heading"><div><span class="eyebrow">FOLLOW THE CONNECTIONS</span><h1>知识图谱</h1><p>从一份收藏出发，沿着来源、主题与时间继续探索。</p></div></div>
  <form class="graph-toolbar paper" @submit.prevent="search"><label class="field">定位内容<input v-model="q" placeholder="搜索收藏、资源、主题或 Wiki" /></label><label class="field">开始日期<input v-model="since" type="date" /></label><label class="field">结束日期<input v-model="until" type="date" /></label><label class="field">展开层数<select v-model.number="hops"><option v-for="value in data?.limits.max_hops || 0" :key="value" :value="value">{{ value }} 跳</option></select></label><button class="button primary" :disabled="loading">查找</button><button type="button" class="button" :disabled="loading" @click="load">刷新</button></form>
  <div class="graph-options"><label v-for="(title, kind) in kindLabels" :key="kind"><input v-model="kinds" type="checkbox" :value="kind" :disabled="kinds.length === 1 && kinds.includes(String(kind))" @change="load" />{{ title }}</label><label><input v-model="archived" type="checkbox" @change="load" />含归档历史</label><div class="button-row"><button class="button small" :aria-pressed="mode === 'graph'" @click="mode = 'graph'">图形</button><button class="button small" :aria-pressed="mode === 'list'" @click="mode = 'list'">关系列表</button></div></div>
  <p v-if="error" class="error-message" role="alert">{{ error }}</p>
  <p v-if="data?.truncated" class="notice" role="status">已达到查询容量，请筛选内容或以某个节点为中心展开。每次最多 {{ data.limits.nodes }} 个节点、{{ data.limits.edges }} 条关系。</p>
  <div class="graph-layout"><section class="paper graph-main" :aria-busy="loading"><div class="section-title"><span class="subtle">{{ data?.nodes.length || 0 }} 个节点 · {{ data?.edges.length || 0 }} 条关系</span><button class="button small" @click="graph?.fit(undefined, 45)">适应画布</button></div><p class="subtle graph-legend">实线：系统事实或已认可关系 · 虚线：AI 建议</p>
    <div v-show="mode === 'graph'" ref="canvas" class="graph-canvas" aria-hidden="true" />
    <div v-if="!loading && !data?.nodes.length" class="empty-state"><Icon name="branch" :size="32" /><h2>还没有匹配的节点</h2><p>保存内容后，来源和主题会逐步连接起来。</p></div>
    <div v-if="mode === 'list'" class="graph-list"><h2>节点</h2><button v-for="node in data?.nodes" :key="node.id" class="graph-node-button" @click="pickNode(node)"><span class="topic">{{ kindLabels[node.kind] }}</span><span>{{ node.label }}</span></button><h2 v-if="data?.edges.length">关系</h2><button v-for="edge in data?.edges" :key="edge.id" class="graph-edge-button" @click="pickEdge(edge)"><span>{{ label(edge.source) }}</span><small>{{ relationLabels[edge.label] || edge.label }} · {{ edge.feedback === 'fact' ? '系统事实' : edge.feedback === 'accepted' ? '已认可' : 'AI 建议' }}</small><span>{{ label(edge.target) }}</span></button></div>
  </section><aside class="paper graph-detail" aria-live="polite">
    <template v-if="selected"><span class="eyebrow">{{ kindLabels[selected.kind] }}</span><h2>{{ selected.label }}</h2><div class="button-row"><RouterLink v-if="selected.href" :to="selected.href" class="button small">打开内容</RouterLink><button class="button small" @click="expand">以此为中心展开</button></div><p v-if="detail?.record?.content" class="original-text">{{ detail.record.content }}</p><p v-if="detail?.content" class="subtle">{{ detail.content.slice(0, 800) }}</p><a v-if="detail?.url" :href="detail.url" target="_blank" rel="noopener noreferrer" class="text-link">打开外部来源</a><template v-if="selected.kind === 'resource'"><h3>收藏时间线</h3><ol class="capture-timeline"><li v-for="record in detail?.timeline" :key="record.id"><time>{{ new Date(record.created_at).toLocaleString() }}</time><RouterLink :to="'/records/' + record.id">{{ record.title }}</RouterLink><p v-if="record.notes">{{ record.notes }}</p><span v-if="record.archived" class="subtle">已归档</span></li></ol><p v-if="detail?.truncated" class="notice">历史记录较多，当前显示最近 {{ detail.timeline.length }} 次。</p></template></template>
    <template v-else-if="selectedEdge"><span class="eyebrow">{{ selectedEdge.feedback === 'fact' ? '系统事实' : selectedEdge.feedback === 'accepted' ? '用户已认可' : 'AI 建议 · 待核对' }}</span><h2>{{ relationLabels[selectedEdge.label] || selectedEdge.label }}</h2><p>{{ selectedEdge.explanation || '该关系来自已保存的收藏与来源。' }}</p><blockquote v-for="evidence in selectedEdge.evidence" :key="evidence.source_id"><p v-if="evidence.quote">{{ evidence.quote }}</p><span class="subtle">来源版本 v{{ evidence.version }} · </span><button class="text-link" @click="openSource(evidence.source_id)">核对来源</button></blockquote><p v-if="!selectedEdge.evidence.length && selectedEdge.feedback !== 'fact'" class="notice">来自主题整理，尚无片段级来源映射。</p><div v-if="['topic', 'relation'].includes(selectedEdge.kind) && selectedEdge.reference_id" class="button-row"><button class="button small" @click="edgeFeedback('accepted')">认可关系</button><button class="button small" @click="edgeFeedback('ignored')">忽略关系</button></div></template>
    <div v-else class="empty-state"><Icon name="branch" :size="28" /><p>选择一个节点或关系，查看它的来源、依据和收藏历史。</p></div>
  </aside></div>
</template>
