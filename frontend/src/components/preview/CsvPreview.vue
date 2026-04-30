<!-- frontend/src/components/preview/CsvPreview.vue -->
<template>
  <div class="csv-preview">
    <div v-if="error" class="csv-error">{{ error }}</div>
    <div v-else class="csv-meta">共 {{ rows.length }} 行（最多展示前 1000 行）</div>
    <table v-if="rows.length">
      <thead>
        <tr>
          <th v-for="(h, i) in headers" :key="i">{{ h }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in rows.slice(0, 1000)" :key="i">
          <td v-for="(cell, j) in row" :key="j">{{ cell }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({ url: String, name: String })
const headers = ref([])
const rows = ref([])
const error = ref('')

async function load() {
  error.value = ''
  try {
    const resp = await fetch(props.url)
    const text = await resp.text()
    const lines = text.split('\n').filter(l => l.length)
    if (!lines.length) return
    headers.value = parseRow(lines[0])
    rows.value = lines.slice(1).map(parseRow)
  } catch (e) {
    error.value = '加载失败：' + e.message
  }
}

function parseRow(line) {
  return line.split(',')
}

onMounted(load)
watch(() => props.url, load)
</script>

<style scoped>
.csv-preview {
  padding: 16px;
  overflow: auto;
  height: 100%;
}
.csv-meta {
  font-size: 12px;
  color: #8c8c8c;
  margin-bottom: 12px;
}
.csv-error {
  color: #c7000b;
  padding: 16px;
}
table {
  border-collapse: collapse;
  font-size: 13px;
}
th, td {
  border: 1px solid #e9ecef;
  padding: 6px 10px;
  text-align: left;
  white-space: nowrap;
}
th {
  background: #f1f3f5;
  font-weight: 600;
}
</style>
