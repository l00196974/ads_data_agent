<!-- frontend/src/components/ArtifactBrowser.vue -->
<template>
  <div class="artifact-browser">
    <header class="browser-header">
      <div class="header-title">
        <span class="header-icon">📦</span>
        <span>{{ manifest?.title || artifactId }}</span>
      </div>
      <div class="header-actions">
        <a :href="`/api/artifacts/${userId}/${artifactId}/zip`" :download="`${artifactId}.zip`" class="btn-zip">
          ⬇ 下载 zip
        </a>
        <button class="btn-close" @click="$emit('close')">×</button>
      </div>
    </header>

    <div class="browser-body">
      <aside class="file-tree">
        <FileTreeNode
          v-for="node in tree"
          :key="node.path"
          :node="node"
          :selected="selected"
          @select="onSelect"
        />
      </aside>

      <main class="file-preview">
        <div v-if="!selected" class="preview-empty">选择左侧文件查看内容</div>
        <component
          v-else
          :is="previewComponent(selected)"
          :url="`/api/artifacts/${userId}/${artifactId}/file/${selected}`"
          :name="selected"
        />
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, h } from 'vue'
import MarkdownPreview from './preview/MarkdownPreview.vue'
import EChartsPreview from './preview/EChartsPreview.vue'
import CsvPreview from './preview/CsvPreview.vue'
import JsonPreview from './preview/JsonPreview.vue'
import ImagePreview from './preview/ImagePreview.vue'
import DownloadFallback from './preview/DownloadFallback.vue'

const props = defineProps({
  userId: { type: String, required: true },
  artifactId: { type: String, required: true },
})
defineEmits(['close'])

const manifest = ref(null)
const tree = ref([])
const selected = ref(null)

async function load() {
  const resp = await fetch(`/api/artifacts/${props.userId}/${props.artifactId}/manifest`)
  if (!resp.ok) return
  const body = await resp.json()
  manifest.value = body.manifest
  tree.value = body.tree
  const entry = body.manifest?.entry_files?.[0]
  if (entry) selected.value = entry
}
onMounted(load)
watch(() => props.artifactId, load)

function onSelect(path) {
  selected.value = path
}

function previewComponent(path) {
  const lower = path.toLowerCase()
  if (lower.endsWith('.md')) return MarkdownPreview
  if (lower.endsWith('.echarts.json')) return EChartsPreview
  if (lower.endsWith('.csv')) return CsvPreview
  if (lower.endsWith('.json') || lower.endsWith('.yaml') || lower.endsWith('.yml')) return JsonPreview
  if (/\.(png|jpe?g|gif|svg|webp)$/.test(lower)) return ImagePreview
  return DownloadFallback
}

const FileTreeNode = {
  name: 'FileTreeNode',
  props: ['node', 'selected'],
  emits: ['select'],
  setup(p, { emit }) {
    const expanded = ref(true)
    return () => {
      const isDir = p.node.type === 'dir'
      if (isDir) {
        return h('div', { class: 'tree-dir' }, [
          h('div', {
            class: 'tree-dir-name',
            onClick: () => { expanded.value = !expanded.value }
          }, [
            h('span', { class: 'tree-toggle' }, expanded.value ? '▼' : '▶'),
            h('span', { class: 'tree-icon' }, '📁'),
            h('span', p.node.path.split('/').pop())
          ]),
          expanded.value ? h('div', { class: 'tree-children' },
            (p.node.children || []).map(child =>
              h(FileTreeNode, {
                key: child.path,
                node: child,
                selected: p.selected,
                onSelect: (path) => emit('select', path)
              })
            )
          ) : null
        ])
      }
      return h('div', {
        class: ['tree-file', { 'tree-selected': p.selected === p.node.path }],
        onClick: () => emit('select', p.node.path)
      }, [
        h('span', { class: 'tree-icon' }, fileIcon(p.node.path)),
        h('span', { class: 'tree-name' }, p.node.path.split('/').pop())
      ])
    }
  }
}

function fileIcon(path) {
  const lower = path.toLowerCase()
  if (lower.endsWith('.md')) return '📝'
  if (lower.endsWith('.echarts.json')) return '📊'
  if (lower.endsWith('.csv')) return '📋'
  if (lower.endsWith('.json') || lower.endsWith('.yaml')) return '🧾'
  if (/\.(png|jpe?g|gif|svg)$/.test(lower)) return '🖼'
  return '📄'
}
</script>

<style scoped>
.artifact-browser {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: white;
}
.browser-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid #e9ecef;
  background: #f8f9fa;
}
.header-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 15px;
}
.header-icon { font-size: 20px; }
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.btn-zip {
  font-size: 13px;
  color: #2f54eb;
  text-decoration: none;
  padding: 6px 12px;
  border: 1px solid #adc6ff;
  border-radius: 6px;
  background: white;
}
.btn-zip:hover { background: #e8f0ff; }
.btn-close {
  font-size: 24px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: #8c8c8c;
  width: 32px;
  height: 32px;
  border-radius: 50%;
}
.btn-close:hover { background: #f1f3f5; color: #1a1a1a; }
.browser-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}
.file-tree {
  width: 260px;
  border-right: 1px solid #e9ecef;
  padding: 12px 8px;
  overflow-y: auto;
  background: #fafbfc;
  font-size: 13px;
}
.file-preview {
  flex: 1;
  overflow: auto;
  background: white;
}
.preview-empty {
  padding: 60px 20px;
  text-align: center;
  color: #8c8c8c;
}
:deep(.tree-dir-name), :deep(.tree-file) {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  user-select: none;
}
:deep(.tree-dir-name:hover), :deep(.tree-file:hover) { background: #e8f0ff; }
:deep(.tree-selected) { background: #d6e4ff !important; color: #2f54eb; font-weight: 500; }
:deep(.tree-toggle) { width: 12px; font-size: 10px; }
:deep(.tree-icon) { font-size: 14px; }
:deep(.tree-children) { margin-left: 16px; }
:deep(.tree-name) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
