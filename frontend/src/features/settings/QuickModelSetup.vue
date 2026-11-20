<script setup lang="ts">
/** 一个配置表单组合已有接口；JSON 测试通过后才切换默认，失败保留已保存对象。 */
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { ApiError, api, date, post } from '../../api'
import { invalidate, notify } from '../../state'
import SecretInput from '../../components/SecretInput.vue'
import type { Connection, Overview } from './types'

const props = defineProps<{ data: Overview }>()
const emit = defineEmits<{ changed: [] }>()
const editing = ref(false), busy = ref(false), error = ref(''), stage = ref(-1)
const provider = ref(''), address = ref(''), key = ref(''), model = ref(''), apiVersion = ref('2024-10-21')
const editAddress = ref(false), providerInput = ref<HTMLSelectElement>()
const connectionId = ref(''), modelId = ref(''), savedTarget = ref(''), savedModel = ref(''), savedKey = ref(false)
const testedRevision = ref(''), testedEndpoint = ref(0), draftTag = ref(crypto.randomUUID().slice(0, 8))
// 网络中断时不能盲目再次 POST；用本表单独有名称查询是否已经提交。
const uncertain = ref<'' | 'connection' | 'model'>('')
let uncertainName = '', uncertainTarget = '', uncertainModel = ''
const steps = ['保存连接', '保存模型', '测试 JSON', '启用默认模型']
const selected = computed(() => props.data.presets.find(p => p.id === provider.value))
const local = computed(() => ['ollama', 'lmstudio', 'custom'].includes(provider.value))
const explicitAddress = computed(() => ['custom', 'azure'].includes(provider.value))
const target = computed(() => JSON.stringify([provider.value, selected.value?.protocol, address.value.trim().replace(/\/+$/, '')]))
const needsKey = computed(() => !local.value && (!savedKey.value || savedTarget.value !== target.value))
const current = computed(() => props.data.models.find(m => m.id === props.data.bindings.default))
const currentConnection = computed(() => props.data.connections.find(c => c.id === current.value?.connection_id))
const currentValid = computed(() => current.value?.enabled && currentConnection.value?.enabled && current.value.endpoint_version === currentConnection.value.endpoint_version)
const currentProbe = computed(() => current.value?.probes.find(p => p.kind === 'json'))
const hasOverrides = computed(() => Object.entries(props.data.bindings).some(([name, id]) => name !== 'default' && id))
const showForm = computed(() => editing.value || !current.value)
const connectionName = computed(() => `${selected.value?.name || '模型'} ${draftTag.value}`)

function chooseProvider() {
  address.value = provider.value === 'azure' ? '' : selected.value?.base_url || ''; key.value = ''; editAddress.value = false
  testedRevision.value = ''; error.value = ''; stage.value = -1
}
async function start() {
  provider.value = ''; address.value = ''; key.value = ''; model.value = ''
  connectionId.value = ''; modelId.value = ''; savedTarget.value = ''; savedModel.value = ''; savedKey.value = false
  testedRevision.value = ''; uncertain.value = ''; error.value = ''; stage.value = -1
  draftTag.value = crypto.randomUUID().slice(0, 8); editing.value = true
  await nextTick(); providerInput.value?.focus()
}
function cancel() { key.value = ''; editing.value = false; error.value = '' }
function rememberConnection(row: Connection) {
  connectionId.value = row.id; savedTarget.value = target.value; savedKey.value = row.key_configured
  key.value = ''; testedRevision.value = ''
}
async function reconcile() {
  busy.value = true
  try {
    const latest = await api<Overview>('/model-settings')
    if (uncertain.value === 'connection') {
      const row = latest.connections.find(c => c.name === uncertainName && JSON.stringify([c.provider, c.protocol, c.base_url]) === uncertainTarget)
      if (!row) throw new Error('暂未查到保存结果。请稍后再次核对，或在高级设置中检查；不会自动重复创建。')
      rememberConnection(row)
    } else {
      const row = latest.models.find(m => m.connection_id === connectionId.value && m.name === uncertainName && m.model === uncertainModel)
      if (!row) throw new Error('暂未查到模型。请稍后再次核对，或在高级设置中检查；不会自动重复创建。')
      modelId.value = row.id; savedModel.value = ''; testedRevision.value = ''
    }
    uncertain.value = ''; error.value = '已确认保存结果，可以继续测试并启用。'; emit('changed')
  } catch (e) { handleError(e) }
  finally { busy.value = false }
}
function handleError(e: unknown) {
  error.value = (e as Error).message
}
function ambiguous(e: unknown) { return e instanceof ApiError && (e.status === 0 || e.status >= 500) }

async function submit() {
  if (busy.value || uncertain.value) return
  busy.value = true; error.value = ''
  try {
    stage.value = 0
    // 首先读取当前配置，避免高级设置、其他标签页的停用或修改被静默覆盖。
    let latest = await api<Overview>('/model-settings')
    if (!latest.credential_ready) throw new Error('模型主密钥尚未准备，请先运行 scripts/prepare-model-settings.ps1，重启后端后刷新。')
    const existing = latest.connections.find(c => c.id === connectionId.value)
    if (connectionId.value && (!existing || !existing.enabled)) throw new Error('连接已停用或移除。请在高级设置检查，或重新开始配置。')
    const connectionChanged = !existing || JSON.stringify([existing.provider, existing.protocol, existing.base_url]) !== target.value || !!key.value
    let row = existing
    if (connectionChanged) {
      testedRevision.value = ''
      const payload = { name: connectionName.value, provider: provider.value, protocol: selected.value!.protocol,
        base_url: address.value.trim(), api_key: key.value || (savedTarget.value !== target.value ? '' : null), enabled: true }
      try {
        row = connectionId.value
          ? await api<Connection>('/model-settings/connections/' + connectionId.value, { method: 'PATCH', body: JSON.stringify(payload) })
          : await post<Connection>('/model-settings/connections', payload)
      } catch (e) {
        if (!connectionId.value && ambiguous(e)) {
          uncertain.value = 'connection'; uncertainName = connectionName.value; uncertainTarget = target.value
          throw new Error('连接保存结果尚未确认。请先核对保存结果，避免创建重复连接。')
        }
        throw e
      }
      rememberConnection(row)
    }
    stage.value = 1
    const payload = { connection_id: connectionId.value, name: `${selected.value!.name} · ${model.value.trim()}`.slice(0, 100),
      model: model.value.trim(), kind: 'llm', api_version: apiVersion.value, ...latest.defaults }
    const signature = JSON.stringify([payload, row!.endpoint_version])
    let revision = latest.models.find(m => m.id === modelId.value)?.revision_id
    if (modelId.value && !latest.models.find(m => m.id === modelId.value)?.enabled) throw new Error('模型已停用，请在高级设置检查。')
    if (!modelId.value || savedModel.value !== signature) {
      testedRevision.value = ''
      try {
        const result = modelId.value
          ? await api<{ id: string; revision_id: string }>('/model-settings/models/' + modelId.value, { method: 'PATCH', body: JSON.stringify(payload) })
          : await post<{ id: string; revision_id: string }>('/model-settings/models', payload)
        modelId.value = result.id; revision = result.revision_id; savedModel.value = signature
      } catch (e) {
        if (!modelId.value && ambiguous(e)) {
          uncertain.value = 'model'; uncertainName = payload.name; uncertainModel = payload.model
          throw new Error('模型保存结果尚未确认。请先核对保存结果，避免创建重复模型。')
        }
        throw e
      }
    }
    stage.value = 2
    // 用途保存失败时保留成功测试的修订，重试只保存用途，不再产生测试费用。
    if (testedRevision.value !== revision || testedEndpoint.value !== row!.endpoint_version) {
      const result = await post<{ succeeded: boolean; detail: string }>('/model-settings/models/' + modelId.value + '/test', { kind: 'json' })
      if (!result.succeeded) throw new Error(`配置已保存，尚未切换默认模型。${result.detail}`)
      testedRevision.value = revision!; testedEndpoint.value = row!.endpoint_version
    }
    stage.value = 3
    latest = await api<Overview>('/model-settings')
    const checked = latest.models.find(m => m.id === modelId.value)
    const checkedConnection = latest.connections.find(c => c.id === connectionId.value)
    if (!checked?.enabled || !checkedConnection?.enabled || checked.revision_id !== testedRevision.value || checkedConnection.endpoint_version !== testedEndpoint.value) {
      testedRevision.value = ''; throw new Error('配置在测试期间已变更或停用，请检查后重新测试。')
    }
    await api('/model-settings/bindings', { method: 'PUT', body: JSON.stringify({ bindings: { ...latest.bindings, default: modelId.value } }) })
    stage.value = 4; key.value = ''; editing.value = false; emit('changed'); invalidate(); notify('模型已通过 JSON 测试，已设为默认模型。')
  } catch (e) { handleError(e); emit('changed') }
  finally { busy.value = false }
}
onBeforeUnmount(() => { key.value = '' })
</script>

<template>
  <section class="paper quick-setup" aria-label="快速接入模型" :aria-busy="busy">
    <div v-if="!showForm" class="connected-summary">
      <div><span class="subtle">当前默认模型</span><h2>{{ current?.model }}</h2><p>{{ currentConnection?.name }} · {{ currentValid ? '已启用' : '配置不可用' }}</p></div>
      <button class="button" :disabled="busy" @click="start">更换模型</button>
      <p v-if="!currentValid" class="error-panel">模型或连接已停用，或服务地址已变更。请更换模型，或展开高级设置检查。</p>
      <p v-else class="subtle">{{ currentProbe ? `最近 JSON 测试：${currentProbe.succeeded ? '通过' : '未通过'} · ${date(currentProbe.created_at)}` : '尚未验证 JSON 能力，可在高级设置中测试' }}</p>
    </div>
    <form v-else @submit.prevent="submit">
      <div class="quick-heading"><h2>接通你的整理模型</h2><span class="subtle">保存后对新任务生效</span></div>
      <fieldset :disabled="busy || !!uncertain">
        <div class="quick-fields">
          <label class="field">厂商<select ref="providerInput" v-model="provider" aria-label="厂商" required @change="chooseProvider"><option value="" disabled>选择厂商或本地服务</option><option v-for="p in data.presets" :key="p.id" :value="p.id">{{ p.name }}</option></select></label>
          <label class="field">API Key<span v-if="local" class="field-note">可免密</span><SecretInput v-model="key" aria-label="API Key" autocomplete="new-password" :required="needsKey" :placeholder="savedKey ? '已安全保存，留空继续使用' : local ? '免密服务留空' : '填写厂商提供的 API Key'" /></label>
          <label class="field">模型 ID<input v-model="model" required maxlength="200" placeholder="填写模型名或部署 ID" /></label>
        </div>
        <template v-if="selected">
          <label v-if="explicitAddress || editAddress" class="field address-field">服务地址<input v-model="address" type="url" required maxlength="500" /></label>
          <div v-else class="address-summary"><span>{{ address }}</span><button type="button" class="text-link" @click="editAddress = true">修改地址</button></div>
          <label v-if="provider === 'azure'" class="field address-field">Azure API 版本<input v-model="apiVersion" required maxlength="50" /></label>
        </template>
      </fieldset>
      <div v-if="error" class="error-panel" role="alert">{{ error }}</div>
      <div v-if="uncertain" class="button-row"><button type="button" class="button" :disabled="busy" @click="reconcile">核对保存结果</button></div>
      <div class="quick-actions"><button class="button primary" :disabled="busy || !!uncertain || !data.credential_ready">{{ busy ? steps[stage] + '…' : '测试并启用' }}</button><button v-if="current" type="button" class="button ghost" :disabled="busy" @click="cancel">取消</button><span class="subtle">发送少量测试文本，可能产生费用</span></div>
      <ol v-if="stage >= 0" class="setup-steps" aria-live="polite"><li v-for="(step, index) in steps" :key="step" :class="{ done: index < stage, active: index === stage }">{{ index < stage ? '✓' : index + 1 }} {{ step }}</li></ol>
      <p v-if="!data.credential_ready" class="error-panel">请先运行 <code>scripts/prepare-model-settings.ps1</code> 准备模型主密钥，重启后端后刷新。</p>
    </form>
    <p v-if="hasOverrides" class="override-note">部分用途仍使用单独配置，可在高级设置调整</p>
  </section>
</template>

<style scoped>
.quick-setup { padding:24px; }.quick-heading,.connected-summary { display:flex; gap:16px; align-items:center; justify-content:space-between; flex-wrap:wrap; }.quick-heading { margin-bottom:20px; }.quick-heading h2 { margin:0; }
fieldset { min-width:0; border:0; padding:0; margin:0; }.quick-fields { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }.field { margin-bottom:16px; }.field-note { margin-left:8px; color:var(--muted); font-size:12px; }
.address-summary { display:flex; gap:12px; align-items:baseline; margin-bottom:20px; font-size:13px; color:var(--muted); }.address-summary span { overflow-wrap:anywhere; min-width:0; }.address-summary button { flex-shrink:0; }.address-field { max-width:680px; }
.quick-actions { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }.quick-actions .subtle { font-size:12px; }.setup-steps { list-style:none; display:flex; gap:16px; flex-wrap:wrap; padding:16px 0 0; margin:0; color:var(--muted); font-size:12px; }.setup-steps .active { color:var(--accent); }.setup-steps .done { color:var(--secondary); }
.connected-summary h2 { margin:8px 0; overflow-wrap:anywhere; }.connected-summary p { margin:0; }.connected-summary > p { flex-basis:100%; }.override-note { color:var(--secondary); font-size:13px; margin:20px 0 0; padding-top:16px; border-top:1px solid var(--line); }
@media(max-width:600px) { .quick-setup { padding:20px; }.quick-heading { margin-bottom:16px; }.quick-heading .subtle { display:none; }.quick-fields { grid-template-columns:1fr; gap:0; }.quick-actions .subtle { flex-basis:100%; }.setup-steps { gap:8px 16px; } }
</style>
