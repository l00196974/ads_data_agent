<template>
  <div class="workspace-page">
    <header class="topbar">
      <span class="brand">📦 我的工作区</span>
      <span class="user-tag">{{ userId }}</span>
      <button class="btn-back" @click="$router.push('/chat')">← 返回对话</button>
    </header>

    <main class="workspace-main">
      <div v-if="loading" class="loading">加载中...</div>
      <div v-else-if="!artifacts.length" class="empty">
        <div class="empty-icon">📂</div>
        <div>还没有任何 artifact</div>
        <div class="empty-hint">让 Agent 帮你生成第一份分析报告吧</div>
      </div>
      <div v-else class="artifact-grid">
        <div
          v-for="art in artifacts"
          :key="art.artifact_id"
          class="artifact-tile"
          @click="browsing = art.artifact_id"
        >
          <div class="tile-title">{{ art.title || art.artifact_id }}</div>
          <div class="tile-meta">
            {{ formatType(art.artifact_type) }} · {{ art.file_count }} 个文件 · {{ formatTime(art.created_at) }}
          </div>
          <div v-if="art.description" class="tile-desc">{{ art.description }}</div>
          <div class="tile-actions">
            <button class="tile-btn-delete" @click.stop="onDelete(art.artifact_id)">删除</button>
          </div>
        </div>
      </div>
    </main>

    <div v-if="browsing" class="overlay" @click.self="browsing = null">
      <div class="modal">
        <ArtifactBrowser :user-id="userId" :artifact-id="browsing" @close="browsing = null" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import ArtifactBrowser from '../components/ArtifactBrowser.vue'

const userId = localStorage.getItem('user_id') || 'anon'
const artifacts = ref([])
const loading = ref(true)
const browsing = ref(null)

async function load() {
  loading.value = true
  try {
    const resp = await fetch(`/api/artifacts/${userId}`)
    const body = await resp.json()
    artifacts.value = body.artifacts || []
  } finally {
    loading.value = false
  }
}

async function onDelete(id) {
  if (!confirm(`确定删除 ${id}？`)) return
  await fetch(`/api/artifacts/${userId}/${id}`, { method: 'DELETE' })
  await load()
}

const TYPE_LABEL = {
  report: '📊 综合报告',
  document: '📄 文档',
  spreadsheet: '📋 电子表格',
  presentation: '📽 演示',
  dataset: '🗃 数据集',
  mixed: '📁 混合产物',
}
function formatType(t) { return TYPE_LABEL[t] || ('📁 ' + (t || 'unknown')) }
function formatTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<style scoped>
.workspace-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
}
.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 24px;
  height: 60px;
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  color: white;
}
.brand { font-size: 18px; font-weight: 600; flex: 1; }
.user-tag {
  font-size: 13px;
  padding: 4px 12px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 12px;
}
.btn-back {
  padding: 6px 16px;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.btn-back:hover {
  background: rgba(255, 255, 255, 0.25);
}
.workspace-main {
  flex: 1;
  padding: 32px 24px;
  overflow-y: auto;
}
.loading, .empty {
  text-align: center;
  padding: 80px 20px;
  color: #8c8c8c;
}
.empty-icon { font-size: 64px; margin-bottom: 12px; }
.empty-hint { font-size: 13px; margin-top: 6px; color: #adb5bd; }
.artifact-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
  max-width: 1200px;
  margin: 0 auto;
}
.artifact-tile {
  background: white;
  border: 1px solid #e9ecef;
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.2s;
  position: relative;
}
.artifact-tile:hover {
  border-color: #adc6ff;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}
.tile-title { font-weight: 600; font-size: 14px; color: #1a1a1a; margin-bottom: 6px; }
.tile-meta { font-size: 11px; color: #8c8c8c; margin-bottom: 8px; }
.tile-desc { font-size: 12px; color: #595959; line-height: 1.5; }
.tile-actions { margin-top: 12px; text-align: right; }
.tile-btn-delete {
  font-size: 11px;
  padding: 4px 10px;
  background: transparent;
  border: 1px solid #ffccc7;
  color: #c7000b;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}
.tile-btn-delete:hover { background: #fff1f0; }
.overlay {
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal {
  width: 90vw; height: 90vh; max-width: 1400px;
  background: white; border-radius: 12px; overflow: hidden;
}
</style>
