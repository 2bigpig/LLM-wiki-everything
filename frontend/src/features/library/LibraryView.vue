<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, date, kindLabels, type RecordItem, type Reviews, type WikiPage } from '../../api'
import { composerOpen, refreshVersion } from '../../state'
import Icon from '../../components/Icon.vue'
import StatusBadge from '../../components/StatusBadge.vue'

const records = ref<RecordItem[]>([]), pages = ref<WikiPage[]>([]), total = ref(0), wikiCount = ref(0)
const kind = ref(''), status = ref(''), archived = ref(false), loading = ref(true), error = ref(''), offset = ref(0)
const limit = 20
const reviewCount = ref(0)
const completed = computed(() => records.value.filter(r => r.status === 'completed').length)
async function load(initial = false) {
  if (initial) loading.value = true
  try {
    const params = new URLSearchParams({ kind: kind.value, status: status.value, archived: String(archived.value), limit: String(limit), offset: String(offset.value) })
    const [data, wiki, reviews] = await Promise.all([api<{ items: RecordItem[]; total: number }>('/records?' + params), api<{ items: WikiPage[] }>('/wiki'), api<Reviews>('/reviews').catch(() => null)])
    reviewCount.value = reviews?.items.length || 0
    records.value = data.items; total.value = data.total; wikiCount.value = wiki.items.length
    pages.value = wiki.items.slice(0, 4); error.value = ''
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
  <div class="page-heading"><div><span class="eyebrow">个人资料库</span><h1>我的收藏<span class="title-count">{{ total }}</span></h1><p>保存原始资料，留下每一次思考</p></div><span class="heading-note"><Icon name="clock" />按收藏时间排列</span></div>
  <RouterLink to="/reviews" class="review-entry paper"><span class="review-entry-icon"><Icon name="clock" /></span><div><strong>今日回顾<span v-if="reviewCount" class="review-count">{{ reviewCount }} 条</span></strong><span>{{ reviewCount ? '已有旧内容可供回顾，查看它们与近期收藏的联系' : '从收藏记录与已有联系中，重新发现旧内容' }}</span></div><span class="review-entry-action"><span class="review-entry-label">查看回顾</span><Icon name="arrow" /></span></RouterLink>
  <div class="library-layout">
    <section>
      <div class="collection-toolbar"><div class="filter-tabs"><button v-for="tab in [{ id: '', label: '全部内容' }, { id: 'link', label: '链接' }, { id: 'text', label: '随手记' }, { id: 'file', label: '附件' }, { id: 'mixed', label: '混合' }]" :key="tab.id" :class="{ selected: kind === tab.id }" @click="kind = tab.id">{{ tab.label }}</button></div><select v-model="status" class="filter-select" aria-label="按处理状态筛选"><option value="">全部状态</option><option value="completed">整理完成</option><option value="queued">等待整理</option><option value="running">正在整理</option><option value="failed">处理失败</option><option value="waiting_configuration">等待配置</option><option value="budget_exceeded">额度不足</option><option value="cancelled">已停止</option></select></div>
      <div class="list-caption"><span>{{ archived ? '已归档' : '最近收藏' }} <span class="subtle">{{ total }} 条内容</span></span><label class="archive-toggle"><input v-model="archived" type="checkbox" /> 查看归档</label></div>
      <div v-if="error" class="error-panel"><strong>暂时无法读取知识库</strong><p>{{ error }}</p><button class="button" @click="load(true)">重新连接</button><RouterLink to="/settings">检查设置</RouterLink></div>
      <div v-else-if="loading" class="empty-state"><span class="spinner" /><p>正在打开你的知识空间…</p></div>
      <div v-else-if="!records.length" class="empty-state paper"><div class="empty-symbol"><Icon name="archive" :size="28" /></div><h2>{{ archived ? '暂无归档内容' : '保存第一份资料' }}</h2><p>文字、链接、截图和文件，都可以从同一个入口保存</p><button v-if="!archived" class="button primary" @click="composerOpen = true"><Icon name="plus" />记录内容</button></div>
      <div v-else class="record-grid" aria-label="收藏列表">
        <RouterLink v-for="(record, index) in records" :key="record.id" :to="'/records/' + record.id" class="record-card">
          <span class="record-index" aria-hidden="true">{{ String(offset + index + 1).padStart(2, '0') }}</span>
          <div class="record-content"><div class="record-meta"><span class="content-kind"><Icon :name="record.kind === 'link' ? 'link' : record.kind === 'text' ? 'note' : 'file'" />{{ kindLabels[record.kind] }}</span><span class="meta-separator">/</span><time>{{ date(record.created_at) }}</time></div><h2>{{ record.title }}</h2><p class="card-excerpt">{{ record.summary || record.content || record.notes || '原始内容已保存，等待后台读取与整理' }}</p><div v-if="record.topics.length" class="topic-row"><span v-for="topic in record.topics.slice(0, 3)" :key="topic" class="topic">{{ topic }}</span></div></div>
          <div class="record-side"><StatusBadge :status="record.status" /><span class="source-host">{{ hostname(record.url) }}</span></div><Icon name="arrow" class="record-arrow" />
        </RouterLink>
      </div>
      <div v-if="total > limit" class="pagination"><button class="button" :disabled="offset === 0" @click="offset -= limit">上一页</button><span>{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(total / limit) }}</span><button class="button" :disabled="offset + limit >= total" @click="offset += limit">下一页</button></div>
    </section>
    <aside class="context-column"><section class="index-card paper"><div class="section-title"><h2>知识索引</h2><Icon name="wiki" /></div><div class="index-total">{{ wikiCount }}<span>个 Wiki 页面</span></div><p>收藏中的知识按主题汇合，每一节都能追溯原文</p><RouterLink to="/wiki" class="text-link">浏览知识 Wiki<Icon name="arrow" /></RouterLink></section>
      <div class="side-section"><div class="section-title"><h3>最近更新的 Wiki</h3><Icon name="wiki" :size="18" /></div><RouterLink v-for="page in pages" :key="page.id" :to="'/wiki/' + page.id" class="wiki-mini"><span class="wiki-mini-icon"><Icon name="wiki" :size="17" /></span><span>{{ page.title }}<small>{{ page.source_ids.length }} 份来源 · v{{ page.version }}</small></span><Icon name="arrow" :size="15" /></RouterLink><p v-if="!pages.length" class="side-empty">整理后的知识会在这里汇聚。<br />每个页面都能回到原始来源。</p></div>
      <div class="quiet-note"><Icon name="link" /><div><strong>从原文到知识</strong><p>本页 {{ completed }} 条已整理<br />每次收藏与来源版本独立保留</p><RouterLink to="/graph" class="text-link">探索知识图谱<Icon name="arrow" /></RouterLink></div></div>
    </aside>
  </div>
</template>
