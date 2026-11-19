<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, date, post, type Reviews, type Source } from '../../api'
import { notify } from '../../state'
import Icon from '../../components/Icon.vue'

const router = useRouter(), root = ref<HTMLElement>()
const data = ref<Reviews | null>(null), error = ref(''), loading = ref(true), busy = ref('')
const blocked = ref<{ resource_id: string; label: string }[]>([])
const seen = new Set<string>()
let observer: IntersectionObserver | undefined
async function load() {
  loading.value = true
  try {
    data.value = await api<Reviews>('/reviews'); error.value = ''
    await nextTick()
    observer?.disconnect()
    root.value?.querySelectorAll<HTMLElement>('[data-card-id]').forEach(element => observer?.observe(element))
  } catch (e) { error.value = (e as Error).message }
  finally { loading.value = false }
}
async function preference(values: { enabled?: boolean; pause?: boolean; resume?: boolean }) {
  try { await api('/reviews/preferences', { method: 'PATCH', body: JSON.stringify(values) }); await load() }
  catch (e) { notify((e as Error).message, true) }
}
async function feedback(id: string, action: string) {
  if (busy.value) return
  busy.value = id
  try { await post('/reviews/' + id + '/feedback', { action }); await load() }
  catch (e) { notify((e as Error).message, true) }
  finally { busy.value = '' }
}
async function markSeen(id: string) {
  if (seen.has(id)) return
  seen.add(id)
  try { await post('/reviews/' + id + '/feedback', { action: 'seen' }) }
  catch { seen.delete(id) }
}
async function loadBlocked() {
  try { blocked.value = (await api<{ items: typeof blocked.value }>('/reviews/blocked')).items }
  catch (e) { notify((e as Error).message, true) }
}
async function restore(id: string) {
  try { await post('/reviews/resources/' + id + '/restore'); await loadBlocked() }
  catch (e) { notify((e as Error).message, true) }
}
async function openSource(id: string) {
  try { const source = await api<Source>('/sources/' + id); await router.push('/records/' + source.record_id + '#source-' + id) }
  catch (e) { notify((e as Error).message, true) }
}
onMounted(() => {
  // 只将真正进入视口的卡片记为已展示，不能把尚未看到的条目消耗掉。
  observer = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      const id = (entry.target as HTMLElement).dataset.cardId
      if (id) void markSeen(id)
    }
  }, { threshold: 0.25 })
  void load()
})
onUnmounted(() => observer?.disconnect())
</script>

<template>
  <div class="page-heading"><div><span class="eyebrow">REDISCOVER WHAT MATTERS</span><h1>发现与回顾</h1><p>看看旧内容，在今天是否有了新的意义。</p></div><button class="button" :disabled="loading" @click="load">刷新</button></div>
  <section class="paper review-preferences"><div><strong>每日站内回顾</strong><p class="subtle">{{ data?.timezone || '加载中' }} · {{ data?.daily_time || '—' }} 后由后台生成，每天最多 {{ data?.daily_limit || '—' }} 条。不会发送外部通知。</p><p v-if="data?.preferences.paused_until" class="notice">已暂停至 {{ date(data.preferences.paused_until) }}。</p><p v-if="data && !data.configured_enabled" class="notice">部署配置已关闭每日回顾。</p></div><div class="button-row"><label v-if="data"><input type="checkbox" :checked="data.preferences.enabled" @change="preference({ enabled: ($event.target as HTMLInputElement).checked })" />启用回顾</label><button class="button small" @click="preference({ pause: true })">暂停 {{ data?.pause_days || '—' }} 天</button><button v-if="data?.preferences.paused_until" class="button small" @click="preference({ resume: true })">恢复回顾</button></div></section>
  <p v-if="error" class="error-message" role="alert">{{ error }}</p>
  <section ref="root" class="review-grid" :aria-busy="loading"><article v-for="card in data?.items" :key="card.id" :data-card-id="card.id" class="paper review-card"><div class="section-title"><span class="topic">{{ card.kind === 'related' ? '新旧知识相遇' : card.kind === 'revisit' ? '再次关注' : '重新发现' }}</span><Icon name="clock" :size="18" /></div><RouterLink :to="'/records/' + card.record_id"><h2>{{ card.title }}</h2></RouterLink><p>{{ card.reason }}</p><div class="button-row"><RouterLink class="text-link" :to="{ path: '/graph', query: { focus: 'resource:' + card.resource_id } }">查看收藏时间线</RouterLink><button v-for="evidence in card.evidence.sources || []" :key="evidence.source_id" class="text-link" @click="openSource(evidence.source_id)">核对依据</button></div><div class="review-actions"><button class="button small" :disabled="!!busy" @click="feedback(card.id, 'helpful')">{{ card.feedback === 'helpful' ? '已标记有帮助' : '有帮助' }}</button><button class="button small" :disabled="!!busy" @click="feedback(card.id, 'snooze')">暂不需要</button><button class="text-link" :disabled="!!busy" @click="feedback(card.id, 'inaccurate')">不准确</button><button class="text-link" :disabled="!!busy" @click="feedback(card.id, 'never')">不再推荐</button></div></article></section>
  <div v-if="!loading && !data?.items.length" class="empty-state paper"><Icon name="clock" :size="34" /><h2>今天暂时没有新的回顾</h2><p>达到回顾时间、后台运行且有符合条件的旧内容时，会显示在这里。<br />资料较新时，先继续收藏，不会为了凑数编造推荐。</p></div>
  <details class="paper blocked-resources" @toggle="($event.target as HTMLDetailsElement).open && loadBlocked()"><summary>管理不再推荐的内容</summary><p v-if="!blocked.length" class="subtle">没有被排除的内容。</p><div v-for="item in blocked" :key="item.resource_id"><span>{{ item.label }}</span><button class="button small" @click="restore(item.resource_id)">恢复推荐</button></div></details>
</template>
