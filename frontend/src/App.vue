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
const mobileMenu = ref<HTMLDialogElement>()
const heading = computed(() => route.path.startsWith('/wiki') ? '知识 Wiki'
  : route.path.startsWith('/tasks') ? '整理进度' : route.path.startsWith('/settings') ? '设置与用量'
  : route.path.startsWith('/search') ? '搜索资料' : route.path.startsWith('/graph') ? '知识图谱'
    : route.path.startsWith('/reviews') ? '发现与回顾' : '我的收藏')
const navigation = [
  { path: '/', label: '我的收藏', icon: 'library' }, { path: '/wiki', label: '知识 Wiki', icon: 'wiki' },
  { path: '/tasks', label: '整理进度', icon: 'tasks' },
  { path: '/graph', label: '知识图谱', icon: 'branch' },
  { path: '/reviews', label: '发现与回顾', icon: 'clock' },
]
async function load() {
  try {
    const [stats, caps] = await Promise.all([api<typeof overview.value>('/overview'), api<Capabilities>('/capabilities')])
    overview.value = stats; capabilities.value = caps
  } catch { /* 页面自身显示连接错误，侧栏不重复弹出通知。 */ }
}
function submitSearch() { if (search.value.trim()) void router.push({ path: '/search', query: { q: search.value.trim() } }) }
function isActive(path: string) {
  return path === '/' ? route.path === '/' || route.path.startsWith('/records') : route.path.startsWith(path)
}
// 手机使用原生 dialog 管理焦点与 Escape，路由切换后自动关闭导航。
watch(() => route.fullPath, () => mobileMenu.value?.close())
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
      <RouterLink to="/" class="brand" aria-label="everything首页"><span class="brand-mark" aria-hidden="true"><Icon name="archive" :size="24" /></span><span>everything</span><span class="brand-caption">个人知识库</span></RouterLink>
      <div class="workspace-label"><span class="workspace-initial" aria-hidden="true">e</span><div>我的知识空间<small>收藏 · 整理 · 追溯</small></div></div>
      <p class="nav-label">资料库</p>
      <nav aria-label="主导航">
        <RouterLink v-for="item in navigation" :key="item.path" :to="item.path" class="nav-item" :aria-label="item.label"
          :class="{ active: isActive(item.path) }" :aria-current="isActive(item.path) ? 'page' : undefined">
          <Icon :name="item.icon" /><span>{{ item.label }}</span>
          <span v-if="item.path === '/'" class="nav-count">{{ overview.records }}</span>
          <span v-if="item.path === '/tasks' && overview.pending" class="nav-count">{{ overview.pending }}</span>
        </RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <RouterLink to="/settings" class="service-card"><span class="service-dot" :class="{ ready: capabilities?.llm }" /><span>{{ capabilities?.llm ? '自动整理已配置' : '配置自动整理' }}<small>{{ capabilities?.llm ? capabilities.llm_model : '连接你的模型服务' }}</small></span><Icon name="arrow" :size="16" /></RouterLink>
        <RouterLink to="/settings" class="nav-item" aria-label="设置与用量" :class="{ active: route.path === '/settings' }" :aria-current="isActive('/settings') ? 'page' : undefined"><Icon name="settings" /><span>设置与用量</span></RouterLink>
        <p class="sidebar-caption">原始资料与每次思考，分别保留</p>
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar"><button class="icon-button menu-trigger" aria-label="打开导航" aria-haspopup="dialog" @click="mobileMenu?.showModal()"><Icon name="menu" /></button><span class="breadcrumb"><span class="breadcrumb-root">资料库 <span>/</span></span> <strong>{{ heading }}</strong></span>
        <form class="global-search" role="search" @submit.prevent="submitSearch"><Icon name="search" /><input ref="searchInput" v-model="search" aria-label="搜索知识库" placeholder="搜索收藏与知识" /><kbd>Ctrl K</kbd></form>
        <button class="button primary compact" @click="composerOpen = true"><Icon name="plus" :size="17" /><span>记录内容</span></button>
      </header>
      <main class="page"><RouterView /></main>
      <footer class="page-footer"><span>everything · 个人知识库</span><span>保留原文，连接知识</span></footer>
    </div>
    <dialog ref="mobileMenu" class="mobile-menu" aria-label="页面导航" @click="event => { if (event.target === mobileMenu) mobileMenu.close() }">
      <div class="section-title"><span class="brand"><Icon name="archive" /><span>everything</span></span><button class="icon-button" aria-label="关闭导航" @click="mobileMenu?.close()"><Icon name="close" /></button></div>
      <p class="nav-label">资料库</p><nav aria-label="手机主导航"><RouterLink v-for="item in navigation" :key="item.path" :to="item.path" class="nav-item" :class="{ active: isActive(item.path) }" :aria-current="isActive(item.path) ? 'page' : undefined" @click="mobileMenu?.close()"><Icon :name="item.icon" /><span>{{ item.label }}</span></RouterLink><RouterLink to="/settings" class="nav-item" :class="{ active: isActive('/settings') }" :aria-current="isActive('/settings') ? 'page' : undefined" @click="mobileMenu?.close()"><Icon name="settings" /><span>设置与用量</span></RouterLink></nav>
    </dialog>
    <Composer v-if="composerOpen" @close="composerOpen = false" />
    <div class="notifications" aria-live="polite"><div v-for="item in notifications" :key="item.id" class="notification" :class="{ error: item.error }">{{ item.text }}</div></div>
  </div>
</template>
