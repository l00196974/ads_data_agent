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
          <div v-for="s in userSkills" :key="s.name" class="skill-item">
            <span class="skill-dot">●</span>{{ s.name }}
          </div>
          <div class="skill-item add-btn">+ 添加</div>
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
          <div
            v-for="(msg, i) in messages"
            :key="i"
            :class="['bubble', msg.role, msg.type === 'chart' ? 'chart-bubble' : '']"
          >
            <template v-if="msg.type === 'text'">
              <div v-if="msg.role === 'user'" class="bubble-text">{{ msg.content }}</div>
              <div v-else class="markdown-body" v-html="renderMd(msg.content)" />
            </template>
            <ChartWidget v-else-if="msg.type === 'chart'" :config="msg.content" />
            <template v-else-if="msg.type === 'plan'">
              <div class="exec-panel">
                <div class="exec-panel-title">📋 执行计划</div>
                <div class="task-list">
                  <div
                    v-for="task in msg.content"
                    :key="task.id"
                    :class="['task-item', task.status]"
                  >
                    <span class="task-icon">
                      {{ task.status === 'done' ? '✅' : task.status === 'running' ? '🔄' : '⬜' }}
                    </span>
                    <span class="task-name">{{ task.name }}</span>
                    <span v-if="task.status === 'running'" class="task-badge">执行中</span>
                  </div>
                </div>
                <div v-if="msg.isLatest && tipText" class="tip-area">
                  <span class="tip-dot">●</span>
                  <span class="tip-text">{{ tipText }}</span>
                </div>
              </div>
            </template>
          </div>
          <div v-if="isStreaming && !messages.some(m => m.type === 'plan')" class="bubble assistant">
            <span class="typing">正在思考...</span>
          </div>
          <div v-if="streamingThought" class="bubble assistant streaming-thought" data-testid="streaming-thought">
            <div class="thought-label">💭 思考流</div>
            <div class="thought-content">{{ streamingThought }}</div>
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
const appendHint = ref(false)
const messagesEl = ref(null)
const currentToken = ref('')
const streamingThought = ref('')
const progressText = ref('')
// conversationId: 同一对话内多轮共享，"新对话"按钮重置为 null（后端会生成新 id）
const conversationId = ref(localStorage.getItem('conversation_id') || null)
const planTasks = ref([])
const tipText = ref('')
let sseClient = null

function renderMd(content) {
  const rawHtml = marked.parse(content)
  return DOMPurify.sanitize(rawHtml)
}

onMounted(async () => {
  try {
    const resp = await fetch(`/api/skills/${userId}`)
    const data = await resp.json()
    systemSkills.value = data.system || []
    userSkills.value = data.user || []
  } catch (_) {}
})

async function sendOrAppend() {
  if (!inputText.value.trim()) return
  const msg = inputText.value.trim()
  inputText.value = ''

  // 执行中：追加要求
  if (isStreaming.value) {
    appendHint.value = true
    setTimeout(() => { appendHint.value = false }, 3000)
    await fetch(`/api/chat/${userId}/append`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, conversation_id: conversationId.value }),
    })
    return
  }

  // 新对话
  messages.value.push({ role: 'user', type: 'text', content: msg })
  currentToken.value = ''
  streamingThought.value = ''
  isStreaming.value = true

  sseClient = new SSEClient(`/api/chat/${userId}`, {
      plan: (data) => {
        // 清除旧 plans 的 isLatest，确保只有最新的 plan 接收更新
        messages.value.forEach(m => { if (m.type === 'plan') m.isLatest = false })

        // 每个任务创建时就包含所有属性，确保 Vue 响应式
        const tasks = data.tasks.map(t => ({
          id: t.id,
          name: t.name,
          status: 'pending'
        }))
        console.log('[plan] created new plan, initial tasks:', tasks.map(t => `${t.name}: ${t.status}`))

        // 新 plan，标记为最新
        const newPlan = {
          role: 'plan',
          type: 'plan',
          content: tasks,
          isLatest: true,
          toolIndex: -1,  // 初始：还没开始任何工具
        }
        console.log('[plan] created new plan, toolIndex =', newPlan.toolIndex, 'tasks =', tasks.length)

        messages.value.push(newPlan)
        scrollToBottom()
      },
      step: (data) => {
        // step 事件只用于更新 tips 文字，不再更新任务状态
        // 任务状态更新完全由 task_update 事件驱动（Agent主动上报，绝对准确）
        tipText.value = data.msg
        scrollToBottom()
      },
      task_update: (data) => {
        // Agent 主动上报任务状态更新
        // data: {task_id, status}，status = pending | running | done
        const { task_id, status } = data

        // 找到最新的 plan 消息
        const planMsg = messages.value.find(msg => msg.type === 'plan' && msg.isLatest)
        if (!planMsg) {
          console.warn('[task_update] no active plan found')
          return
        }

        // 根据 task_id 找到对应任务，更新状态
        // 创建全新数组确保 Vue 响应式更新
        const newTasks = planMsg.content.map(task => {
          if (task.id === task_id) {
            return { ...task, status }
          }
          return task
        })

        planMsg.content = newTasks
        console.log(`[t=${performance.now().toFixed(0)}ms] [task_update] task ${task_id} -> ${status}`)

        scrollToBottom()
      },
      token: (data) => {
        // 累加流式 token，渲染为半透明的"思考流"气泡，给用户实时反馈
        // 一旦最终 text 到达就清空（避免和正式答案重复）
        streamingThought.value += data.token
        // 限长保留最近 800 字，旧内容截断成省略号
        if (streamingThought.value.length > 800) {
          streamingThought.value = '…' + streamingThought.value.slice(-800)
        }
        scrollToBottom()
      },
      text: (data) => {
        // 最终 text 到达：清空思考流，正式气泡入消息流
        streamingThought.value = ''
        messages.value.push({ role: 'assistant', type: 'text', content: data.content })
        scrollToBottom()
      },
      render: (data) => {
        // send_to_user(action="chart") — 插入图表消息
        messages.value.push({ role: 'assistant', type: 'chart', content: {
          type: data.component,
          title: data.title,
          xAxis: { data: data.x_data },
          series: data.series.map(s => ({ name: s.name, type: data.component, data: s.data })),
        }})
        scrollToBottom()
      },
      progress: (data) => {
        progressText.value = data.message
      },
      chart: (data) => {
        // 兼容旧格式（如有）
        messages.value.push({ role: 'assistant', type: 'chart', content: data })
        scrollToBottom()
      },
      interrupt: (data) => {
        isInterrupted.value = true
        isStreaming.value = false
        interruptMsg.value = data.message || data.msg || '需要您确认此操作'
        interruptPreview.value = data.preview || []
      },
      done: () => {
        // 标记当前 plan 不再是最新（隐藏 tips）
        messages.value.forEach(m => { if (m.type === 'plan') m.isLatest = false })
        isStreaming.value = false
        progressText.value = ''
        tipText.value = ''
        streamingThought.value = ''
      },
      error: (data) => {
        messages.value.push({
          role: 'assistant',
          type: 'text',
          content: `错误：${data.error || data.message || String(data)}`,
        })
        messages.value.forEach(m => { if (m.type === 'plan') m.isLatest = false })
        isStreaming.value = false
        progressText.value = ''
        tipText.value = ''
        streamingThought.value = ''
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
  currentToken.value = ''
  streamingThought.value = ''
  isStreaming.value = false
  appendHint.value = false
  planTasks.value = []
  tipText.value = ''
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
.bubble.plan {
  align-self: flex-start;
  background: rgba(255, 251, 230, 0.85);
  backdrop-filter: blur(10px);
  border: 1px solid #ffe58f;
  width: 480px;
  max-width: 92%;
  min-width: unset;
  padding: 14px 16px;
  border-bottom-left-radius: 4px;
}
.exec-panel {
  width: 100%;
}
.exec-panel-title {
  font-size: 12px;
  font-weight: 600;
  color: #8c6a00;
  margin-bottom: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.task-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
}
.task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #595959;
  padding: 6px 10px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.03);
  transition: background 0.2s;
}
.task-item.running {
  background: rgba(199, 0, 11, 0.08);
  color: #c7000b;
  font-weight: 500;
}
.task-item.done {
  color: #389e0d;
  background: rgba(82, 196, 26, 0.06);
}
.task-icon {
  font-size: 14px;
  flex-shrink: 0;
}
.task-name {
  flex: 1;
}
.task-badge {
  font-size: 11px;
  padding: 2px 8px;
  background: rgba(199, 0, 11, 0.12);
  color: #c7000b;
  border-radius: 10px;
  font-weight: 500;
}
.tip-area {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  background: rgba(240, 245, 255, 0.8);
  border-radius: 6px;
  border-left: 3px solid #2f54eb;
}
.tip-dot {
  color: #2f54eb;
  font-size: 8px;
  animation: blink 1s infinite;
  flex-shrink: 0;
}
.tip-text {
  font-size: 12px;
  color: #2f54eb;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.typing {
  color: #8c8c8c;
  letter-spacing: 2px;
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

/* 流式思考气泡 — P2 */
.streaming-thought {
  background: rgba(120, 130, 150, 0.08);
  border: 1px dashed rgba(120, 130, 150, 0.35);
  border-radius: 8px;
  padding: 10px 14px;
  font-style: normal;
  margin: 6px 0;
  max-width: 92%;
}
.streaming-thought .thought-label {
  font-size: 12px;
  color: #8a93a3;
  font-weight: 500;
  margin-bottom: 6px;
  letter-spacing: 0.3px;
}
.streaming-thought .thought-content {
  font-size: 13px;
  line-height: 1.6;
  color: #525a6b;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 110px;
  overflow-y: auto;
  font-family: ui-monospace, SFMono-Regular, "Cascadia Mono", Menlo, monospace;
}
</style>
