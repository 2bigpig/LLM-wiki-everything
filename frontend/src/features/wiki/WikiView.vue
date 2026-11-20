<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, date, type WikiPage } from '../../api'
import { composerOpen, refreshVersion } from '../../state'
import Icon from '../../components/Icon.vue'
const pages = ref<WikiPage[]>([]), query = ref(''), loading = ref(true), error = ref('')
const filtered = computed(() => pages.value.filter(p => p.title.toLowerCase().includes(query.value.toLowerCase())))
async function load() {
  try { pages.value = (await api<{ items: WikiPage[] }>('/wiki')).items; error.value = '' }
  catch (e) { error.value = (e as Error).message }
  finally { loading.value = false }
}
onMounted(load); watch(refreshVersion, load)
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">主题知识</span><h1>知识 Wiki</h1><p>不同来源汇成知识页面，每一节保留原文引用</p></div><span class="heading-counter">{{ pages.length }}<small>个知识页面</small></span></div>
  <div class="collection-toolbar"><span class="section-label">全部 Wiki</span><div class="inline-search"><Icon name="search" :size="17" /><input v-model="query" placeholder="查找主题…" aria-label="查找 Wiki 主题" /></div></div>
  <div v-if="error" class="error-panel"><p>{{ error }}</p><button class="button" @click="load">重新连接</button></div>
  <div v-else-if="loading" class="empty-state"><span class="spinner" /></div>
  <div v-else-if="!filtered.length" class="empty-state paper"><div class="empty-symbol"><Icon name="wiki" :size="35" /></div><h2>{{ query ? '还没有找到这个主题' : '暂无知识页面' }}</h2><p>{{ query ? '换一个关键词，或收藏相关的新内容。' : '保存内容并完成分析后，系统会根据资料创建或更新 Wiki。每一节都保留引用。' }}</p><button v-if="!query" class="button primary" @click="composerOpen = true">收藏一份内容<Icon name="plus" :size="17" /></button></div>
  <div v-else class="wiki-grid"><RouterLink v-for="page in filtered" :key="page.id" :to="'/wiki/' + page.id" class="wiki-card"><div class="wiki-card-heading"><span class="wiki-page-icon"><Icon name="wiki" :size="25" /></span><span class="subtle">v{{ page.version }}</span></div><h2>{{ page.title }}</h2><p>{{ page.generated.replace(/[#*\[\]()>]/g, '').slice(0, 150) }}</p><div class="card-footer"><span>{{ page.source_ids.length }} 份证据片段</span><time>{{ date(page.updated_at) }}</time></div></RouterLink></div>
</template>
