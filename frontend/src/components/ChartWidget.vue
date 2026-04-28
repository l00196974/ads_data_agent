<template>
  <div ref="chartEl" class="chart-widget"></div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ config: Object })
const chartEl = ref(null)
let chart = null
let resizeObserver = null

onMounted(async () => {
  // Wait for DOM layout to complete before initializing chart
  await nextTick()
  chart = echarts.init(chartEl.value, null, {
    renderer: 'canvas',
    useDirtyRect: true
  })
  renderChart()

  // Responsive resize handling
  resizeObserver = new ResizeObserver(() => {
    chart?.resize()
  })
  resizeObserver.observe(chartEl.value)
})

watch(() => props.config, renderChart)

function renderChart() {
  if (!chart || !props.config) return

  // Support both old and new config formats
  let config = props.config
  let type = config.type || 'bar'
  let title = config.title || ''
  let xAxisData = config.xAxis?.data || config.x || []
  let seriesData = config.series || []

  // Apply brand-themed default options
  const option = {
    title: title ? {
      text: title,
      textStyle: {
        fontSize: 15,
        fontWeight: 600,
        color: '#1a1a1a'
      },
      left: 'center'
    } : undefined,
    tooltip: {
      trigger: type === 'pie' ? 'item' : 'axis',
      backgroundColor: 'rgba(255, 255, 255, 0.95)',
      borderColor: '#e8e8e8',
      textStyle: { color: '#333' }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      containLabel: true
    },
    color: ['#c7000b', '#d81a24', '#ff7875', '#ff9c2e', '#2f54eb'],
    xAxis: type !== 'pie' ? {
      type: 'category',
      data: xAxisData,
      axisLine: { lineStyle: { color: '#d9d9d9' } },
      axisLabel: { color: '#595959' }
    } : undefined,
    yAxis: type !== 'pie' ? {
      type: 'value',
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { color: '#595959' }
    } : undefined,
    series: seriesData.map(s => ({
      ...s,
      type: s.type || type,
      itemStyle: type === 'pie' ? {
        borderColor: '#fff',
        borderWidth: 2
      } : undefined
    })),
  }

  chart.setOption(option, true)
}

onUnmounted(() => {
  if (resizeObserver) {
    resizeObserver.disconnect()
  }
  if (chart) {
    chart.dispose()
  }
})
</script>

<style scoped>
.chart-widget {
  width: 100%;
  min-height: 320px;
  height: 320px;
}

@media (max-width: 768px) {
  .chart-widget {
    min-height: 260px;
    height: 260px;
  }
}
</style>
