<template>
  <div ref="chartEl" class="chart-widget"></div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ config: Object })
const chartEl = ref(null)
let chart = null

onMounted(() => {
  chart = echarts.init(chartEl.value)
  renderChart()
})

watch(() => props.config, renderChart)

function renderChart() {
  if (!chart || !props.config) return
  const { type, title, x, series } = props.config
  const option = {
    title: { text: title, textStyle: { fontSize: 13 } },
    tooltip: { trigger: type === 'pie' ? 'item' : 'axis' },
    series: series.map(s => ({ ...s, type })),
  }
  if (type !== 'pie') {
    option.xAxis = { data: x }
    option.yAxis = {}
  }
  chart.setOption(option)
}
</script>

<style scoped>
.chart-widget { width: 100%; height: 280px; }
</style>
