<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, date, kindLabels, type RecordItem, type WikiPage } from '../../api'
import { composerOpen, refreshVersion } from '../../state'
import Icon from '../../components/Icon.vue'
import StatusBadge from '../../components/StatusBadge.vue'

const records = ref<RecordItem[]>([]), pages = ref<WikiPage[]>([]), total = ref(0)
const kind = ref(''), status = ref(''), archived = ref(false), loading = ref(true), error = ref(''), offset = ref(0)
const limit = 20
const completed = computed(() => records.value.filter(r => r.status === 'completed').length)
async function load(initial = false) {
  if (initial) loading.value = true
  try {
    const params = new URLSearchParams({ kind: kind.value, status: status.value, archived: String(archived.value), limit: String(limit), offset: String(offset.value) })
    const [data, wiki] = await Promise.all([api<{ items: RecordItem[]; total: number }>('/records?' + params), api<{ items: WikiPage[] }>('/wiki')])
    records.value = data.items; total.value = data.total; pages.value = wiki.items.slice(0, 4); error.value = ''
  } catch (e) { error.value = (e as Error).message }
  finally { loading.value = false }
}
let timer: ReturnType<typeof setInterval>
onMounted(() => { void load(); timer = setInterval(() => { if (!document.hidden) void load() }, 5000) })
onUnmounted(() => clearInterval(timer))
watch([kind, status, archived], () => { offset.value = 0; void load(true) })
watch([offset, refreshVersion], () => load())
function hostname(url: string | null) { try { return url ? new URL(url).hostname.replace(/^www\./, '') : '自己的记录' } catch { return '外部内容' } }
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">YOUR KNOWLEDGE, CONNECTED</span><h1>让收藏，成为知识。</h1><p>留下重要的内容，发现它们之间的联系。</p></div><span class="heading-note"><span class="tiny-leaf">✳</span> 每一份积累，都有迹可循</span></div>
  <div class="library-layout">
    <section>
      <div class="collection-toolbar"><div class="filter-tabs"><button v-for="tab in [{ id: '', label: '全部内容' }, { id: 'link', label: '链接' }, { id: 'text', label: '随手记' }, { id: 'file', label: '附件' }, { id: 'mixed', label: '混合' }]" :key="tab.id" :class="{ selected: kind === tab.id }" @click="kind = tab.id">{{ tab.label }}</button></div><select v-model="status" class="filter-select" aria-label="按处理状态筛选"><option value="">全部状态</option><option value="completed">整理完成</option><option value="queued">等待整理</option><option value="running">正在整理</option><option value="failed">处理失败</option><option value="waiting_configuration">等待配置</option><option value="budget_exceeded">额度不足</option><option value="cancelled">已停止</option></select></div>
      <div class="list-caption"><span>{{ archived ? '已归档' : '最近收藏' }} <span class="subtle">{{ total }} 条内容</span></span><label class="archive-toggle"><input v-model="archived" type="checkbox" /> 查看归档</label></div>
      <div v-if="error" class="error-panel"><strong>暂时无法读取知识库</strong><p>{{ error }}</p><button class="button" @click="load(true)">重新连接</button><RouterLink to="/settings">检查设置</RouterLink></div>
      <div v-else-if="loading" class="empty-state"><span class="spinner" /><p>正在打开你的知识空间…</p></div>
      <div v-else-if="!records.length" class="empty-state paper"><div class="empty-symbol"><Icon name="library" :size="34" /></div><h2>{{ archived ? '还没有归档内容' : '第一份知识，从这里开始' }}</h2><p>一篇值得重读的文章，一次面试录屏，<br />或一闪而过的想法，都可以留下来。</p><button v-if="!archived" class="button primary" @click="composerOpen = true"><Icon name="plus" :size="17" />记录第一条内容</button></div>
      <div v-else class="record-grid"><RouterLink v-for="record in records" :key="record.id" :to="'/records/' + record.id" class="record-card"><div class="record-meta"><span class="content-kind" :class="record.kind"><Icon :name="record.kind === 'link' ? 'link' : record.kind === 'text' ? 'note' : 'file'" :size="17" />{{ kindLabels[record.kind] }}</span><time>{{ date(record.created_at) }}</time></div><h2>{{ record.title }}</h2><p class="card-excerpt">{{ record.summary || record.content || record.notes || '内容已保存，等待后台读取与整理。' }}</p><div class="topic-row"><span v-for="topic in record.topics.slice(0, 3)" :key="topic" class="topic">{{ topic }}</span><span v-if="!record.topics.length" class="subtle">{{ record.summary ? '已保留来源' : '原始内容已保存' }}</span></div><div class="card-footer"><StatusBadge :status="record.status" /><span class="source-host">{{ hostname(record.url) }}<Icon name="arrow" :size="14" /></span></div></RouterLink></div>
      <div v-if="total > limit" class="pagination"><button class="button" :disabled="offset === 0" @click="offset -= limit">上一页</button><span>{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(total / limit) }}</span><button class="button" :disabled="offset + limit >= total" @click="offset += limit">下一页</button></div>
    </section>
    <aside class="context-column"><div class="welcome-card"><div class="welcome-illustration"><div class="orbit orbit-one" /><div class="orbit orbit-two" /><span class="seed seed-one" /><span class="seed seed-two" /><span class="seed seed-three" /><Icon name="spark" :size="32" /></div><span class="eyebrow">A SECOND BRAIN</span><h3>收藏之后，<br />还有新的可能。</h3><p>新资料与旧积累相遇，<br />慢慢形成自己的知识脉络。</p><RouterLink to="/wiki" class="text-link">打开知识 Wiki<Icon name="arrow" :size="16" /></RouterLink></div>
      <div class="side-section"><div class="section-title"><h3>最近更新的 Wiki</h3><Icon name="wiki" :size="18" /></div><RouterLink v-for="page in pages" :key="page.id" :to="'/wiki/' + page.id" class="wiki-mini"><span class="wiki-mini-icon"><Icon name="wiki" :size="17" /></span><span>{{ page.title }}<small>{{ page.source_ids.length }} 份来源 · v{{ page.version }}</small></span><Icon name="arrow" :size="15" /></RouterLink><p v-if="!pages.length" class="side-empty">整理后的知识会在这里汇聚。<br />每个页面都能回到原始来源。</p></div>
      <div class="quiet-note"><Icon name="clock" :size="18" /><div><strong>按自己的节奏积累</strong><p>本页 {{ completed }} 条内容已整理。<br />你可以随时回来，继续阅读。</p></div></div>
    </aside>
  </div>
</template>
