<script setup lang="ts">
/** 调度仅修改持久化偏好，不发送付费测试，不影响当前模型选择。 */
import { onMounted, ref } from 'vue'
import { api } from '../../api'
import { invalidate, notify } from '../../state'
interface Policy { enabled: boolean; start: string; end: string; timezone: string; end_policy: 'finish_batch' | 'wait_next_window' }
interface Schedule { policy: Policy; open_now: boolean; next_window_at: string }
const policy = ref<Policy | null>(null), saved = ref<Schedule | null>(null), error = ref(''), busy = ref(false)
async function load() {
  try { const data = await api<Schedule>('/model-settings/schedule'); saved.value = data; policy.value = { ...data.policy }; error.value = '' }
  catch (e) { error.value = (e as Error).message }
}
async function save() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    saved.value = await api<Schedule>('/model-settings/schedule', { method: 'PUT', body: JSON.stringify(policy.value) })
    policy.value = { ...saved.value.policy }; invalidate(); notify('LLM 执行时间已保存，等待任务将按新策略调度。')
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function nextTime() {
  if (!saved.value) return ''
  return new Intl.DateTimeFormat('zh-CN', { timeZone: saved.value.policy.timezone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(saved.value.next_window_at))
}
onMounted(load)
</script>
<template>
  <section class="paper schedule-card" aria-label="LLM 执行时间">
    <div class="section-title"><h2>LLM 执行时间</h2><span v-if="saved" class="subtle">{{ !saved.policy.enabled ? '立即处理' : saved.open_now ? '当前处于执行时段' : `下次 ${nextTime()} · ${saved.policy.timezone}` }}</span></div>
    <p class="subtle">统一安排分析、关联、Wiki 和深层回顾，利用厂商优惠时段；保存资料和本地模型处理照常进行</p>
    <form v-if="policy" @submit.prevent="save">
      <fieldset :disabled="busy"><label class="schedule-toggle"><input v-model="policy.enabled" type="checkbox" />按每日优惠时段运行</label>
        <template v-if="policy.enabled">
          <div class="schedule-fields"><label class="field">开始时间<input v-model="policy.start" type="time" required /></label><label class="field">结束时间<input v-model="policy.end" type="time" required /></label><label class="field">时区<input v-model="policy.timezone" required list="schedule-zones" placeholder="Asia/Shanghai" /></label></div>
          <datalist id="schedule-zones"><option value="Asia/Shanghai" /><option value="UTC" /></datalist>
          <label class="field">结束时的处理方式<select v-model="policy.end_policy" aria-label="结束时的处理方式"><option value="finish_batch">本批全部完成（默认）</option><option value="wait_next_window">时段外等待</option></select></label>
          <p class="subtle">{{ policy.end_policy === 'finish_batch' ? '时段内进入本批的任务会继续完成；结束后新提交的任务等待下次。' : '结束后不再发起新请求；在途请求正常完成，剩余步骤等待下次。' }} 支持跨午夜，以所填时区为准。</p>
        </template>
      </fieldset>
      <div v-if="error" class="error-panel" role="alert">{{ error }}</div>
      <div class="schedule-actions"><button class="button" :disabled="busy">{{ busy ? '保存中…' : '保存执行时间' }}</button><span class="subtle">模型测试始终即时执行；实际折扣以厂商规则为准</span></div>
    </form>
    <div v-else-if="error" class="error-panel"><p>{{ error }}</p><button class="button" @click="load">重新读取</button><p>更新项目后请重启后端再刷新</p></div>
  </section>
</template>
<style scoped>
.schedule-card { padding:24px; margin-top:24px; }.section-title { flex-wrap:wrap; gap:8px; }.schedule-card > .subtle { margin-bottom:20px; }fieldset { padding:0; margin:0; border:0; min-width:0; }.schedule-toggle { display:flex; align-items:center; gap:8px; font-size:14px; margin-bottom:20px; }.schedule-toggle input { width:auto; }.schedule-fields { display:grid; grid-template-columns:1fr 1fr 2fr; gap:16px; }.schedule-actions { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:20px; }.schedule-actions .subtle { font-size:12px; }
.schedule-fields input { min-width:0; }
@media(max-width:600px) { .schedule-card { padding:20px; margin-top:20px; }.schedule-fields { grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 12px; }.schedule-fields > :last-child { grid-column:1 / -1; } }
</style>
