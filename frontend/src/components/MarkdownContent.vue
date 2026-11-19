<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
const props = defineProps<{ content: string }>()
const router = useRouter()
// 禁用原始 HTML 和远程图片；来源中的 HTML/脚本不会进入页面。
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: false })
markdown.disable('image')
const defaultValidate = markdown.validateLink
markdown.validateLink = value => defaultValidate(value) && (/^https?:\/\//i.test(value) || /^\/(?!\/)/.test(value) || value.startsWith('#'))
const rendered = computed(() => markdown.render(props.content))
function navigate(event: MouseEvent) {
  const link = (event.target as HTMLElement).closest('a')
  if (!link) return
  const href = link.getAttribute('href') || ''
  if (href.startsWith('/') && !href.startsWith('//')) {
    event.preventDefault(); void router.push(href)
  } else { link.setAttribute('rel', 'noopener noreferrer'); link.setAttribute('target', '_blank') }
}
</script>
<template><div class="markdown" @click="navigate" v-html="rendered" /></template>
