<!-- frontend/src/components/preview/EChartsPreview.vue -->
<template>
  <div class="echarts-preview-wrapper">
    <div ref="chartEl" class="echarts-preview" />
    <div v-if="errorMsg" class="echarts-error">加载失败：{{ errorMsg }}</div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ url: String, name: String })
const chartEl = ref(null)
const errorMsg = ref('')
let chart = null

async function load() {
  errorMsg.value = ''
  try {
    const resp = await fetch(props.url)
    const config = await resp.json()
    if (!chart && chartEl.value) {
      chart = echarts.init(chartEl.value)
    }
    chart?.setOption(config)
  } catch (e) {
    errorMsg.value = e.message
  }
}

onMounted(load)
watch(() => props.url, load)
onBeforeUnmount(() => chart?.dispose())
</script>

<style scoped>
.echarts-preview-wrapper {
  width: 100%;
  height: 100%;
  position: relative;
  min-height: 400px;
}
.echarts-preview {
  width: 100%;
  height: 100%;
  min-height: 400px;
}
.echarts-error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c7000b;
  padding: 20px;
}
</style>
