<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, date, post, stageLabels, type Run } from '../../api'
import { invalidate, notify } from '../../state'
import Icon from '../../components/Icon.vue'
import StatusBadge from '../../components/StatusBadge.vue'
const runs = ref<Run[]>([]), error = ref(''), busy = ref('')
const active = computed(() => runs.value.filter(r => ['queued', 'running'].includes(r.status)).length)
async function load() {
  try { runs.value = (await api<{ items: Run[] }>('/runs')).items; error.value = '' }
  catch (e) { error.value = (e as Error).message }
}
async function action(run: Run, cancel: boolean) {
  if (cancel && !window.confirm('停止后续整理步骤？当前正在执行的步骤可能仍需完成。')) return
  busy.value = run.id
  try { await post(cancel ? '/runs/' + run.id + '/cancel' : '/records/' + run.record_id + '/retry'); invalidate(); await load(); notify(cancel ? '已请求停止后续步骤。' : '已提交重试。') }
  catch (e) { notify((e as Error).message, true) }
  finally { busy.value = '' }
}
let timer: ReturnType<typeof setInterval>
onMounted(() => { void load(); timer = setInterval(() => { if (!document.hidden) void load() }, 4000) })
onUnmounted(() => clearInterval(timer))
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">后台任务</span><h1>整理进度</h1><p>查看读取、分析与 Wiki 更新的执行状态</p></div><span class="heading-counter">{{ active }}<small>个任务正在排队或处理</small></span></div>
  <div v-if="error" class="error-panel"><p>{{ error }}</p><button class="button" @click="load">重新连接</button></div>
  <div v-else-if="!runs.length" class="empty-state paper"><Icon name="tasks" :size="38" /><h2>一切准备就绪</h2><p>保存第一条内容后，整理进度会出现在这里。</p></div>
  <div v-else class="paper task-table-wrapper"><table class="task-table"><thead><tr><th>整理内容</th><th>当前步骤</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="run in runs" :key="run.id"><td><RouterLink :to="'/records/' + run.record_id" class="task-title">{{ run.record_title }}</RouterLink><small>{{ date(run.created_at) }} · 原件 v{{ run.version }} · 执行 {{ run.attempts }} 次</small><p v-if="run.error" class="task-error">{{ run.error }}</p></td><td>{{ stageLabels[run.stage] || run.stage }}</td><td><StatusBadge :status="run.status" /></td><td><button v-if="['queued', 'running'].includes(run.status)" class="button small" :disabled="busy === run.id" @click="action(run, true)">停止</button><button v-else-if="['failed', 'waiting_configuration', 'budget_exceeded', 'cancelled'].includes(run.status)" class="button small" :disabled="busy === run.id" @click="action(run, false)">重试</button><span v-else class="subtle">已保留结果</span></td></tr></tbody></table></div>
</template>
