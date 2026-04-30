<!-- frontend/src/components/preview/JsonPreview.vue -->
<template>
  <pre class="json-preview"><code>{{ rendered }}</code></pre>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({ url: String, name: String })
const rendered = ref('')

async function load() {
  try {
    const resp = await fetch(props.url)
    const text = await resp.text()
    if (text.length > 100 * 1024) {
      rendered.value = '文件过大（>100KB），请下载查看：' + props.url
      return
    }
    try {
      rendered.value = JSON.stringify(JSON.parse(text), null, 2)
    } catch {
      rendered.value = text
    }
  } catch (e) {
    rendered.value = '加载失败：' + e.message
  }
}
onMounted(load)
watch(() => props.url, load)
</script>

<style scoped>
.json-preview {
  margin: 0;
  padding: 20px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 13px;
  background: #f6f8fa;
  height: 100%;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
