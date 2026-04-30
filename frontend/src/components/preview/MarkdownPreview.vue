<!-- frontend/src/components/preview/MarkdownPreview.vue -->
<template>
  <div class="md-preview markdown-body" v-html="rendered" />
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps({ url: String, name: String })
const rendered = ref('')

async function load() {
  try {
    const resp = await fetch(props.url)
    const text = await resp.text()
    rendered.value = DOMPurify.sanitize(marked.parse(text))
  } catch (e) {
    rendered.value = DOMPurify.sanitize(`<p style="color:#c7000b">加载失败：${e.message}</p>`)
  }
}
onMounted(load)
watch(() => props.url, load)
</script>

<style scoped>
.md-preview {
  padding: 20px;
  font-size: 14px;
  line-height: 1.7;
  color: #1a1a1a;
}
.md-preview :deep(h1) { font-size: 1.6em; margin: 0.6em 0; }
.md-preview :deep(h2) { font-size: 1.3em; margin: 0.6em 0; }
.md-preview :deep(p) { margin: 0.6em 0; }
.md-preview :deep(code) { background: #f1f3f5; padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.9em; }
.md-preview :deep(pre) { background: #f6f8fa; padding: 12px; border-radius: 8px; overflow-x: auto; }
</style>
