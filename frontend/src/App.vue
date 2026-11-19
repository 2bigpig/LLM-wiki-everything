<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type Capabilities } from './api'
import { composerOpen, notifications, refreshVersion } from './state'
import Icon from './components/Icon.vue'
import Composer from './features/library/Composer.vue'

const route = useRoute(), router = useRouter()
const overview = ref({ records: 0, pages: 0, relations: 0, pending: 0 })
const capabilities = ref<Capabilities | null>(null)
const search = ref(''), searchInput = ref<HTMLInputElement>()
const heading = computed(() => route.path.startsWith('/wiki') ? '知识 Wiki'
  : route.path.startsWith('/tasks') ? '整理进度' : route.path.startsWith('/settings') ? '偏好与服务'
    : route.path.startsWith('/search') ? '找回知识' : '我的收藏')
const navigation = [
  { path: '/', label: '我的收藏', icon: 'library' }, { path: '/wiki', label: '知识 Wiki', icon: 'wiki' },
  { path: '/tasks', label: '整理进度', icon: 'tasks' },
]
async function load() {
  try {
    if (sessionStorage.getItem('wiki-access-token')) await api('/session', { method: 'POST' })
    const [stats, caps] = await Promise.all([api<typeof overview.value>('/overview'), api<Capabilities>('/capabilities')])
    overview.value = stats; capabilities.value = caps
  } catch { /* 页面自身显示连接或口令错误，侧栏不重复弹出通知。 */ }
}
function submitSearch() { if (search.value.trim()) void router.push({ path: '/search', query: { q: search.value.trim() } }) }
function shortcut(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault(); searchInput.value?.focus()
  }
}
let timer: ReturnType<typeof setInterval>
onMounted(() => { void load(); timer = setInterval(() => { if (!document.hidden) void load() }, 6000); window.addEventListener('keydown', shortcut) })
onUnmounted(() => { clearInterval(timer); window.removeEventListener('keydown', shortcut) })
watch(refreshVersion, load)
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <RouterLink to="/" class="brand" aria-label="知间首页"><span class="brand-mark">知<span>·</span></span><span>知间<small>让知识持续生长</small></span></RouterLink>
      <div class="workspace-label"><span class="avatar">我</span><span>我的知识空间<small>个人知识库</small></span><span class="workspace-dot" /></div>
      <p class="nav-label">知识空间</p>
      <nav aria-label="主导航">
        <RouterLink v-for="item in navigation" :key="item.path" :to="item.path" class="nav-item"
          :class="{ active: item.path === '/' ? route.path === '/' || route.path.startsWith('/records') : route.path.startsWith(item.path) }">
          <Icon :name="item.icon" /><span>{{ item.label }}</span>
          <span v-if="item.path === '/'" class="nav-count">{{ overview.records }}</span>
          <span v-if="item.path === '/tasks' && overview.pending" class="nav-count">{{ overview.pending }}</span>
        </RouterLink>
      </nav>
      <div class="sidebar-note"><Icon name="branch" :size="26" /><p>留下重要的，<br />连接有用的。</p><span>每一条收藏，都是知识的起点。</span></div>
      <div class="sidebar-bottom">
        <RouterLink to="/settings" class="service-card"><span class="service-dot" :class="{ ready: capabilities?.llm }" /><span>{{ capabilities?.llm ? '自动整理已配置' : '配置自动整理' }}<small>{{ capabilities?.llm ? capabilities.llm_model : '连接你的模型服务' }}</small></span><Icon name="arrow" :size="16" /></RouterLink>
        <RouterLink to="/settings" class="nav-item" aria-label="设置与用量" :class="{ active: route.path === '/settings' }"><Icon name="settings" /><span>设置与用量</span></RouterLink>
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar"><span class="breadcrumb">知识空间 <span>/</span> <strong>{{ heading }}</strong></span>
        <form class="global-search" @submit.prevent="submitSearch"><Icon name="search" :size="17" /><input ref="searchInput" v-model="search" aria-label="搜索知识库" placeholder="搜索资料、概念与想法…" /><kbd>Ctrl K</kbd></form>
        <button class="button primary compact" @click="composerOpen = true"><Icon name="plus" :size="17" /><span>记录内容</span></button>
      </header>
      <main class="page"><RouterView /></main>
      <footer class="page-footer"><span>知间 · 让收藏成为自己的知识</span><span>保留原始表达，追溯每一份来源。</span></footer>
    </div>
    <Composer v-if="composerOpen" @close="composerOpen = false" />
    <div class="notifications" aria-live="polite"><div v-for="item in notifications" :key="item.id" class="notification" :class="{ error: item.error }">{{ item.text }}</div></div>
  </div>
</template>
