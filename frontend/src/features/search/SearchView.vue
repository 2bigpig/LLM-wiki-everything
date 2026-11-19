<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, locationLabel, type SearchHit } from '../../api'
import Icon from '../../components/Icon.vue'
const route = useRoute(), router = useRouter(), query = ref(String(route.query.q || ''))
const results = ref<SearchHit[]>([]), loading = ref(false), error = ref(''), semantic = ref(false)
async function search() {
  query.value = String(route.query.q || '')
  if (!query.value.trim()) return
  loading.value = true
  try { const data = await api<{ items: SearchHit[]; semantic: boolean }>('/search?q=' + encodeURIComponent(query.value)); results.value = data.items; semantic.value = data.semantic; error.value = '' }
  catch (e) { error.value = (e as Error).message }
  finally { loading.value = false }
}
function submit() { void router.push({ path: '/search', query: { q: query.value } }) }
onMounted(search); watch(() => route.query.q, search)
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">FIND WHAT YOU ALREADY KNOW</span><h1>让旧知识，重新有用。</h1><p>从原始资料中找回具体片段，继续上一次的思考。</p></div></div>
  <form class="large-search" @submit.prevent="submit"><Icon name="search" :size="22" /><input v-model="query" aria-label="搜索资料" placeholder="记得一个关键词，或描述你想找的内容…" maxlength="300" required /><button class="button primary">查找资料</button></form>
  <p class="list-caption">{{ semantic ? '关键词 + 语义检索' : '关键词检索' }}<span class="subtle">{{ results.length }} 个证据片段</span></p>
  <div v-if="error" class="error-panel"><p>{{ error }}</p></div><div v-else-if="loading" class="empty-state"><span class="spinner" /><p>正在找回相关资料…</p></div><div v-else-if="!results.length" class="empty-state paper"><Icon name="search" :size="34" /><h2>暂时没有找到相关片段</h2><p>试试具体的概念、技术名词或原文关键词。<br />尚未读取的资料会在读取完成后进入搜索。</p></div><div v-else class="search-results"><RouterLink v-for="hit in results" :key="hit.id" :to="'/records/' + hit.record_id + '#source-' + hit.id" class="paper search-result"><div class="section-title"><h2>{{ hit.record_title }}</h2><Icon name="arrow" :size="18" /></div><p>{{ hit.text.slice(0, 650) }}{{ hit.text.length > 650 ? '…' : '' }}</p><span class="topic">{{ locationLabel(hit.locator) }}</span></RouterLink></div>
</template>
