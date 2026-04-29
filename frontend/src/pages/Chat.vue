<template>
  <div class="chat-layout">
    <header class="topbar">
      <span class="brand">华为广告数据助手</span>
      <span class="user-tag">{{ userId }}</span>
      <button class="btn-new" @click="clearChat">新对话</button>
      <button class="btn-logout" @click="logout">退出</button>
    </header>

    <div class="main">
      <!-- 左侧 Skills 面板 -->
      <aside class="skill-panel">
        <div class="panel-title">已注册能力</div>
        <div class="skill-section">
          <p class="section-label">系统 Skills</p>
          <div v-for="s in systemSkills" :key="s.name" class="skill-item">
            <span class="skill-dot">●</span>{{ s.name }}
          </div>
        </div>
        <div class="skill-section">
          <p class="section-label">我的 Skills</p>
          <div
            v-for="s in userSkills"
            :key="s.name"
            class="skill-item user-skill-item"
            :title="s.description || s.name"
          >
            <span class="skill-dot">●</span>
            <span class="skill-name">{{ s.name }}</span>
            <button
              v-if="s.type === 'skill_md'"
              class="skill-delete"
              :title="`删除 ${s.name}`"
              @click="deleteSkill(s.name)"
            >×</button>
          </div>
          <div class="skill-item add-btn" @click="triggerUpload">+ 添加</div>
          <input
            ref="fileInputRef"
            type="file"
            accept=".zip"
            style="display: none"
            @change="onFileChange"
          />
          <div v-if="uploadStatus" class="upload-status">{{ uploadStatus }}</div>
        </div>
      </aside>

      <!-- 右侧对话区 -->
      <div class="chat-area">
        <!-- 进度状态栏 -->
        <div v-if="progressText" class="progress-container">
          <div class="progress-bar">
            <div class="progress-indicator"></div>
          </div>
          <span class="progress-text"><span class="progress-dot">●</span> {{ progressText }}</span>
        </div>

        <!-- 消息列表 -->
        <div class="messages" ref="messagesEl">
          <template v-for="(msg, i) in messages" :key="i">
            <!-- 用户消息：保留红色气泡 -->
            <div v-if="msg.type === 'text' && msg.role === 'user'" class="bubble user">
              <div class="bubble-text">{{ msg.content }}</div>
            </div>
            <!-- AI 文本：直接平铺到页面，无气泡 -->
            <div
              v-else-if="msg.type === 'text'"
              class="ai-response markdown-body"
              v-html="renderMd(msg.content)"
            />
            <!-- 图表：保留卡片容器（图表渲染需要明确边界） -->
            <div v-else-if="msg.type === 'chart'" class="bubble chart-bubble">
              <ChartWidget :config="msg.content" />
            </div>
            <!-- 思考过程：append-only 日志，可折叠 -->
            <div v-else-if="msg.type === 'thinking'" class="thinking-group">
              <div class="thinking-summary" @click="msg.collapsed = !msg.collapsed">
                <span class="thinking-toggle">{{ msg.collapsed ? '▶' : '▼' }}</span>
                <span class="thinking-label">
                  思考过程<span v-if="msg.collapsed">：{{ summarizeThinking(msg.entries) }}</span>
                </span>
              </div>
              <div v-if="!msg.collapsed" class="thinking-entries">
                <div
                  v-for="(entry, j) in msg.entries"
                  :key="j"
                  :class="['thinking-entry', `entry-${entry.kind}`]"
                >
                  <span class="entry-label">{{ entry.label }}</span>
                </div>
              </div>
            </div>
          </template>
          <div v-if="isStreaming && currentThinkingIdx === null && currentStreamingIdx === null" class="thinking-hint">
            <span class="typing">正在思考...</span>
          </div>
        </div>

        <!-- 追加消息提示 -->
        <div v-if="appendHint" class="append-hint">📝 已收到补充要求，正在调整计划...</div>

        <!-- 输入区 -->
        <div class="input-area">
          <input
            v-model="inputText"
            placeholder="输入问题，执行中也可追加要求..."
            @keyup.enter="sendOrAppend"
            class="input-box"
          />
          <button
            @click="sendOrAppend"
            :disabled="!inputText.trim()"
            class="btn-send"
          >发送</button>
          <button
            v-if="isStreaming"
            @click="stopStream"
            class="btn-stop"
          >停止</button>
        </div>
      </div>
    </div>

    <ConfirmDialog
      :visible="isInterrupted"
      :message="interruptMsg"
      :preview="interruptPreview"
      @confirm="handleConfirm"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { SSEClient } from '../utils/sse.js'
import ChartWidget from '../components/ChartWidget.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const router = useRouter()
const userId = localStorage.getItem('user_id') || ''
const messages = ref([])
const inputText = ref('')
const isStreaming = ref(false)
const isInterrupted = ref(false)
const interruptMsg = ref('')
const interruptPreview = ref([])
const systemSkills = ref([])
const userSkills = ref([])
const fileInputRef = ref(null)
const uploadStatus = ref('')
const appendHint = ref(false)
const messagesEl = ref(null)
// 当前流式文本气泡在 messages 中的下标。token 累加到这里，非 token 事件复位。
const currentStreamingIdx = ref(null)
// 当前思考过程分组在 messages 中的下标。所有 plan/step 事件 append 进这个分组。
// token 到来时关闭分组，让下次 plan/step 自然新开下一个。
const currentThinkingIdx = ref(null)
const progressText = ref('')
// conversationId: 同一对话内多轮共享，"新对话"按钮重置为 null（后端会生成新 id）
const conversationId = ref(localStorage.getItem('conversation_id') || null)
let sseClient = null

function renderMd(content) {
  const rawHtml = marked.parse(content)
  return DOMPurify.sanitize(rawHtml)
}

// 折叠态展示已发生的工具调用名（去掉"执行中..."后缀），让用户一眼看到调过哪些 skill
function summarizeThinking(entries) {
  const names = entries
    .filter(e => e.kind === 'running' || e.kind === 'done')
    .map(e => e.label.replace(/\s*执行中\.\.\.$/, ''))
  if (!names.length) return `${entries.length} 步`
  if (names.length <= 3) return names.join(' → ')
  return `${names.slice(0, 3).join(' → ')} → +${names.length - 3}`
}

async function refreshSkills() {
  try {
    const resp = await fetch(`/api/skills/${userId}`)
    const data = await resp.json()
    systemSkills.value = data.system || []
    userSkills.value = data.user || []
  } catch (_) {}
}

onMounted(refreshSkills)

function triggerUpload() {
  fileInputRef.value?.click()
}

async function onFileChange(e) {
  const file = e.target.files?.[0]
  if (!file) return
  uploadStatus.value = '上传中...'
  try {
    const fd = new FormData()
    fd.append('file', file)
    const resp = await fetch(`/api/skills/${userId}/upload`, { method: 'POST', body: fd })
    if (!resp.ok) {
      const detail = await resp.text().catch(() => '')
      uploadStatus.value = `失败: ${detail.slice(0, 100)}`
      return
    }
    const data = await resp.json()
    uploadStatus.value = `已添加: ${data.name}`
    await refreshSkills()
  } catch (err) {
    uploadStatus.value = `网络错误: ${err.message}`
  } finally {
    e.target.value = ''
    setTimeout(() => { uploadStatus.value = '' }, 4000)
  }
}

async function deleteSkill(name) {
  if (!confirm(`确认删除 "${name}"？`)) return
  try {
    const resp = await fetch(`/api/skills/${userId}/${encodeURIComponent(name)}`, { method: 'DELETE' })
    if (!resp.ok) {
      uploadStatus.value = `删除失败: ${await resp.text()}`
      return
    }
    uploadStatus.value = `已删除: ${name}`
    await refreshSkills()
  } catch (err) {
    uploadStatus.value = `删除错误: ${err.message}`
  } finally {
    setTimeout(() => { uploadStatus.value = '' }, 3000)
  }
}

async function sendOrAppend() {
  if (!inputText.value.trim()) return
  const msg = inputText.value.trim()
  inputText.value = ''

  // 执行中：后端会 cancel + 重起 agent_runner，前端在同 SSE 连接上接新一轮事件。
  // 必须重置游标——否则新一轮 plan/step/token 会被追加到上一轮的 thinking 分组 / 文本块上。
  if (isStreaming.value) {
    messages.value.push({ role: 'user', type: 'text', content: msg })
    currentThinkingIdx.value = null
    currentStreamingIdx.value = null
    appendHint.value = true
    setTimeout(() => { appendHint.value = false }, 3000)
    scrollToBottom()
    await fetch(`/api/chat/${userId}/append`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, conversation_id: conversationId.value }),
    })
    return
  }

  // 新对话
  messages.value.push({ role: 'user', type: 'text', content: msg })
  currentStreamingIdx.value = null
  currentThinkingIdx.value = null
  isStreaming.value = true

  // token 到来时关闭思考分组，让下一波工具事件自然新开下一个
  const closeStreaming = () => { currentStreamingIdx.value = null }
  const closeThinking = () => { currentThinkingIdx.value = null }

  // 取活跃的思考分组，没有就新建一个
  const getOrCreateThinking = () => {
    if (currentThinkingIdx.value === null) {
      messages.value.push({ type: 'thinking', entries: [], collapsed: false })
      currentThinkingIdx.value = messages.value.length - 1
    }
    return messages.value[currentThinkingIdx.value]
  }
  const appendLog = (entry) => {
    getOrCreateThinking().entries.push(entry)
    scrollToBottom()
  }

  sseClient = new SSEClient(`/api/chat/${userId}`, {
      step: (data) => {
        closeStreaming()
        // 同一工具的 start/end 是同一条 log 的状态变化，不是两条独立条目。
        // 不用图标，靠 "执行中..." 后缀表达运行态——end 漏报时也只是后缀残留，不会误显示假完成。
        if (data.type === 'tool_start') {
          appendLog({ label: `${data.msg} 执行中...`, kind: 'running' })
        } else {
          const group = currentThinkingIdx.value !== null
            ? messages.value[currentThinkingIdx.value]
            : null
          if (group) {
            for (let k = group.entries.length - 1; k >= 0; k--) {
              if (group.entries[k].kind === 'running') {
                group.entries[k].label = group.entries[k].label.replace(/\s*执行中\.\.\.$/, '')
                group.entries[k].kind = 'done'
                break
              }
            }
          }
          scrollToBottom()
        }
      },
      task_update: (_data) => {
        // append-only 模式下不需要状态推断，忽略
      },
      token: (data) => {
        // 流式 token：关闭思考分组，累加到当前 assistant 气泡
        closeThinking()
        if (currentStreamingIdx.value === null) {
          messages.value.push({ role: 'assistant', type: 'text', content: data.token })
          currentStreamingIdx.value = messages.value.length - 1
        } else {
          messages.value[currentStreamingIdx.value].content += data.token
        }
        scrollToBottom()
      },
      render: (data) => {
        closeStreaming()
        closeThinking()
        messages.value.push({ role: 'assistant', type: 'chart', content: {
          type: data.component,
          title: data.title,
          xAxis: { data: data.x_data },
          series: data.series.map(s => ({ name: s.name, type: data.component, data: s.data })),
        }})
        scrollToBottom()
      },
      progress: (data) => {
        // progress 不进消息流，仅在顶部状态栏短暂展示
        progressText.value = data.message
      },
      chart: (data) => {
        closeStreaming()
        closeThinking()
        messages.value.push({ role: 'assistant', type: 'chart', content: data })
        scrollToBottom()
      },
      interrupt: (data) => {
        closeStreaming()
        closeThinking()
        isInterrupted.value = true
        // 不要清 isStreaming——SSE 连接还活着，用户 confirm 后后端会继续推。
        // 清成 false 会导致输入框激活、新对话覆盖 sseClient、孤立旧连接。
        interruptMsg.value = data.message || data.msg || '需要您确认此操作'
        interruptPreview.value = data.preview || []
      },
      done: () => {
        closeStreaming()
        // 不再 done 时自动折叠——用户需要看到调用了哪些工具，不然会以为
        // 模型啥都没干。要折叠用户自己点 ▼。
        closeThinking()
        isStreaming.value = false
        progressText.value = ''
      },
      error: (data) => {
        closeStreaming()
        closeThinking()
        messages.value.push({
          role: 'assistant',
          type: 'text',
          content: `错误：${data.error || data.message || String(data)}`,
        })
        isStreaming.value = false
        progressText.value = ''
      },
    })

  try {
    await sseClient.connect({ message: msg, conversation_id: conversationId.value })
    // 后端可能新生成了 conversation_id（首次发起时），同步到本地
    if (sseClient.conversationId && sseClient.conversationId !== conversationId.value) {
      conversationId.value = sseClient.conversationId
      localStorage.setItem('conversation_id', sseClient.conversationId)
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      messages.value.push({
        role: 'assistant',
        type: 'text',
        content: '连接中断，请重试',
      })
    }
  }
  isStreaming.value = false
}

async function handleConfirm(action) {
  isInterrupted.value = false
  await fetch(`/api/chat/${userId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, session_id: sseClient?.sessionId }),
  })
}

async function stopStream() {
  sseClient?.abort()
  await fetch(`/api/chat/${userId}/cancel`, { method: 'POST' })
  isStreaming.value = false
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  })
}

function clearChat() {
  messages.value = []
  progressText.value = ''
  currentStreamingIdx.value = null
  currentThinkingIdx.value = null
  isStreaming.value = false
  appendHint.value = false
  // 切换 conversationId 为 null：下次发请求时后端会创建新 thread，旧对话历史不参与
  conversationId.value = null
  localStorage.removeItem('conversation_id')
}

function logout() {
  localStorage.removeItem('user_id')
  router.push('/')
}
</script>

<style scoped>
.chat-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 24px;
  height: 60px;
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  color: white;
  box-shadow: 0 2px 8px rgba(199, 0, 11, 0.3);
}
.brand {
  font-size: 18px;
  font-weight: 600;
  flex: 1;
  letter-spacing: 0.3px;
}
.user-tag {
  font-size: 13px;
  opacity: 0.9;
  padding: 4px 12px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 12px;
}
.btn-new {
  padding: 6px 16px;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s ease;
}
.btn-new:hover {
  background: rgba(255, 255, 255, 0.28);
  transform: translateY(-1px);
}
.btn-logout {
  padding: 6px 16px;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s ease;
}
.btn-logout:hover {
  background: rgba(255, 255, 255, 0.28);
  transform: translateY(-1px);
}

.main {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.skill-panel {
  width: 240px;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  border-right: 1px solid rgba(0, 0, 0, 0.08);
  padding: 20px 16px;
  overflow-y: auto;
  flex-shrink: 0;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.04);
}
.panel-title {
  font-weight: 600;
  font-size: 14px;
  color: #1a1a1a;
  margin-bottom: 16px;
  letter-spacing: 0.5px;
}
.skill-section {
  margin-bottom: 20px;
}
.section-label {
  font-size: 12px;
  color: #8c8c8c;
  margin: 0 0 8px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.skill-item {
  font-size: 13px;
  color: #595959;
  padding: 6px 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-radius: 4px;
  transition: background 0.15s ease;
}
.skill-item:hover {
  background: rgba(199, 0, 11, 0.05);
}
.skill-dot {
  color: #c7000b;
  font-size: 10px;
}
.add-btn {
  color: #c7000b;
  cursor: pointer;
  font-weight: 500;
}
.user-skill-item {
  position: relative;
}
.user-skill-item .skill-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-delete {
  background: transparent;
  border: none;
  color: #999;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0 4px;
  border-radius: 4px;
  margin-left: 4px;
  display: none;
}
.user-skill-item:hover .skill-delete {
  display: inline-block;
}
.skill-delete:hover {
  background: rgba(199, 0, 11, 0.1);
  color: #c7000b;
}
.upload-status {
  font-size: 11px;
  color: #595959;
  padding: 6px 8px;
  margin-top: 4px;
  background: rgba(199, 0, 11, 0.06);
  border-radius: 4px;
  word-break: break-all;
}

.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: transparent;
}

.step-tracker {
  padding: 12px 20px 16px;
  background: rgba(255, 251, 230, 0.9);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid #ffe58f;
  max-height: 180px;
  overflow-y: auto;
}
.step-tracker::before {
  content: "🔄 执行步骤";
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #8c6a00;
  margin-bottom: 8px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.step-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #595959;
  padding: 4px 0;
}
.step-icon {
  font-size: 14px;
}

/* Progress Container with visual bar */
.progress-container {
  padding: 12px 20px;
  background: rgba(240, 245, 255, 0.9);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid #adc6ff;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.progress-bar {
  width: 100%;
  height: 6px;
  background: rgba(173, 198, 255, 0.4);
  border-radius: 3px;
  overflow: hidden;
}
.progress-indicator {
  width: 30%;
  height: 100%;
  background: linear-gradient(90deg, #2f54eb, #597ef7);
  border-radius: 3px;
  animation: progress-pulse 1.5s ease-in-out infinite;
}
.progress-text {
  font-size: 13px;
  color: #2f54eb;
  display: flex;
  align-items: center;
  gap: 6px;
}
.progress-dot {
  animation: blink 1s infinite;
  color: #2f54eb;
  font-size: 10px;
}

@keyframes progress-pulse {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(350%); }
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.2; }
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.bubble {
  max-width: 80%;
  min-width: 200px;
  padding: 14px 18px;
  border-radius: 16px;
  font-size: 15px;
  line-height: 1.7;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  transition: transform 0.2s ease;
}
.bubble:hover {
  transform: translateY(-1px);
}
.bubble.user {
  align-self: flex-end;
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  color: white;
  border-bottom-right-radius: 4px;
}
.bubble.assistant {
  align-self: flex-start;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  color: #1a1a1a;
  border-bottom-left-radius: 4px;
  max-width: 90%;
}
.bubble.system {
  align-self: flex-start;
  background: rgba(245, 240, 250, 0.9);
  backdrop-filter: blur(10px);
  color: #595959;
  border-bottom-left-radius: 4px;
  font-size: 13px;
  padding: 8px 14px;
}
.bubble.chart-bubble {
  width: 500px;
  max-width: 90%;
  min-width: unset;
  padding: 12px;
}
.typing {
  color: #8c8c8c;
  letter-spacing: 2px;
}

/* 思考过程分组（append-only 日志） */
.thinking-group {
  align-self: flex-start;
  width: 100%;
  max-width: 760px;
  padding: 4px 0;
}
.thinking-summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #8c8c8c;
  cursor: pointer;
  user-select: none;
  padding: 4px 0;
  transition: color 0.15s ease;
}
.thinking-summary:hover {
  color: #c7000b;
}
.thinking-toggle {
  font-size: 9px;
  width: 10px;
  text-align: center;
}
.thinking-label {
  font-weight: 500;
  letter-spacing: 0.2px;
}
.thinking-entries {
  margin: 4px 0 4px 14px;
  padding: 6px 0 6px 14px;
  border-left: 2px solid rgba(199, 0, 11, 0.15);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.thinking-entry {
  font-size: 12.5px;
  line-height: 1.6;
  color: #595959;
}
.thinking-entry.entry-running {
  color: #c7000b;
}
.thinking-entry.entry-plan {
  color: #595959;
  font-weight: 500;
}
.entry-label {
  word-break: break-all;
  font-family: ui-monospace, SFMono-Regular, "Cascadia Mono", Menlo, monospace;
}

/* AI 回复：直接平铺到页面，不带气泡背景，限读宽 */
.ai-response {
  align-self: flex-start;
  width: 100%;
  max-width: 760px;
  padding: 4px 4px;
  color: #1a1a1a;
  font-size: 15px;
  line-height: 1.75;
}

/* 思考占位提示（无气泡，仅文字） */
.thinking-hint {
  align-self: flex-start;
  padding: 8px 4px;
  color: #8c8c8c;
}

.append-hint {
  margin: 8px 20px;
  padding: 8px 16px;
  background: rgba(230, 247, 255, 0.9);
  backdrop-filter: blur(10px);
  border: 1px solid #91d5ff;
  border-radius: 8px;
  font-size: 13px;
  color: #1890ff;
}

.input-area {
  display: flex;
  gap: 10px;
  padding: 16px 20px;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  border-top: 1px solid rgba(0, 0, 0, 0.08);
  box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.04);
}
.input-box {
  flex: 1;
  padding: 12px 18px;
  border: 1px solid #d9d9d9;
  border-radius: 12px;
  font-size: 15px;
  outline: none;
  transition: all 0.2s ease;
  font-family: 'Inter', sans-serif;
}
.input-box:focus {
  border-color: #c7000b;
  box-shadow: 0 0 0 3px rgba(199, 0, 11, 0.1);
}
.btn-send {
  padding: 12px 24px;
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  color: white;
  border: none;
  border-radius: 12px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s ease;
  box-shadow: 0 2px 6px rgba(199, 0, 11, 0.25);
}
.btn-send:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(199, 0, 11, 0.35);
}
.btn-send:disabled {
  background: #d9d9d9;
  cursor: not-allowed;
  box-shadow: none;
}
.btn-stop {
  padding: 12px 20px;
  background: linear-gradient(135deg, #ff4d4f 0%, #ff7875 100%);
  color: white;
  border: none;
  border-radius: 12px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s ease;
  box-shadow: 0 2px 6px rgba(255, 77, 79, 0.25);
}
.btn-stop:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(255, 77, 79, 0.35);
}

/* Markdown 样式 */
.markdown-body :deep(h1) {
  font-size: 1.6em;
  margin: 0.8em 0;
  font-weight: 700;
  color: #1a1a1a;
}
.markdown-body :deep(h2) {
  font-size: 1.4em;
  margin: 0.7em 0;
  font-weight: 600;
  color: #1a1a1a;
}
.markdown-body :deep(h3) {
  font-size: 1.2em;
  margin: 0.6em 0;
  font-weight: 600;
  color: #1a1a1a;
}
.markdown-body :deep(p) {
  margin: 0.8em 0;
  color: #2a2a2a;
}
.markdown-body :deep(ul) {
  margin: 0.8em 0;
  padding-left: 1.8em;
}
.markdown-body :deep(ol) {
  margin: 0.8em 0;
  padding-left: 1.8em;
}
.markdown-body :deep(li) {
  margin: 0.4em 0;
  color: #2a2a2a;
}
.markdown-body :deep(code) {
  background: #f1f3f5;
  padding: 0.2em 0.5em;
  border-radius: 4px;
  font-size: 0.9em;
  font-family: 'JetBrains Mono', ui-monospace, Consolas, monospace;
  color: #c7000b;
}
.markdown-body :deep(pre) {
  background: #f6f8fa;
  padding: 1em;
  border-radius: 8px;
  overflow-x: auto;
  margin: 1em 0;
  border: 1px solid #e9ecef;
}
.markdown-body :deep(pre code) {
  background: none;
  padding: 0;
  color: #2a2a2a;
}
.markdown-body :deep(blockquote) {
  border-left: 4px solid #c7000b;
  padding-left: 1em;
  margin: 1em 0;
  color: #595959;
  background: rgba(199, 0, 11, 0.03);
  padding: 0.5em 1em;
  border-radius: 0 4px 4px 0;
}
.markdown-body :deep(a) {
  color: #c7000b;
  text-decoration: none;
}
.markdown-body :deep(a:hover) {
  text-decoration: underline;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 1em 0;
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
}
.markdown-body :deep(th) {
  background: #f1f3f5;
  font-weight: 600;
  color: #1a1a1a;
}
.markdown-body :deep(th), .markdown-body :deep(td) {
  border: 1px solid #e9ecef;
  padding: 0.6em 0.8em;
}

/* Responsive Breakpoints */
@media (max-width: 768px) {
  .skill-panel {
    display: none;
  }
  .bubble {
    max-width: 92% !important;
  }
  .topbar {
    padding: 0 12px;
    gap: 8px;
  }
  .brand {
    font-size: 16px;
  }
  .btn-new, .btn-logout {
    padding: 5px 10px;
    font-size: 12px;
  }
  .messages {
    padding: 16px 12px;
  }
  .input-area {
    padding: 12px 10px;
    gap: 8px;
  }
  .btn-send {
    padding: 12px 16px;
  }
  .btn-stop {
    padding: 12px 12px;
  }
}

@media (min-width: 769px) and (max-width: 1024px) {
  .skill-panel {
    width: 200px;
  }
}

</style>
