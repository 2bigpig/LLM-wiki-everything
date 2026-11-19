<script setup lang="ts">
import { statusLabels } from '../api'
defineProps<{ status: string }>()
</script>
<template><span class="status" :class="status"><i />{{ statusLabels[status] || status }}</span></template>
