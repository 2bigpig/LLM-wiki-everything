<script setup lang="ts">
/** 只切换当前输入的显示方式，不读取已保存的服务端密钥。清空后恢复隐藏。 */
import { ref, watch } from 'vue'
import Icon from './Icon.vue'
defineOptions({ inheritAttrs: false })
const value = defineModel<string>({ required: true })
const visible = ref(false)
watch(value, current => { if (!current) visible.value = false })
</script>

<template>
  <span class="secret-input">
    <input v-bind="$attrs" v-model="value" :type="visible ? 'text' : 'password'" />
    <button type="button" :aria-label="visible ? '隐藏密钥' : '显示密钥'" :aria-pressed="visible"
      :title="visible ? '隐藏密钥' : '显示密钥'" @click="visible = !visible">
      <Icon :name="visible ? 'eye-off' : 'eye'" :size="18" />
    </button>
  </span>
</template>

<style scoped>
.secret-input { display:block; position:relative; margin-top:8px; }
.secret-input input { margin-top:0; padding-right:48px; }
.secret-input button { position:absolute; top:1px; right:1px; bottom:1px; width:44px; display:grid; place-items:center; border:0; border-radius:0 7px 7px 0; background:transparent; color:var(--secondary); }
.secret-input button:hover { color:var(--ink); background:var(--surface); }
.secret-input button:focus-visible { outline:2px solid var(--accent); outline-offset:-3px; }
</style>
