<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, type CaptureHistory, type Recognition, type RecordItem } from '../../api'
import { invalidate, notify } from '../../state'
import Icon from '../../components/Icon.vue'

const emit = defineEmits<{ close: [] }>()
const router = useRouter(), dialog = ref<HTMLDialogElement>(), editor = ref<HTMLTextAreaElement>()
const picker = ref<HTMLInputElement>(), text = ref(''), title = ref(''), notes = ref(''), purpose = ref('auto')
const files = ref<File[]>([]), excluded = ref<string[]>([]), recognition = ref<Recognition | null>(null)
const busy = ref(false), error = ref(''), recognizing = ref(false)
const includedCount = computed(() => recognition.value?.links.filter(link => !excluded.value.includes(link.url)).length || 0)
const canSave = computed(() => !!(text.value.trim() || files.value.length) && !busy.value)
let submissionKey = crypto.randomUUID(), timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined, sequence = 0

// 草稿变化代表新的提交意图；相同草稿的网络重试继续使用同一个幂等键。
watch([text, title, notes, purpose, files, excluded], () => {
  if (!busy.value) submissionKey = crypto.randomUUID()
}, { flush: 'sync' })
watch(text, () => { clearTimeout(timer); timer = setTimeout(() => { void recognizeNow() }, 300) })

async function recognizeNow(): Promise<boolean> {
  controller?.abort(); controller = new AbortController()
  const current = ++sequence
  recognizing.value = true
  try {
    const result = await api<Recognition>('/captures/recognize', {
      method: 'POST', body: JSON.stringify({ text: text.value }), signal: controller.signal,
    })
    if (current !== sequence) return false
    recognition.value = result
    excluded.value = excluded.value.filter(url => result.links.some(link => link.url === url))
    return true
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') return false
    if (current === sequence) error.value = (e as Error).message
    return false
  } finally { if (current === sequence) recognizing.value = false }
}
function addFiles(incoming: File[]) { files.value = [...files.value, ...incoming] }
function choose(event: Event) {
  const input = event.target as HTMLInputElement
  addFiles(Array.from(input.files || [])); input.value = ''
}
function drop(event: DragEvent) {
  if (busy.value) return
  const incoming = Array.from(event.dataTransfer?.files || [])
  if (incoming.length) addFiles(incoming)
  else {
    const value = event.dataTransfer?.getData('text/plain') || ''
    if (value) text.value += (text.value ? '\n' : '') + value
  }
}
function paste(event: ClipboardEvent) {
  const incoming = Array.from(event.clipboardData?.files || [])
  if (!incoming.length) return
  event.preventDefault(); addFiles(incoming)
  const value = event.clipboardData?.getData('text/plain') || ''
  if (value) {
    const start = editor.value?.selectionStart ?? text.value.length
    const end = editor.value?.selectionEnd ?? start
    text.value = text.value.slice(0, start) + value + text.value.slice(end)
  }
}
function toggle(url: string, checked: boolean) {
  excluded.value = checked ? excluded.value.filter(item => item !== url) : [...excluded.value, url]
}
function close() { if (!busy.value) emit('close') }
async function save() {
  if (!canSave.value) return
  error.value = ''; busy.value = true; clearTimeout(timer)
  try {
    if (!await recognizeNow()) return
    if (includedCount.value > recognition.value!.max_links)
      throw new Error(`一次最多自动读取 ${recognition.value!.max_links} 个链接，请取消部分链接的读取。`)
    if (files.value.length > recognition.value!.max_attachments)
      throw new Error(`一次最多添加 ${recognition.value!.max_attachments} 个附件。`)
    const form = new FormData()
    for (const file of files.value) form.append('files', file)
    for (const [key, value] of Object.entries({ text: text.value, title: title.value,
      notes: notes.value, purpose: purpose.value, excluded_urls: JSON.stringify(excluded.value) })) form.append(key, value)
    const result = await api<RecordItem & { history_matches: CaptureHistory[] }>('/captures', {
      method: 'POST', body: form, headers: { 'Idempotency-Key': submissionKey },
    })
    invalidate()
    notify(result.history_matches.length ? '已保存。这份内容你以前也收藏过，可在详情查看当时的记录。' : '已保存，后台会自动读取与整理。')
    emit('close'); void router.push('/records/' + result.id)
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
onMounted(async () => { await nextTick(); dialog.value?.showModal(); editor.value?.focus(); void recognizeNow() })
onUnmounted(() => { clearTimeout(timer); controller?.abort(); dialog.value?.close() })
</script>

<template>
  <dialog ref="dialog" class="compose-dialog" @cancel.prevent="close" @click="e => { if (e.target === dialog) close() }">
    <form @submit.prevent="save" @dragover.prevent @drop.prevent="drop">
      <div class="dialog-heading"><div><span class="eyebrow">CAPTURE WHAT MATTERS</span><h2>留下一份新知识</h2></div><button type="button" class="icon-button" aria-label="关闭" :disabled="busy" @click="close"><Icon name="close" /></button></div>
      <fieldset class="capture-fields" :disabled="busy">
        <label class="field">内容<textarea ref="editor" v-model="text" rows="7" maxlength="200000" placeholder="写下想法，粘贴一个或多个链接，也可以粘贴截图或拖入文件…" @paste="paste" /></label>
        <div class="capture-tools"><button type="button" class="button" @click="picker?.click()"><Icon name="plus" :size="16" />添加附件</button><span class="subtle">{{ recognizing ? '正在识别…' : `已识别 ${includedCount} 个待读链接 · ${files.length} 个附件` }}</span></div>
        <input ref="picker" class="capture-file-input" type="file" multiple aria-label="选择上传文件" accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.flac,.ogg,.mp4,.webm,.mkv,.mov" @change="choose" />
        <ul v-if="files.length" class="capture-files"><li v-for="(file, index) in files" :key="index"><Icon name="file" :size="16" /><span>{{ file.name }}</span><small>{{ (file.size / 1024).toFixed(1) }} KB</small><button type="button" class="icon-button" :aria-label="'移除附件 ' + file.name" @click="files = files.filter((_, i) => i !== index)"><Icon name="close" :size="15" /></button></li></ul>
        <div v-if="recognition?.links.length" class="capture-links"><span class="subtle">自动读取这些来源，可取消；原始输入始终保留。</span><label v-for="link in recognition.links" :key="link.url"><input type="checkbox" :checked="!excluded.includes(link.url)" @change="toggle(link.url, ($event.target as HTMLInputElement).checked)" /><span>{{ link.kind === 'video' ? '视频' : '网页' }} · {{ link.url }}</span></label></div>
        <p v-for="warning in recognition?.warnings" :key="warning" class="notice">{{ warning }}</p>
        <details class="capture-options"><summary>更多选项</summary><label class="field">标题 <span class="optional">选填</span><input v-model="title" maxlength="300" placeholder="不填会根据内容命名" /></label><div class="form-grid"><label class="field">整理用途<select v-model="purpose"><option value="auto">自动判断</option><option value="learning">学习资料</option><option value="interview">面试复盘</option><option value="note">个人随笔</option></select></label><label class="field">为什么收藏 <span class="optional">选填</span><input v-model="notes" maxlength="20000" placeholder="此刻的想法，也许与上次不同" /></label></div></details>
      </fieldset>
      <p v-if="error" class="error-message" role="alert">{{ error }}</p>
      <div class="dialog-footer"><span><Icon name="spark" :size="16" />一次留下，保留每一份来源</span><button class="button primary" :disabled="!canSave"><span v-if="busy" class="spinner" />{{ busy ? '正在保存…' : '保存到知识库' }}<Icon name="arrow" :size="17" /></button></div>
    </form>
  </dialog>
</template>
