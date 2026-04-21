<template>
  <div class="chat-layout">
    <header class="topbar">
      <span class="brand">华为广告数据助手</span>
      <span class="user-tag">{{ userId }}</span>
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
        <!-- 步骤进展面板 -->
        <div v-if="steps.length" class="step-tracker">
          <div v-for="(step, i) in steps" :key="i" class="step-item">
            <span class="step-icon">{{ step.done ? '✅' : '⏳' }}</span>
            <span>{{ step.msg }}</span>
          </div>
        </div>

        <!-- 消息列表 -->
        <div class="messages" ref="messagesEl">
          <div
            v-for="(msg, i) in messages"
            :key="i"
            :class="['bubble', msg.role]"
          >
            <template v-if="msg.type === 'text'">
              <span class="bubble-text">{{ msg.content }}</span>
            </template>
            <ChartWidget v-else-if="msg.type === 'chart'" :config="msg.content" />
          </div>
          <div v-if="isStreaming && currentToken === ''" class="bubble assistant">
            <span class="typing">...</span>
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
import { SSEClient } from '../utils/sse.js'
import ChartWidget from '../components/ChartWidget.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const router = useRouter()
const userId = localStorage.getItem('user_id') || ''
const messages = ref([])
const steps = ref([])
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
let sseClient = null

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
      body: JSON.stringify({ message: msg }),
    })
    return
  }

  // 新对话
  messages.value.push({ role: 'user', type: 'text', content: msg })
  steps.value = []
  currentToken.value = ''
  isStreaming.value = true

  messages.value.push({ role: 'assistant', type: 'text', content: '' })
  const assistantIdx = messages.value.length - 1

  sseClient = new SSEClient(`/api/chat/${userId}`, {
    step: (data) => {
      steps.value.push({ msg: data.msg, done: data.type === 'tool_end' })
      scrollToBottom()
    },
    token: (data) => {
      currentToken.value += data.token
      messages.value[assistantIdx].content = currentToken.value
      scrollToBottom()
    },
    chart: (data) => {
      messages.value.push({ role: 'assistant', type: 'chart', content: data })
      scrollToBottom()
    },
    interrupt: (data) => {
      isInterrupted.value = true
      isStreaming.value = false
      interruptMsg.value = data.msg || '需要您确认此操作'
      interruptPreview.value = data.preview || []
    },
    done: () => {
      isStreaming.value = false
    },
    error: (data) => {
      messages.value[assistantIdx].content = `错误：${data.error}`
      isStreaming.value = false
    },
  })

  try {
    await sseClient.connect({ message: msg })
  } catch (e) {
    if (e.name !== 'AbortError') {
      messages.value[assistantIdx].content = '连接中断，请重试'
    }
  }
  isStreaming.value = false
}

async function handleConfirm(action) {
  isInterrupted.value = false
  await fetch(`/api/chat/${userId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
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

function logout() {
  localStorage.removeItem('user_id')
  router.push('/')
}
</script>

<style scoped>
.chat-layout { display: flex; flex-direction: column; height: 100vh; background: #f5f5f5; }

.topbar {
  display: flex; align-items: center; gap: 12px;
  padding: 0 24px; height: 52px;
  background: #c7000b; color: white;
}
.brand { font-size: 16px; font-weight: 600; flex: 1; }
.user-tag { font-size: 13px; opacity: 0.85; }
.btn-logout { padding: 4px 14px; background: rgba(255,255,255,0.2); border: none; color: white; border-radius: 4px; cursor: pointer; }

.main { display: flex; flex: 1; overflow: hidden; }

.skill-panel {
  width: 200px; background: white;
  border-right: 1px solid #e8e8e8;
  padding: 16px; overflow-y: auto;
  flex-shrink: 0;
}
.panel-title { font-weight: 600; font-size: 13px; color: #333; margin-bottom: 12px; }
.skill-section { margin-bottom: 16px; }
.section-label { font-size: 11px; color: #999; margin: 0 0 6px; text-transform: uppercase; }
.skill-item { font-size: 13px; color: #555; padding: 4px 0; display: flex; align-items: center; gap: 6px; }
.skill-dot { color: #c7000b; font-size: 8px; }
.add-btn { color: #c7000b; cursor: pointer; }

.chat-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

.step-tracker {
  padding: 8px 16px; background: #fffbe6;
  border-bottom: 1px solid #ffe58f;
  max-height: 120px; overflow-y: auto;
}
.step-item { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #666; padding: 2px 0; }
.step-icon { font-size: 12px; }

.messages {
  flex: 1; overflow-y: auto;
  padding: 16px; display: flex;
  flex-direction: column; gap: 12px;
}
.bubble {
  max-width: 75%; padding: 10px 14px;
  border-radius: 10px; font-size: 14px; line-height: 1.6;
}
.bubble.user {
  align-self: flex-end;
  background: #c7000b; color: white;
  border-bottom-right-radius: 2px;
}
.bubble.assistant {
  align-self: flex-start;
  background: white; color: #333;
  border-bottom-left-radius: 2px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  max-width: 85%;
}
.typing { color: #999; letter-spacing: 2px; }

.append-hint {
  margin: 4px 16px; padding: 6px 12px;
  background: #e6f7ff; border: 1px solid #91d5ff;
  border-radius: 4px; font-size: 12px; color: #1890ff;
}

.input-area {
  display: flex; gap: 8px;
  padding: 12px 16px;
  background: white; border-top: 1px solid #e8e8e8;
}
.input-box {
  flex: 1; padding: 9px 14px;
  border: 1px solid #ddd; border-radius: 6px;
  font-size: 14px; outline: none;
}
.input-box:focus { border-color: #c7000b; }
.btn-send {
  padding: 9px 20px; background: #c7000b;
  color: white; border: none; border-radius: 6px; cursor: pointer;
}
.btn-send:disabled { background: #ddd; cursor: not-allowed; }
.btn-stop {
  padding: 9px 16px; background: #ff4d4f;
  color: white; border: none; border-radius: 6px; cursor: pointer;
}
</style>
