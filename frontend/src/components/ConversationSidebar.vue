<template>
  <aside class="conv-sidebar" :class="{ collapsed }">
    <!-- 折叠态：只显示一个展开按钮 + 新建按钮 -->
    <div v-if="collapsed" class="collapsed-rail">
      <button class="rail-btn" title="展开会话列表" @click="toggleCollapsed">▶</button>
      <button class="rail-btn primary" title="新对话" @click="$emit('new-conversation')">＋</button>
    </div>

    <!-- 展开态 -->
    <template v-else>
      <div class="sidebar-header">
        <span class="sidebar-title">📋 对话</span>
        <button class="collapse-btn" title="折叠" @click="toggleCollapsed">◀</button>
      </div>

      <button class="new-conv-btn" @click="$emit('new-conversation')">
        <span>＋</span><span>新对话</span>
      </button>

      <div v-if="loading" class="sidebar-empty">加载中...</div>
      <div v-else-if="!groupedActive.length && !archivedConvs.length" class="sidebar-empty">
        还没有历史对话
      </div>

      <div class="conv-list">
        <!-- 未归档：按时间分组 -->
        <div v-for="g in groupedActive" :key="g.label" class="conv-group">
          <div class="group-label">{{ g.label }}</div>
          <ConversationItem
            v-for="c in g.items"
            :key="c.conversation_id"
            :conv="c"
            :is-current="c.conversation_id === currentConversationId"
            :renaming="renamingId === c.conversation_id"
            :menu-open="menuOpenId === c.conversation_id"
            @select="$emit('select', c)"
            @toggle-menu="toggleMenu(c.conversation_id)"
            @rename-start="startRename(c.conversation_id)"
            @rename-commit="(t) => commitRename(c.conversation_id, t)"
            @rename-cancel="cancelRename"
            @archive="archiveConv(c.conversation_id)"
          />
        </div>

        <!-- 已归档：折叠分组（默认折叠） -->
        <div v-if="archivedConvs.length" class="conv-group archived-group">
          <div class="group-label clickable" @click="showArchived = !showArchived">
            <span class="archived-toggle">{{ showArchived ? '▼' : '▶' }}</span>
            已归档（{{ archivedConvs.length }}）
          </div>
          <template v-if="showArchived">
            <ConversationItem
              v-for="c in archivedConvs"
              :key="c.conversation_id"
              :conv="c"
              :is-current="c.conversation_id === currentConversationId"
              :renaming="renamingId === c.conversation_id"
              :menu-open="menuOpenId === c.conversation_id"
              :archived="true"
              @select="$emit('select', c)"
              @toggle-menu="toggleMenu(c.conversation_id)"
              @rename-start="startRename(c.conversation_id)"
              @rename-commit="(t) => commitRename(c.conversation_id, t)"
              @rename-cancel="cancelRename"
              @restore="restoreConv(c.conversation_id)"
              @delete-permanent="deletePermanent(c.conversation_id)"
            />
          </template>
        </div>
      </div>
    </template>
  </aside>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import ConversationItem from './ConversationItem.vue'

const props = defineProps({
  userId: { type: String, required: true },
  currentConversationId: { type: String, default: null },
})
const emit = defineEmits(['select', 'new-conversation'])

const collapsed = ref(localStorage.getItem('sidebar_collapsed') === '1')
const loading = ref(false)
const activeConvs = ref([])      // archived_at IS NULL
const archivedConvs = ref([])    // archived_at IS NOT NULL
const renamingId = ref(null)
const menuOpenId = ref(null)
const showArchived = ref(false)

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  localStorage.setItem('sidebar_collapsed', collapsed.value ? '1' : '0')
}

async function refresh() {
  loading.value = true
  try {
    const [aRes, arRes] = await Promise.all([
      fetch(`/api/chat/${props.userId}/conversations?archived=false`),
      fetch(`/api/chat/${props.userId}/conversations?archived=true`),
    ])
    const aData = await aRes.json()
    const arData = await arRes.json()
    activeConvs.value = aData.conversations || []
    archivedConvs.value = arData.conversations || []
  } catch (e) {
    console.warn('refresh conversations failed', e)
  } finally {
    loading.value = false
  }
}

defineExpose({ refresh })

onMounted(refresh)
watch(() => props.userId, refresh)

// 时间分组：今天 / 昨天 / 7天内 / 30天内 / 更早
const groupedActive = computed(() => {
  const now = new Date()
  const dayMs = 86400_000
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const groups = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '7 天内', items: [] },
    { label: '30 天内', items: [] },
    { label: '更早', items: [] },
  ]
  for (const c of activeConvs.value) {
    const t = new Date(c.last_active_at).getTime()
    const diff = now - t
    if (t >= startOfToday.getTime()) groups[0].items.push(c)
    else if (t >= startOfToday.getTime() - dayMs) groups[1].items.push(c)
    else if (diff < 7 * dayMs) groups[2].items.push(c)
    else if (diff < 30 * dayMs) groups[3].items.push(c)
    else groups[4].items.push(c)
  }
  return groups.filter(g => g.items.length)
})

function toggleMenu(id) {
  menuOpenId.value = menuOpenId.value === id ? null : id
}

function startRename(id) {
  renamingId.value = id
  menuOpenId.value = null
}
function cancelRename() {
  renamingId.value = null
}
async function commitRename(id, newTitle) {
  renamingId.value = null
  if (!newTitle?.trim()) return
  await fetch(`/api/chat/${props.userId}/conversations/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: newTitle.trim() }),
  })
  await refresh()
}

async function archiveConv(id) {
  if (!confirm('确认归档此对话？\n（仍可在"已归档"里恢复）')) return
  menuOpenId.value = null
  await fetch(`/api/chat/${props.userId}/conversations/${id}`, { method: 'DELETE' })
  await refresh()
}

async function restoreConv(id) {
  menuOpenId.value = null
  await fetch(`/api/chat/${props.userId}/conversations/${id}/restore`, { method: 'POST' })
  await refresh()
}

async function deletePermanent(id) {
  if (!confirm('永久删除此对话？\n\n⚠️ 包括 LLM 对话历史，无法恢复。')) return
  menuOpenId.value = null
  await fetch(`/api/chat/${props.userId}/conversations/${id}/permanent`, { method: 'DELETE' })
  await refresh()
}
</script>

<style scoped>
.conv-sidebar {
  width: 260px;
  flex-shrink: 0;
  background: rgba(252, 252, 254, 0.96);
  backdrop-filter: blur(10px);
  border-right: 1px solid rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.2s ease;
}
.conv-sidebar.collapsed {
  width: 48px;
}

.collapsed-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px 0;
}
.rail-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  color: #595959;
  border-radius: 6px;
  transition: background 0.15s;
}
.rail-btn:hover {
  background: rgba(199, 0, 11, 0.08);
  color: #c7000b;
}
.rail-btn.primary {
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  color: white;
  font-size: 16px;
  font-weight: 600;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 14px 8px;
}
.sidebar-title {
  font-weight: 600;
  font-size: 14px;
  color: #1a1a1a;
}
.collapse-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  color: #8c8c8c;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 4px;
}
.collapse-btn:hover {
  background: rgba(0, 0, 0, 0.05);
  color: #1a1a1a;
}

.new-conv-btn {
  margin: 4px 12px 12px;
  padding: 8px 12px;
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  box-shadow: 0 2px 6px rgba(199, 0, 11, 0.25);
  transition: transform 0.15s;
}
.new-conv-btn:hover {
  transform: translateY(-1px);
}

.sidebar-empty {
  padding: 24px 16px;
  font-size: 12px;
  color: #8c8c8c;
  text-align: center;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 6px 12px;
}
.conv-group {
  margin-top: 8px;
}
.group-label {
  font-size: 11px;
  color: #8c8c8c;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 8px 8px 4px;
  font-weight: 600;
}
.group-label.clickable {
  cursor: pointer;
  user-select: none;
}
.group-label.clickable:hover {
  color: #595959;
}
.archived-toggle {
  display: inline-block;
  width: 12px;
  font-size: 9px;
}
.archived-group {
  border-top: 1px dashed rgba(0, 0, 0, 0.08);
  margin-top: 12px;
  padding-top: 4px;
}
</style>
