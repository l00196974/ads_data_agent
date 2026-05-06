<template>
  <div
    class="conv-item"
    :class="{ current: isCurrent, 'is-archived': archived }"
    @click="onItemClick"
  >
    <!-- 重命名态：内联 input -->
    <input
      v-if="renaming"
      ref="renameInput"
      v-model="renameDraft"
      class="rename-input"
      @keyup.enter="commit"
      @keyup.esc="$emit('rename-cancel')"
      @blur="commit"
      @click.stop
    />
    <!-- 普通态 -->
    <template v-else>
      <div class="conv-title" :title="conv.title">{{ conv.title }}</div>
      <div class="conv-meta">
        <span class="conv-time">{{ relativeTime(conv.last_active_at) }}</span>
        <span class="conv-msg-count">· {{ conv.message_count }} 条</span>
      </div>
      <div v-if="conv.metrics && conv.metrics.calls > 0" class="conv-metrics" :title="metricsTitle">
        <span>🪙 {{ formatTokens(conv.metrics.total_tokens) }}</span>
        <span class="metric-divider">·</span>
        <span>{{ conv.metrics.calls }} 次</span>
      </div>
      <button class="menu-btn" :title="archived ? '操作' : '更多'" @click.stop="$emit('toggle-menu')">⋯</button>
    </template>

    <!-- 三点菜单 -->
    <div v-if="menuOpen" class="menu-pop" @click.stop>
      <button class="menu-item" @click="$emit('rename-start')">✏️ 重命名</button>
      <template v-if="!archived">
        <button class="menu-item danger" @click="$emit('archive')">🗑 归档</button>
      </template>
      <template v-else>
        <button class="menu-item" @click="$emit('restore')">↩ 恢复</button>
        <button class="menu-item danger" @click="$emit('delete-permanent')">⚠️ 永久删除</button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'

const props = defineProps({
  conv: { type: Object, required: true },
  isCurrent: { type: Boolean, default: false },
  renaming: { type: Boolean, default: false },
  menuOpen: { type: Boolean, default: false },
  archived: { type: Boolean, default: false },
})
const emit = defineEmits([
  'select', 'toggle-menu', 'rename-start', 'rename-commit', 'rename-cancel',
  'archive', 'restore', 'delete-permanent',
])

const renameDraft = ref(props.conv.title)
const renameInput = ref(null)

watch(() => props.renaming, async (v) => {
  if (v) {
    renameDraft.value = props.conv.title
    await nextTick()
    renameInput.value?.focus()
    renameInput.value?.select()
  }
})

function onItemClick() {
  if (props.renaming) return
  emit('select')
}

function commit() {
  if (!props.renaming) return
  emit('rename-commit', renameDraft.value)
}

function formatTokens(n) {
  if (n == null) return '-'
  if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k'
  return String(n)
}

const metricsTitle = computed(() => {
  const m = props.conv.metrics
  if (!m || !m.calls) return ''
  const dur = m.duration_ms ? `${(m.duration_ms / 1000).toFixed(1)}s LLM 耗时` : ''
  return [
    `累计 ${m.total_tokens.toLocaleString()} tokens（输入 ${m.input_tokens.toLocaleString()} + 输出 ${m.output_tokens.toLocaleString()}）`,
    m.cache_read_tokens ? `prompt cache 命中 ${m.cache_read_tokens.toLocaleString()}` : '',
    `${m.calls} 次 LLM 调用`,
    dur,
  ].filter(Boolean).join('\n')
})

// "刚刚" / "5 分钟前" / "今天 14:30" / "昨天" / "MM-DD"
function relativeTime(iso) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  const now = Date.now()
  const diff = now - t
  const min = 60_000
  const hour = 3600_000
  const day = 86400_000
  if (diff < min) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / min)} 分钟前`
  const d = new Date(t)
  const today = new Date()
  const sameDay = d.getFullYear() === today.getFullYear()
    && d.getMonth() === today.getMonth() && d.getDate() === today.getDate()
  if (sameDay) return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  if (diff < 2 * day) return '昨天'
  return `${(d.getMonth() + 1).toString().padStart(2, '0')}-${d.getDate().toString().padStart(2, '0')}`
}
</script>

<style scoped>
.conv-item {
  position: relative;
  padding: 8px 28px 8px 10px;
  margin: 2px 0;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
  border: 1px solid transparent;
}
.conv-item:hover {
  background: rgba(199, 0, 11, 0.06);
}
.conv-item.current {
  background: rgba(199, 0, 11, 0.1);
  border-color: rgba(199, 0, 11, 0.3);
}
.conv-item.is-archived {
  opacity: 0.7;
}

.conv-title {
  font-size: 13px;
  color: #1a1a1a;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.4;
}
.conv-item.current .conv-title {
  color: #c7000b;
}
.conv-meta {
  font-size: 11px;
  color: #8c8c8c;
  margin-top: 2px;
  display: flex;
  gap: 4px;
}
.conv-metrics {
  font-size: 10px;
  color: #8c8c8c;
  margin-top: 2px;
  display: flex;
  gap: 4px;
  font-family: ui-monospace, SFMono-Regular, "Cascadia Mono", Menlo, monospace;
  cursor: help;
}
.metric-divider {
  opacity: 0.5;
}
.conv-time {
  white-space: nowrap;
}
.conv-msg-count {
  white-space: nowrap;
}

.menu-btn {
  position: absolute;
  top: 6px;
  right: 4px;
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: #8c8c8c;
  border-radius: 4px;
  font-size: 16px;
  font-weight: 600;
  line-height: 1;
  display: none;
}
.conv-item:hover .menu-btn,
.conv-item.current .menu-btn {
  display: block;
}
.menu-btn:hover {
  background: rgba(0, 0, 0, 0.08);
  color: #1a1a1a;
}

.menu-pop {
  position: absolute;
  top: 30px;
  right: 4px;
  z-index: 10;
  background: white;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 6px;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
  padding: 4px 0;
  min-width: 130px;
}
.menu-item {
  display: block;
  width: 100%;
  padding: 6px 12px;
  background: transparent;
  border: none;
  text-align: left;
  font-size: 12px;
  color: #1a1a1a;
  cursor: pointer;
  white-space: nowrap;
}
.menu-item:hover {
  background: rgba(199, 0, 11, 0.08);
}
.menu-item.danger {
  color: #c7000b;
}
.menu-item.danger:hover {
  background: rgba(199, 0, 11, 0.1);
}

.rename-input {
  width: 100%;
  padding: 4px 8px;
  border: 1px solid #c7000b;
  border-radius: 4px;
  font-size: 13px;
  outline: none;
}
</style>
