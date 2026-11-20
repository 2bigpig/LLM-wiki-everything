<script setup lang="ts">
/** 管理页只拿脱敏配置；密钥输入不会进入浏览器存储或列表状态。 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, post } from '../../api'
import { invalidate, notify } from '../../state'
const emit = defineEmits<{ changed: [] }>()

interface Connection { id: string; name: string; provider: string; protocol: string; base_url: string; enabled: boolean; key_configured: boolean }
interface Profile { id: string; connection_id: string; name: string; model: string; kind: string; enabled: boolean; revision_id: string; timeout_seconds: number; max_output_tokens: number; temperature: number | null; thinking: string; api_version: string; probes: { kind: string; succeeded: boolean; detail: string; created_at: string }[] }
interface Space { id: string; model_name: string; model: string; revision_id: string; status: string; indexed: number; total: number; error: string | null; activate_when_ready: boolean }
interface Preset { id: string; name: string; protocol: string; base_url: string }
interface Overview { connections: Connection[]; models: Profile[]; bindings: Record<string, string | null>; active_space_id: string | null; spaces: Space[]; presets: Preset[]; credential_ready: boolean; defaults: { timeout_seconds: number; max_output_tokens: number } }
const data = ref<Overview | null>(null), error = ref(''), busy = ref(false), polling = ref(false)
const connectionDialog = ref<HTMLDialogElement>(), modelDialog = ref<HTMLDialogElement>()
const connectionId = ref(''), modelId = ref(''), replaceKey = ref(false), key = ref('')
const connection = ref({ name: '', provider: 'custom', protocol: 'openai', base_url: '', enabled: true })
const model = ref({ connection_id: '', name: '', model: '', kind: 'llm', enabled: true, timeout_seconds: 180, max_output_tokens: 2400, temperature: null as number | null, thinking: 'default', api_version: '2024-10-21' })
const bindings = ref<Record<string, string | null>>({}), spaceModel = ref(''), confirmSpace = ref('')
const stages = [{ id: 'default', name: '默认模型' }, { id: 'analyze', name: '分析与综合' }, { id: 'relate', name: '内容关联' }, { id: 'wiki', name: 'Wiki 生成' }, { id: 'revisit', name: '深层回顾' }]
const protocols = ['openai', 'azure', 'anthropic', 'google', 'deepseek', 'bge']
const llms = computed(() => data.value?.models.filter(m => m.kind === 'llm') || [])
function available(profile: Profile) { return profile.enabled && !!data.value?.connections.find(c => c.id === profile.connection_id)?.enabled }
const embeddings = computed(() => data.value?.models.filter(m => m.kind === 'embedding' && m.enabled) || [])
let timer: ReturnType<typeof setInterval> | undefined
async function load(quiet = false) {
  try { const result = await api<Overview>('/model-settings'); data.value = result; if (!quiet) bindings.value = Object.fromEntries(stages.map(s => [s.id, result.bindings[s.id] || null])); error.value = '' }
  catch (e) { if (!quiet) error.value = (e as Error).message }
}
async function perform(action: () => Promise<unknown>, message: string) {
  if (busy.value) return
  busy.value = true
  try { await action(); notify(message); await load(); emit('changed'); invalidate(); return true }
  catch (e) { notify((e as Error).message, true); return false }
  finally { busy.value = false }
}
function preset() {
  const selected = data.value?.presets.find(p => p.id === connection.value.provider)
  if (selected) { connection.value.protocol = selected.protocol; connection.value.base_url = selected.base_url; if (!connectionId.value) connection.value.name = selected.name }
}
function openConnection(row?: Connection, copy = false) {
  connectionId.value = copy ? '' : row?.id || ''; key.value = ''; replaceKey.value = false
  connection.value = row ? { name: row.name + (copy ? ' 副本' : ''), provider: row.provider, protocol: row.protocol, base_url: row.base_url, enabled: row.enabled } : { name: '', provider: 'custom', protocol: 'openai', base_url: '', enabled: true }
  if (!row) preset()
  connectionDialog.value?.showModal()
}
async function saveConnection() {
  const payload = { ...connection.value, api_key: !connectionId.value || replaceKey.value ? key.value : null }
  const saved = await perform(() => connectionId.value ? api('/model-settings/connections/' + connectionId.value, { method: 'PATCH', body: JSON.stringify(payload) }) : post('/model-settings/connections', payload), '连接配置已保存。')
  if (saved) { key.value = ''; connectionDialog.value?.close() }
}
function openModel(row?: Profile) {
  modelId.value = row?.id || ''
  model.value = { connection_id: row?.connection_id || data.value?.connections[0]?.id || '', name: row?.name || '', model: row?.model || '', kind: row?.kind || 'llm', enabled: row?.enabled ?? true, timeout_seconds: row?.timeout_seconds ?? data.value?.defaults.timeout_seconds ?? 180, max_output_tokens: row?.max_output_tokens ?? data.value?.defaults.max_output_tokens ?? 2400, temperature: row?.temperature ?? null, thinking: row?.thinking || 'default', api_version: row?.api_version || '2024-10-21' }
  modelDialog.value?.showModal()
}
async function saveModel() {
  const saved = await perform(() => modelId.value ? api('/model-settings/models/' + modelId.value, { method: 'PATCH', body: JSON.stringify(model.value) }) : post('/model-settings/models', model.value), '模型修订已保存，新任务将使用新参数。')
  if (saved) modelDialog.value?.close()
}
const discovered = ref<string[]>([]), discoverConnection = ref('')
async function discover(row: Connection) {
  await perform(async () => { const result = await post<{ models: string[] }>('/model-settings/connections/' + row.id + '/discover'); discovered.value = result.models; discoverConnection.value = row.id }, '模型列表已读取，也可手动填写其他模型 ID。')
}
async function test(row: Profile, kind: string) {
  await perform(async () => { const result = await post<{ succeeded: boolean; detail: string; duration_seconds: number }>('/model-settings/models/' + row.id + '/test', { kind }); if (!result.succeeded) throw new Error(result.detail); notify(result.detail + ' · ' + result.duration_seconds.toFixed(1) + ' 秒') }, '测试结果已记录。')
}
async function toggle(row: Connection) {
  await perform(() => api('/model-settings/connections/' + row.id, { method: 'PATCH', body: JSON.stringify({ name: row.name, provider: row.provider, protocol: row.protocol, base_url: row.base_url, enabled: !row.enabled }) }), row.enabled ? '连接已停用，后续调用将停止。' : '连接已启用。')
}
async function createSpace() {
  await perform(() => post('/model-settings/spaces', { model_id: spaceModel.value }), '向量空间已准备，可开始重建。')
}
async function spaceAction(row: Space, action: string) {
  await perform(() => post('/model-settings/spaces/' + row.id + '/' + action), action === 'activate' ? '已请求切换；缺失向量将先由 Worker 补齐。' : '向量任务状态已更新。')
  confirmSpace.value = ''
}
function status(value: string) { return ({ draft: '待重建', building: '重建中', ready: '已准备', failed: '失败', cancelled: '已取消' } as Record<string, string>)[value] || value }
onMounted(async () => { await load(); timer = setInterval(async () => { if (data.value?.spaces.some(s => s.status === 'building') && !polling.value && !busy.value) { polling.value = true; await load(true); polling.value = false } }, 4000) })
onUnmounted(() => { if (timer) clearInterval(timer); key.value = ''; connectionDialog.value?.close(); modelDialog.value?.close() })
</script>

<template>
  <section class="paper model-manager">
    <div class="section-title"><div><h2>模型连接</h2><p class="subtle">国内外与本地模型共用入口，密钥加密保存在服务端。</p></div><button class="button" :disabled="busy" @click="load()">刷新</button></div>
    <div v-if="error" class="error-panel"><p>{{ error }}</p><p>本机准备命令：<code>powershell -ExecutionPolicy Bypass -File .\scripts\prepare-model-settings.ps1</code></p></div>
    <template v-if="data">
      <p v-if="!data.credential_ready" class="error-panel">请先运行本机准备命令生成模型主密钥，再保存厂商密钥。</p>
      <div class="button-row"><button class="button primary" :disabled="busy" @click="openConnection()">添加连接</button><button class="button" :disabled="busy || !data.connections.length" @click="openModel()">添加模型</button></div>
      <div class="model-rows">
        <article v-for="row in data.connections" :key="row.id" class="model-row">
          <div class="model-row-title"><strong>{{ row.name }}</strong><span class="topic">{{ row.enabled ? '已启用' : '已停用' }}</span></div>
          <p class="subtle">{{ row.protocol }} · {{ row.key_configured ? '密钥已配置' : '未设置密钥 / 本地免密' }}</p><p class="model-address">{{ row.base_url }}</p>
          <div class="button-row"><button class="button ghost" :disabled="busy" @click="openConnection(row)">编辑</button><button class="button ghost" :disabled="busy" @click="openConnection(row, true)">复制配置</button><button class="button ghost" :disabled="busy || !row.enabled" @click="discover(row)">读取模型列表</button><button class="button ghost" :disabled="busy" @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</button></div>
          <p v-if="row.id === discoverConnection" class="subtle model-address">发现的模型：{{ discovered.join('、') || '服务未返回列表，请手动添加。' }}</p>
          <div v-for="profile in data.models.filter(m => m.connection_id === row.id)" :key="profile.id" class="model-profile">
            <div><strong>{{ profile.name }}</strong><p class="subtle">{{ profile.kind === 'llm' ? 'LLM' : 'Embedding · 1024 维' }} · {{ profile.model }} · {{ profile.enabled ? '已启用' : '已停用' }}</p><p v-if="profile.probes.length" class="subtle">最近测试：{{ profile.probes[0]?.succeeded ? '通过' : '未通过' }} · {{ profile.probes[0]?.detail }}</p><p v-else class="subtle">尚未验证 · 配置成功不代表模型能力已通过测试</p></div>
            <div class="button-row"><button class="button" :disabled="busy" @click="openModel(profile)">编辑模型</button><template v-if="profile.kind === 'llm'"><button class="button" :disabled="busy || !row.enabled || !profile.enabled" @click="test(profile, 'connection')">测试调用</button><button class="button" :disabled="busy || !row.enabled || !profile.enabled" @click="test(profile, 'json')">测试 JSON</button></template><button v-else class="button" :disabled="busy || !row.enabled || !profile.enabled" @click="test(profile, 'embedding')">测试向量</button></div>
          </div>
        </article>
      </div>
      <p class="subtle">测试会发送少量测试文本，可能产生厂商调用费用，计入本月额度。复制配置需要重新填写密钥。</p>
      <hr />
      <h2>用途分配</h2><p class="subtle">未覆写的阶段使用默认模型。修改对新任务生效，自动重试保留原修订。</p>
      <form @submit.prevent="perform(() => api('/model-settings/bindings', { method: 'PUT', body: JSON.stringify({ bindings }) }), '用途分配已保存。')">
        <div class="model-form-grid"><label v-for="stage in stages" :key="stage.id" class="field">{{ stage.name }}<select v-model="bindings[stage.id]" :aria-label="stage.name"><option :value="null">{{ stage.id === 'default' ? '未配置' : '使用默认模型' }}</option><option v-for="profile in llms" :key="profile.id" :value="profile.id" :disabled="!available(profile)">{{ profile.name }} · {{ profile.model }}{{ available(profile) ? '' : '（已停用）' }}</option></select></label></div>
        <button class="button primary" :disabled="busy">保存用途分配</button>
      </form>
      <hr />
      <h2>向量索引</h2><p class="subtle">仅支持 1024 维。更换模型先重建独立空间，旧索引继续服务；重建可能产生 Embedding 费用。</p>
      <form class="model-space-form" @submit.prevent="createSpace"><label class="field">Embedding 模型<select v-model="spaceModel" required><option value="" disabled>选择模型</option><option v-for="profile in embeddings" :key="profile.id" :value="profile.id">{{ profile.name }}</option></select></label><button class="button" :disabled="busy || !spaceModel">准备空间</button></form>
      <article v-for="space in data.spaces" :key="space.id" class="model-row">
        <div class="model-row-title"><strong>{{ space.model_name }}</strong><span class="topic">{{ data.active_space_id === space.id ? '当前检索空间' : status(space.status) }}</span></div><p class="subtle">{{ space.model }} · {{ space.indexed }} / {{ space.total }} 个有效片段 · {{ status(space.status) }}{{ space.activate_when_ready ? '，补齐后切换' : '' }}</p><p v-if="space.error" class="error-panel">{{ space.error }}</p>
        <div class="button-row"><button class="button" :disabled="busy || space.status === 'building'" @click="spaceAction(space, 'rebuild')">{{ space.status === 'failed' || space.status === 'cancelled' ? '恢复重建' : '重建 / 补齐' }}</button><button v-if="space.status === 'building'" class="button" :disabled="busy" @click="spaceAction(space, 'cancel')">取消重建</button><button v-if="data.active_space_id !== space.id" class="button" :disabled="busy" @click="confirmSpace = space.id">切换到此空间</button></div>
        <div v-if="confirmSpace === space.id" class="model-confirm"><p>确认将检索切换到 {{ space.model_name }}？缺失片段会先重建，完成后切换；可稍后切回其他空间。</p><div class="button-row"><button class="button primary" :disabled="busy" @click="spaceAction(space, 'activate')">确认切换</button><button class="button" @click="confirmSpace = ''">返回</button></div></div>
      </article>
      <p v-if="busy" class="subtle" role="status">正在处理，请等待结果…</p>
    </template>
  </section>
  <dialog ref="connectionDialog" class="model-dialog" @close="key = ''"><form @submit.prevent="saveConnection">
    <div class="section-title"><h2>{{ connectionId ? '编辑连接' : '添加连接' }}</h2><button type="button" class="button" @click="connectionDialog?.close()">关闭</button></div>
    <div class="model-form-grid"><label class="field">厂商预设<select v-model="connection.provider" @change="preset"><option v-for="p in data?.presets" :key="p.id" :value="p.id">{{ p.name }}</option><option value="bge">本地 BGE</option></select></label><label class="field">连接名称<input v-model="connection.name" required maxlength="100" /></label><label class="field">协议<select v-model="connection.protocol"><option v-for="p in protocols" :key="p" :value="p">{{ p }}</option></select></label><label class="field">服务地址<input v-model="connection.base_url" type="url" required maxlength="500" /></label></div>
    <label v-if="connectionId" class="model-check"><input v-model="replaceKey" type="checkbox" />替换密钥；更换地址或协议必须勾选，本地免密可留空</label>
    <label v-if="!connectionId || replaceKey" class="field">API Key<input v-model="key" type="password" autocomplete="new-password" placeholder="本地免密服务可留空" /></label>
    <label class="model-check"><input v-model="connection.enabled" type="checkbox" />启用此连接</label><button class="button primary" :disabled="busy">保存连接</button>
  </form></dialog>
  <dialog ref="modelDialog" class="model-dialog" aria-label="模型配置"><form @submit.prevent="saveModel">
    <div class="section-title"><h2>{{ modelId ? '编辑模型' : '添加模型' }}</h2><button type="button" class="button" @click="modelDialog?.close()">关闭</button></div>
    <div class="model-form-grid"><label class="field">连接<select v-model="model.connection_id" aria-label="模型连接" required><option v-for="row in data?.connections" :key="row.id" :value="row.id">{{ row.name }}</option></select></label><label class="field">名称<input v-model="model.name" required maxlength="100" /></label><label class="field">模型 ID<input v-model="model.model" required maxlength="200" placeholder="厂商控制台中的模型或部署 ID" /></label><label class="field">类型<select v-model="model.kind" :disabled="!!modelId"><option value="llm">LLM</option><option value="embedding">Embedding · 1024 维</option></select></label><label class="field">超时（秒）<input v-model.number="model.timeout_seconds" type="number" min="1" max="7200" required /></label><label v-if="model.kind === 'llm'" class="field">最大输出 Tokens<input v-model.number="model.max_output_tokens" type="number" min="16" max="200000" required /></label></div>
    <details><summary>更多参数</summary><div class="model-form-grid"><label class="field">Temperature（空值使用厂商默认）<input v-model.number="model.temperature" type="number" min="0" max="2" step="0.1" @change="model.temperature = model.temperature === null || String(model.temperature) === '' ? null : Number(model.temperature)" /></label><label class="field">兼容协议思考模式<select v-model="model.thinking"><option value="default">厂商默认</option><option value="enabled">启用</option><option value="disabled">禁用</option></select></label><label class="field">Azure API 版本<input v-model="model.api_version" maxlength="50" /></label></div><p class="subtle">厂商与模型支持不同参数，保存后请测试；不支持的关键参数不会自动删除。</p></details>
    <label class="model-check"><input v-model="model.enabled" type="checkbox" />启用此模型</label><button class="button primary" :disabled="busy">保存模型</button>
  </form></dialog>
</template>

<style scoped>
.model-manager { padding:24px; margin-top:24px; }
.model-manager hr { border:0; border-top:1px solid var(--line); margin:28px 0; }
.model-rows { margin-top:16px; }.model-row { padding:18px 0; border-bottom:1px solid var(--line); }
.model-row-title { display:flex; flex-wrap:wrap; align-items:center; gap:12px; }.model-row p { margin:8px 0; }
.model-address { overflow-wrap:anywhere; color:var(--muted); font-size:13px; }
.model-profile { border-left:2px solid var(--line); padding-left:16px; margin:16px 0 0; }.model-profile .button-row { margin-top:10px; }
.model-form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; margin:18px 0; }
.model-check { display:flex; align-items:center; gap:8px; margin:16px 0; }.model-check input { width:auto; }
.model-space-form { display:flex; align-items:end; gap:12px; margin:18px 0; }.model-space-form .field { flex:1; margin:0; }
.model-dialog { width:min(660px,calc(100vw - 32px)); max-height:calc(100dvh - 32px); overflow:auto; padding:24px; border:1px solid var(--line); border-radius:8px; color:var(--ink); background:var(--paper); }
.model-dialog::backdrop { background:rgb(15 23 42 / 30%); }.model-dialog summary { margin:16px 0; cursor:pointer; }.model-confirm { padding:12px 0; }
.model-manager code { overflow-wrap:anywhere; }
@media(max-width:600px) { .model-manager,.model-dialog { padding:16px; }.model-form-grid { grid-template-columns:1fr; }.model-space-form { flex-direction:column; align-items:stretch; }.model-profile { padding-left:10px; } }
</style>
