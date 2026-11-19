import { ref } from 'vue'

/** 保存完成后通知当前页面刷新，避免引入只用于单一事件的全局状态框架。 */
export const refreshVersion = ref(0)
export const composerOpen = ref(false)
export const notifications = ref<{ id: number; text: string; error: boolean }[]>([])
let nextNotification = 0
export function notify(text: string, error = false) {
  const id = ++nextNotification
  notifications.value.push({ id, text, error })
  window.setTimeout(() => { notifications.value = notifications.value.filter(n => n.id !== id) }, 6500)
}
export const invalidate = () => { refreshVersion.value++ }
