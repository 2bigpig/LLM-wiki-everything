<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type RecordItem } from '../../api'
import { invalidate, notify } from '../../state'
import Icon from '../../components/Icon.vue'

const emit = defineEmits<{ close: [] }>()
const router = useRouter(), dialog = ref<HTMLDialogElement>()
const mode = ref('link'), title = ref(''), content = ref(''), url = ref(''), notes = ref(''), purpose = ref('auto')
const files = ref<File[]>([]), busy = ref(false), error = ref('')
const submissionKey = crypto.randomUUID()
function choose(event: Event) { files.value = Array.from((event.target as HTMLInputElement).files || []) }
function drop(event: DragEvent) { files.value = Array.from(event.dataTransfer?.files || []); mode.value = 'file' }
function close() { if (!busy.value) emit('close') }
async function save() {
  error.value = ''; busy.value = true
  try {
    let result: RecordItem
    if (mode.value === 'file') {
      if (!files.value.length) throw new Error('请选择需要上传的文件。')
      const form = new FormData()
      for (const file of files.value) form.append('files', file)
      for (const [key, value] of Object.entries({ title: title.value, content: content.value, notes: notes.value, purpose: purpose.value })) form.append(key, value)
      result = await api('/records/upload', { method: 'POST', body: form, headers: { 'Idempotency-Key': submissionKey } })
    } else {
      result = await api('/records', { method: 'POST', headers: { 'Idempotency-Key': submissionKey }, body: JSON.stringify({
        title: title.value, content: content.value, url: mode.value === 'link' ? url.value : null,
        notes: notes.value, purpose: purpose.value,
      }) })
    }
    invalidate(); notify('已保存，后台会自动读取与整理。'); emit('close'); void router.push('/records/' + result.id)
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
onMounted(async () => { await nextTick(); dialog.value?.showModal() })
onUnmounted(() => dialog.value?.close())
</script>
<template>
  <dialog ref="dialog" class="compose-dialog" @cancel.prevent="close" @click="e => { if (e.target === dialog) close() }">
    <form @submit.prevent="save">
      <div class="dialog-heading"><div><span class="eyebrow">CAPTURE SOMETHING VALUABLE</span><h2>留下一份新知识</h2></div><button type="button" class="icon-button" aria-label="关闭" @click="close"><Icon name="close" /></button></div>
      <div class="compose-tabs"><button v-for="tab in [{ id: 'link', label: '链接收藏', icon: 'link' }, { id: 'text', label: '随手记', icon: 'note' }, { id: 'file', label: '上传素材', icon: 'upload' }]" :key="tab.id" type="button" :class="{ selected: mode === tab.id }" @click="mode = tab.id"><Icon :name="tab.icon" :size="18" />{{ tab.label }}</button></div>
      <label v-if="mode === 'link'" class="field">内容链接<input v-model="url" type="url" required placeholder="https://… 粘贴你想收藏的内容" autofocus /></label>
      <div v-if="mode === 'file'" class="upload-zone" @dragover.prevent @drop.prevent="drop"><Icon name="upload" :size="30" /><strong>拖放文件，或点击选择</strong><span>文档、图片、音频、视频 · 一次最多 10 个</span><input type="file" multiple aria-label="选择上传文件" accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.flac,.ogg,.mp4,.webm,.mkv,.mov" @change="choose" /></div>
      <p v-if="files.length && mode === 'file'" class="file-selection">{{ files.map(f => f.name).join('、') }}</p>
      <label class="field">标题 <span class="optional">选填</span><input v-model="title" maxlength="300" placeholder="给这份内容起个名字" /></label>
      <label class="field">{{ mode === 'text' ? '此刻的想法' : '补充原文或文字说明' }} <span v-if="mode !== 'text'" class="optional">选填</span><textarea v-model="content" :required="mode === 'text'" :rows="mode === 'text' ? 7 : 3" maxlength="200000" placeholder="一个观点、一次经历，或还没想明白的问题…" /></label>
      <div class="form-grid"><label class="field">整理用途<select v-model="purpose"><option value="auto">自动判断</option><option value="learning">学习资料</option><option value="interview">面试复盘</option><option value="note">个人随笔</option></select></label><label class="field">为什么收藏 <span class="optional">选填</span><input v-model="notes" maxlength="20000" placeholder="值得留下的原因或整理重点" /></label></div>
      <p v-if="error" class="error-message" role="alert">{{ error }}</p>
      <div class="dialog-footer"><span><Icon name="spark" :size="16" />保存后自动阅读、分析与关联</span><button class="button primary" :disabled="busy"><span v-if="busy" class="spinner" />{{ busy ? '正在保存…' : '保存到知识库' }}<Icon name="arrow" :size="17" /></button></div>
    </form>
  </dialog>
</template>
