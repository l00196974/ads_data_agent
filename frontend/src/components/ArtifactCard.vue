<!-- frontend/src/components/ArtifactCard.vue -->
<template>
  <div class="artifact-card" @click="$emit('open', artifactId)">
    <div class="artifact-icon">📦</div>
    <div class="artifact-content">
      <div class="artifact-title">
        {{ manifest?.title || artifactId }}
      </div>
      <div class="artifact-meta">
        <span v-if="manifest">
          {{ formatType(manifest.artifact_type) }} · {{ formatTime(manifest.created_at) }}
        </span>
        <span v-else class="artifact-loading">加载中...</span>
      </div>
      <div v-if="manifest?.description" class="artifact-desc">
        {{ manifest.description }}
      </div>
    </div>
    <div class="artifact-action">
      <span class="action-text">查看 →</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({
  artifactId: { type: String, required: true },
  userId: { type: String, required: true },
})
defineEmits(['open'])

const manifest = ref(null)

async function loadManifest() {
  try {
    const resp = await fetch(`/api/artifacts/${props.userId}/${props.artifactId}/manifest`)
    if (!resp.ok) return
    const body = await resp.json()
    manifest.value = body.manifest
  } catch (_) {}
}

onMounted(loadManifest)
watch(() => props.artifactId, loadManifest)

const TYPE_LABEL = {
  report: '📊 综合报告',
  document: '📄 文档',
  spreadsheet: '📋 电子表格',
  presentation: '📽 演示',
  dataset: '🗃 数据集',
  mixed: '📁 混合产物',
}
function formatType(t) {
  return TYPE_LABEL[t] || '📁 ' + (t || 'unknown')
}
function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.artifact-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: rgba(248, 250, 255, 0.95);
  border: 1px solid #d6e4ff;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  align-self: flex-start;
  width: 100%;
  max-width: 560px;
}
.artifact-card:hover {
  background: rgba(232, 240, 255, 0.95);
  border-color: #adc6ff;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}
.artifact-icon {
  font-size: 28px;
  flex-shrink: 0;
}
.artifact-content {
  flex: 1;
  min-width: 0;
}
.artifact-title {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a1a;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.artifact-meta {
  font-size: 11px;
  color: #8c8c8c;
}
.artifact-loading {
  color: #c7000b;
  font-style: italic;
}
.artifact-desc {
  font-size: 12px;
  color: #595959;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.artifact-action {
  flex-shrink: 0;
}
.action-text {
  font-size: 12px;
  color: #2f54eb;
  font-weight: 500;
}
</style>
