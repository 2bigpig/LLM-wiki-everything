<script setup lang="ts">
defineProps<{ name: string; size?: number }>()
// 图标保持 SVG 原生描边，组件只负责统一尺寸和可访问性。
const paths: Record<string, string> = {
  library: 'M4 4h6v16H4zM14 4h6v16h-6zM4 8h6M14 8h6',
  wiki: 'M4 4h6c2 0 2 1 2 2v15c0-2-2-3-4-3H3V4zM12 6c0-2 2-2 4-2h5v14h-5c-2 0-4 1-4 3',
  tasks: 'M9 6h11M9 12h11M9 18h11M3 6l1 1 2-2M3 12l1 1 2-2M3 18l1 1 2-2',
  settings: 'M4 7h16M4 17h16M8 4v6M16 14v6',
  plus: 'M12 5v14M5 12h14', search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  arrow: 'M5 12h14M14 7l5 5-5 5', close: 'M6 6l12 12M18 6 6 18',
  link: 'M10 14l4-4M9 7l2-2a5 5 0 0 1 7 7l-2 2M15 17l-2 2a5 5 0 0 1-7-7l2-2',
  note: 'M5 3h11l3 3v15H5zM9 10h6M9 14h6M9 18h4',
  file: 'M14 3v6h6M4 3h10l6 6v12H4z',
  upload: 'M12 16V3M7 8l5-5 5 5M4 15v6h16v-6',
  spark: 'M12 3l2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4z',
  clock: 'M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  back: 'M19 12H5M10 7l-5 5 5 5', check: 'M5 12l4 4L19 6',
  edit: 'M14 5l5 5M4 20l5-1L21 7l-5-5L4 14z',
  download: 'M12 3v12M7 10l5 5 5-5M4 17v4h16v-4',
  branch: 'M6 3v12a3 3 0 0 0 3 3h9M6 8h9a3 3 0 0 0 3-3V3M18 15v6',
  archive: 'M9 4H4v16h5M15 4h5v16h-5M9 12h6',
  menu: 'M4 6h16M4 12h16M4 18h16',
}
</script>
<template>
  <svg :width="size || 18" :height="size || 18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path :d="paths[name] || paths.file" />
  </svg>
</template>
