<template>
  <div class="chat-layout">
    <header class="topbar">
      <span class="brand">华为广告数据助手</span>
      <span class="user-tag">{{ userId }}</span>
      <button class="btn-new" @click="$router.push('/artifacts')">📦 我的工作区</button>
      <button class="btn-new" @click="$router.push('/preferences')">⚙️ 设置</button>
      <button class="btn-new" @click="clearChat">新对话</button>
      <button class="btn-logout" @click="logout">退出</button>
    </header>

    <div class="main">
      <!-- 最左侧：历史会话 sidebar -->
      <ConversationSidebar
        ref="sidebarRef"
        :user-id="userId"
        :current-conversation-id="conversationId"
        @select="selectConversation"
        @new-conversation="clearChat"
      />

      <!-- 右侧对话区 -->
      <div class="chat-area">
        <!-- 进度状态栏 -->
        <div v-if="progressText" class="progress-container">
          <div class="progress-bar">
            <div class="progress-indicator"></div>
          </div>
          <span class="progress-text"><span class="progress-dot">●</span> {{ progressText }}</span>
        </div>

        <!-- LLM metrics 实时栏：会话累计 + 本轮 + 实时（模型/速度/上下文）-->
        <div v-if="latestMetrics || sessionTokens.calls" class="metrics-bar">
          <!-- 会话累计：跨多次发问，整个 conversation 的总 tokens 消耗。
               用户最关心"这个会话烧了多少钱"——长对话里这是核心成本指标。 -->
          <span class="metric-group" title="会话 = 跨多次发问的 conversation 总累计（含历史发问）">
            <span class="metric-group-label">会话</span>
            <span class="metric-item">📥 {{ formatTokens(sessionTokens.input) }}</span>
            <span class="metric-item">📤 {{ formatTokens(sessionTokens.output) }}</span>
            <span class="metric-item metric-calls">{{ sessionTokens.calls }} 次</span>
          </span>

          <span class="metric-divider-vert">|</span>

          <!-- 本轮：单次发问到完整回答的所有 LLM 调用累加 -->
          <span class="metric-group" title="本轮 = 一次 user 提问到 agent 完整回答的所有 LLM 调用累加">
            <span class="metric-group-label">本轮</span>
            <span class="metric-item">📥 {{ formatTokens(turnTokens.input) }}</span>
            <span class="metric-item">📤 {{ formatTokens(turnTokens.output) }}</span>
            <span class="metric-item metric-calls">{{ turnTokens.calls }} 次</span>
            <span v-if="turnElapsedMs" class="metric-item metric-elapsed"
                  :title="isStreaming ? '本轮已耗时（跑完后定格）' : '本轮总耗时（用户发问到 agent 完整回答）'">
              ⏱ {{ formatDuration(turnElapsedMs) }}
            </span>
            <span v-if="turnSkills.length" class="metric-item metric-skills"
                  :title="`本轮调用的 skill 子命令（按时序）：${turnSkills.join(' → ')}`">
              🛠 {{ turnSkills.join(' → ') }}
            </span>
          </span>

          <span v-if="latestMetrics" class="metric-divider-vert">|</span>

          <!-- 实时：最近一次 LLM 调用的非累加观测值（模型 / 速度）。
               in/out tokens 已在「会话/本轮」展示，这里不重复 -->
          <span v-if="latestMetrics" class="metric-group" :title="metricsTitleSubagent">
            <span class="metric-item">
              <template v-if="latestMetrics.subagent">🤖 {{ subagentLabel(latestMetrics.subagent) }}</template>
              <template v-else>🧑 主 Agent</template>
            </span>
            <span v-if="latestMetrics.model" class="metric-item metric-model" :title="`模型：${latestMetrics.model}`">
              {{ latestMetrics.model }}
            </span>
            <span v-if="latestMetrics.cache_read_tokens" class="metric-item metric-cache" :title="cacheTitle">
              💾 {{ formatTokens(latestMetrics.cache_read_tokens) }}
            </span>
            <span v-if="latestMetrics.tps" class="metric-item" :title="`首 token 延迟 ${latestMetrics.ttft_ms || '-'} ms`">
              ⚡ {{ latestMetrics.tps.toFixed(1) }} t/s
            </span>
          </span>
          <span v-if="latestMetrics && latestMetrics.context_used_pct != null" class="metric-context"
                @mouseenter="ctxPopoverVisible = true"
                @mouseleave="ctxPopoverVisible = false">
            <span class="ctx-label">🧠 上下文</span>
            <span class="ctx-bar" :class="ctxOverallLevelClass(latestMetrics.context_used_pct)">
              <!-- 4 段堆叠：system / tool_results / history / current -->
              <template v-if="contextSegments">
                <span v-for="seg in contextSegments" :key="seg.key"
                      :class="['ctx-seg', `ctx-seg-${seg.key}`]"
                      :style="{ width: seg.widthPct + '%' }"></span>
              </template>
              <span v-else class="ctx-fill"
                    :class="ctxLevelClass(latestMetrics.context_used_pct)"
                    :style="{ width: Math.min(100, latestMetrics.context_used_pct) + '%' }"></span>
            </span>
            <span class="ctx-pct">{{ latestMetrics.context_used_pct.toFixed(1) }}%</span>

            <!-- 自定义 popover：原生 title 多行显示在不同浏览器不一致，且无法显示
                 配色样式；自己渲染一个深色面板能保证 breakdown 4 段都看得到 -->
            <div v-if="ctxPopoverVisible" class="ctx-popover" @mouseenter.stop>
              <div class="ctx-popover-head">
                <span>当前轮上下文</span>
                <strong>{{ formatTokens(latestMetrics.input_tokens) }} / {{ formatTokens(latestMetrics.context_trigger) }}</strong>
                <span class="ctx-popover-pct">{{ latestMetrics.context_used_pct.toFixed(1) }}%</span>
              </div>
              <div class="ctx-popover-divider"></div>
              <template v-if="contextSegments && contextSegments.length">
                <div class="ctx-popover-section">各部分占用（tiktoken 估算 ±5%）</div>
                <div v-for="seg in contextSegments" :key="seg.key" class="ctx-popover-row">
                  <span :class="['ctx-popover-dot', `ctx-seg-${seg.key}`]"></span>
                  <span class="ctx-popover-label">{{ SEGMENT_LABELS[seg.key] }}</span>
                  <span class="ctx-popover-value">{{ formatTokens(seg.tokens) }}</span>
                  <span class="ctx-popover-share">{{ seg.widthPct.toFixed(1) }}%</span>
                </div>
              </template>
              <div v-else class="ctx-popover-empty">
                breakdown 数据暂不可用（tiktoken 未生效或本轮无 messages）
              </div>
              <div class="ctx-popover-divider"></div>
              <div class="ctx-popover-hint">超过 100% 触发自动压缩 SummarizationMiddleware</div>
            </div>
          </span>
        </div>

        <!-- 消息列表 -->
        <div class="messages" ref="messagesEl">
          <template v-for="(msg, i) in messages" :key="i">
            <!-- 用户消息：保留红色气泡 -->
            <div v-if="msg.type === 'text' && msg.role === 'user'" class="bubble user">
              <div class="bubble-text">{{ msg.content }}</div>
            </div>
            <!-- AI 回答：text + chart 块混排（图表来自 markdown ```chart``` 代码块） -->
            <template v-else-if="msg.type === 'text'">
              <template v-for="(block, j) in parseAiContent(msg.content)" :key="j">
                <div
                  v-if="block.type === 'text'"
                  class="ai-response markdown-body"
                  v-html="renderMd(block.content)"
                />
                <div v-else class="bubble chart-bubble">
                  <ChartWidget :config="block.config" />
                </div>
              </template>
            </template>
            <ArtifactCard
              v-else-if="msg.type === 'artifact'"
              :artifact-id="msg.artifactId"
              :user-id="userId"
              @open="(id) => browsingArtifactId = id"
            />
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
      :tool-name="interruptToolName"
      :risk-level="interruptRiskLevel"
      @confirm="handleConfirm"
    />
    <div v-if="browsingArtifactId" class="artifact-overlay" @click.self="browsingArtifactId = null">
      <div class="artifact-modal">
        <ArtifactBrowser
          :user-id="userId"
          :artifact-id="browsingArtifactId"
          @close="browsingArtifactId = null"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { SSEClient } from '../utils/sse.js'
import ChartWidget from '../components/ChartWidget.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import ArtifactCard from '../components/ArtifactCard.vue'
import ArtifactBrowser from '../components/ArtifactBrowser.vue'
import ConversationSidebar from '../components/ConversationSidebar.vue'

const router = useRouter()
const userId = localStorage.getItem('user_id') || ''
const messages = ref([])
const inputText = ref('')
const isStreaming = ref(false)
const isInterrupted = ref(false)
const interruptMsg = ref('')
const interruptPreview = ref([])
const interruptToolName = ref('')
const interruptRiskLevel = ref('medium')  // 'high' | 'medium'
const appendHint = ref(false)
const messagesEl = ref(null)
const sidebarRef = ref(null)
// 当前流式文本气泡在 messages 中的下标。token 累加到这里，非 token 事件复位。
const currentStreamingIdx = ref(null)
// 当前思考过程分组在 messages 中的下标。所有 plan/step 事件 append 进这个分组。
// 一个对话回合**只有一个** thinking 分组——token 流不再关闭它，避免主 Agent
// 在 task 工具调用前后插入的过渡 token（"我来按步骤完成..."这种）把同一回合的
// step 事件拆成多个"思考过程"分组、用户分不清主/子 Agent 在干啥。
// 真正的回合边界：sendOrAppend 开始时 reset 为 null。
const currentThinkingIdx = ref(null)
const progressText = ref('')
// 最近一次 LLM 调用的 metrics（覆盖式：每次 on_chat_model_end 后端推一条）。
// 顶部状态栏「本次调用」组读取这个对象 —— 含速度 / 上下文进度条（这些是实时观测值，
// 没法累加）。切换会话 / 新对话时 reset 为 null，避免显示别的会话的脏数据。
const latestMetrics = ref(null)

// 本轮（一次 user 提问到 agent 完整回答）所有 LLM 调用的 input/output 累加。
// 后端 call_seq==1 表示新一轮的第一次调用 —— 在这里归零，之后的 metrics 持续累加。
// 累加 input_tokens 是真实"本轮成本"：每次 LLM 调用都按当次 input 计费，即使内容
// 大部分重复（prompt cache 命中部分由 cache_read_tokens 单独显示折扣信号）。
const turnTokens = ref({ input: 0, output: 0, calls: 0 })

// 整个会话累计——跨多次发问，回答 user "这个会话总共花了多少 tokens"。
// 进入历史会话时从 backend metrics summary 初始化，metrics 事件持续追加。
// 新对话 / 切到没历史的会话时归零。
const sessionTokens = ref({ input: 0, output: 0, calls: 0 })

// 本轮调用过的 skill 子命令列表（按调用时序，去重）。
// 数据来源：SSE 'step' 事件 type=tool_start 时 backend 传 skill_subcmd 字段
// （仅 run_command 工具传，task / write_file 等不传）。
// 重置点：sendOrAppend 起点（新一轮）、selectConversation（切会话）。
const turnSkills = ref([])

// 本轮耗时计时器：sendOrAppend 起点 → done 事件终点。
// 跑过程中每 200ms 更新一次 turnElapsedMs（视觉上秒数滚动），跑完定格在最终值。
const turnElapsedMs = ref(0)
let turnStartMs = null
let elapsedTimer = null
function startTurnTimer() {
  turnStartMs = Date.now()
  turnElapsedMs.value = 0
  if (elapsedTimer) clearInterval(elapsedTimer)
  elapsedTimer = setInterval(() => {
    if (turnStartMs) turnElapsedMs.value = Date.now() - turnStartMs
  }, 200)
}
function stopTurnTimer() {
  if (turnStartMs) turnElapsedMs.value = Date.now() - turnStartMs
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}
function formatDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const min = Math.floor(ms / 60_000)
  const sec = Math.floor((ms % 60_000) / 1000)
  return `${min}m${sec}s`
}

const SUBAGENT_CN_LABELS = {
  'data-fetcher': '取数员',
  'data-analyst': '数据分析师',
  'issue-diagnostician': '问题诊断师',
  'general-purpose': '通用助手',
}
function subagentLabel(name) {
  return SUBAGENT_CN_LABELS[name] || name
}
function formatTokens(n) {
  if (n == null) return '-'
  if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k'
  return String(n)
}
function ctxLevelClass(pct) {
  if (pct >= 95) return 'ctx-fill-danger'
  if (pct >= 80) return 'ctx-fill-warning'
  return 'ctx-fill-normal'
}
// 整体进度条边框色：跟随当前轮 context_used_pct 等级（与 ctx-fill 颜色策略一致）
function ctxOverallLevelClass(pct) {
  if (pct >= 95) return 'ctx-bar-danger'
  if (pct >= 80) return 'ctx-bar-warning'
  return ''
}

// 把 breakdown 各部分换算成进度条的 width% —— 基准是 context_trigger 阈值
// （与外层 context_used_pct 同基准），4 段宽度加起来 = ctx_used_pct（最多到 100%）。
const SEGMENT_LABELS = {
  system: '系统提示',
  tool_results: '工具结果',
  history: '历史对话',
  current: '当前问题',
}
const contextSegments = computed(() => {
  const m = latestMetrics.value
  if (!m?.breakdown || !m?.context_trigger) return null
  const trigger = m.context_trigger
  const order = ['system', 'tool_results', 'history', 'current']
  return order
    .map(key => ({
      key,
      tokens: m.breakdown[key] || 0,
      widthPct: ((m.breakdown[key] || 0) * 100 / trigger),
    }))
    .filter(s => s.tokens > 0)
})

// 上下文 breakdown 自定义 popover 显示状态
const ctxPopoverVisible = ref(false)
const cacheTitle = computed(() => {
  const m = latestMetrics.value
  if (!m) return ''
  const cache = m.cache_read_tokens || 0
  const ratio = m.input_tokens ? (cache * 100 / m.input_tokens).toFixed(0) : 0
  return `本轮 LLM 输入 tokens (含 prompt cache 命中 ${ratio}%)`
})
const metricsTitleSubagent = computed(() => {
  const m = latestMetrics.value
  if (!m) return ''
  return m.subagent
    ? `本轮指标来自子 Agent：${subagentLabel(m.subagent)}`
    : '本轮指标来自主 Agent'
})
// conversationId: 同一对话内多轮共享，"新对话"按钮重置为 null（后端会生成新 id）
const conversationId = ref(localStorage.getItem('conversation_id') || null)
const browsingArtifactId = ref(null)
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

// 把 AI 回答的 markdown 文本拆成 text / chart 块。LLM 的图表通过
// ```chart {...JSON...} ``` 代码块嵌入正文，前端在这里解析出来，避免一次单独的
// send_to_user round trip。流式过程中不完整的 ```chart 块会被先隐藏，等闭合后渲染。
function parseAiContent(content) {
  const blocks = []
  const re = /```chart\s*\n([\s\S]*?)\n```/g
  let lastEnd = 0
  for (const m of content.matchAll(re)) {
    if (m.index > lastEnd) {
      blocks.push({ type: 'text', content: content.slice(lastEnd, m.index) })
    }
    try {
      const raw = JSON.parse(m[1])
      blocks.push({ type: 'chart', config: normalizeChartConfig(raw) })
    } catch (_) {
      blocks.push({ type: 'text', content: m[0] })
    }
    lastEnd = m.index + m[0].length
  }
  // 处理流式中途未闭合的 ```chart——隐藏它直到 ``` 闭合到达
  let trailing = content.slice(lastEnd)
  const unclosed = trailing.indexOf('```chart')
  if (unclosed >= 0) trailing = trailing.slice(0, unclosed)
  if (trailing) blocks.push({ type: 'text', content: trailing })
  return blocks
}

// LLM 给的扁平结构 → ChartWidget 期望的形状
function normalizeChartConfig(raw) {
  const type = raw.type || raw.chart_type || 'bar'
  const xData = raw.x_data || raw.x || []
  const series = (raw.series || []).map(s => ({ name: s.name, type: s.type || type, data: s.data }))
  return { type, title: raw.title || '', xAxis: { data: xData }, series }
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
    startTurnTimer()  // 追问 = 新一轮，重新计时
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
  startTurnTimer()

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
      artifact_updated: (data) => {
        closeStreaming()
        closeThinking()
        messages.value.push({
          type: 'artifact',
          artifactId: data.artifact_id,
          action: data.action,
        })
        scrollToBottom()
      },
      step: (data) => {
        closeStreaming()
        // 同一工具的 start/end 是同一条 log 的状态变化，不是两条独立条目。
        // 不用图标，靠 "执行中..." 后缀表达运行态——end 漏报时也只是后缀残留，不会误显示假完成。
        // data.subagent 存在 = 当前工具在某个子 Agent 内部执行，前缀加 "↳" + 缩进，
        // 让用户一眼看出"这是子 Agent 干的活"，不是主 Agent 调的。
        if (data.type === 'tool_start') {
          const indent = data.subagent ? '   ↳ ' : ''
          appendLog({ label: `${indent}${data.msg} 执行中...`, kind: 'running', subagent: data.subagent || null })
          // backend 仅在 run_command 时传 skill_subcmd——按时序去重收集到本轮列表
          if (data.skill_subcmd && !turnSkills.value.includes(data.skill_subcmd)) {
            turnSkills.value.push(data.skill_subcmd)
          }
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
      reasoning_finalize: (data) => {
        // 后端通知：刚才那次 LLM 调用的 content 是中间 reasoning（接着发起了
        // tool_calls），不是最终回复——把流式气泡降级到思考分组里去。
        // streaming 气泡通常在 thinking 分组之**后**（thinking 是 sendOrAppend
        // 入口创建的，token 流晚于 step），所以 splice streaming 不影响 thinking
        // 索引。如果没有 streaming 气泡（content 全空 / 已被收尾），不做事。
        if (currentStreamingIdx.value === null) return
        const reasoning = (data.content || messages.value[currentStreamingIdx.value]?.content || '').trim()
        // 删流式气泡
        messages.value.splice(currentStreamingIdx.value, 1)
        currentStreamingIdx.value = null
        if (!reasoning) return
        // 把 reasoning 摘要放进思考分组（记不住完整文本意义不大，前 200 字够 review）
        const summary = reasoning.length > 200 ? reasoning.slice(0, 200) + '…' : reasoning
        appendLog({ label: `💭 ${summary}`, kind: 'done', subagent: null })
      },
      token: (data) => {
        // 流式 token 累加到当前 assistant 气泡。
        // 故意**不**关闭 thinking 分组——主 Agent 经常在 task 工具调用前先输出
        // 一句过渡话（"我来按步骤完成..."），如果这时关掉 thinking，后续子
        // Agent 内部的 step 事件会另起新分组，前端就出现"思考过程"标题反复
        // 出现，用户分不清是主 Agent 还是子 Agent 的活。
        // 同回合所有 step 都聚合到 sendOrAppend 开始时创建的那个 thinking 分组，
        // 文本气泡按 currentStreamingIdx 单独累积，messages 数组顺序天然保留时序。
        if (currentStreamingIdx.value === null) {
          messages.value.push({ role: 'assistant', type: 'text', content: data.token })
          currentStreamingIdx.value = messages.value.length - 1
        } else {
          messages.value[currentStreamingIdx.value].content += data.token
        }
        scrollToBottom()
      },
      progress: (data) => {
        // progress 不进消息流，仅在顶部状态栏短暂展示
        progressText.value = data.message
      },
      metrics: (data) => {
        // call_seq==1 = 该轮的第一次 LLM 调用 → turnTokens / turnSkills 归零（容错"本轮"边界）。
        // sessionTokens 不归零——它跨多轮累计；只有 clearChat / 切新会话才清零。
        if (data.call_seq === 1) {
          turnTokens.value = { input: 0, output: 0, calls: 0 }
          turnSkills.value = []
        }
        if (typeof data.input_tokens === 'number') {
          turnTokens.value.input += data.input_tokens
          sessionTokens.value.input += data.input_tokens
        }
        if (typeof data.output_tokens === 'number') {
          turnTokens.value.output += data.output_tokens
          sessionTokens.value.output += data.output_tokens
        }
        turnTokens.value.calls += 1
        sessionTokens.value.calls += 1
        // 「实时」组用覆盖式 latestMetrics（模型 / 速度 / 上下文进度条）
        latestMetrics.value = data
      },
      interrupt: (data) => {
        closeStreaming()
        closeThinking()
        isInterrupted.value = true
        // 不要清 isStreaming——SSE 连接还活着，用户 confirm 后后端会继续推。
        // 清成 false 会导致输入框激活、新对话覆盖 sseClient、孤立旧连接。
        interruptMsg.value = data.message || data.msg || '需要您确认此操作'
        interruptPreview.value = data.preview || []
        interruptToolName.value = data.tool_name || ''
        interruptRiskLevel.value = data.risk_level || 'medium'
      },
      done: () => {
        closeStreaming()
        // 不再 done 时自动折叠——用户需要看到调用了哪些工具，不然会以为
        // 模型啥都没干。要折叠用户自己点 ▼。
        closeThinking()
        isStreaming.value = false
        progressText.value = ''
        stopTurnTimer()  // 本轮耗时定格
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
        stopTurnTimer()
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
  // 一轮跑完刷新 sidebar：标题 / last_active_at / message_count 都可能变化
  sidebarRef.value?.refresh()
}

// 切换到历史会话：关掉当前 SSE → 拉历史 messages + events → 重建 messages 数组
async function selectConversation(conv) {
  if (conv.conversation_id === conversationId.value) return  // 同一个直接 no-op
  // 关掉当前流（如有），避免事件流入新会话
  if (isStreaming.value) {
    sseClient?.abort()
    isStreaming.value = false
  }
  stopTurnTimer()
  turnElapsedMs.value = 0
  // 清空当前显示
  messages.value = []
  progressText.value = ''
  currentStreamingIdx.value = null
  currentThinkingIdx.value = null
  appendHint.value = false
  latestMetrics.value = null  // 顶部 metrics 栏复位
  turnTokens.value = { input: 0, output: 0, calls: 0 }
  turnSkills.value = []
  // sessionTokens 从历史 metrics summary 初始化——切到老会话能立即看到累计数
  try {
    const r = await fetch(`/api/chat/${userId}/conversations/${conv.conversation_id}/metrics`)
    const data = await r.json()
    const s = data.summary || {}
    sessionTokens.value = {
      input: s.input_tokens || 0,
      output: s.output_tokens || 0,
      calls: s.calls || 0,
    }
  } catch (_) {
    sessionTokens.value = { input: 0, output: 0, calls: 0 }
  }

  try {
    const resp = await fetch(
      `/api/chat/${userId}/conversations/${conv.conversation_id}/messages`
    )
    const data = await resp.json()
    messages.value = rebuildMessagesFromHistory(data.messages || [], data.events || [])
  } catch (e) {
    console.warn('load conversation failed', e)
    messages.value = [{ role: 'assistant', type: 'text', content: '加载历史失败' }]
  }
  conversationId.value = conv.conversation_id
  localStorage.setItem('conversation_id', conv.conversation_id)
  scrollToBottom()
}

// 把后端返回的 messages + events 重建成前端 messages 数组（与实时模式同 shape）。
// 配对策略：events 按 turn_id 分组（保持首次出现顺序），第 N 个 turn 的事件对应
// 第 N 个 user message——chat 入口每次发问生成一个新 turn_id，与 user message
// 出现顺序天然一一对应。
function rebuildMessagesFromHistory(rawMessages, events) {
  const turnGroups = new Map()  // turn_id -> events[]
  const turnOrder = []           // turn_id 按首次出现顺序
  for (const e of events) {
    if (!turnGroups.has(e.turn_id)) {
      turnGroups.set(e.turn_id, [])
      turnOrder.push(e.turn_id)
    }
    turnGroups.get(e.turn_id).push(e)
  }

  const out = []
  let userIdx = 0
  for (const m of rawMessages) {
    if (m.role === 'user') {
      out.push({ role: 'user', type: 'text', content: m.content })
      // 每个 user 之后插入对应 turn 的思考分组 + artifact card
      const turnId = turnOrder[userIdx]
      const turnEvents = turnId ? turnGroups.get(turnId) : []
      const entries = []
      const artifacts = []
      for (const e of turnEvents) {
        if (e.kind === 'step' && e.payload?.type === 'tool_start') {
          // 历史回看：直接按 'done' 渲染（不再有"执行中..."状态），保留 ↳ 缩进
          const indent = e.payload.subagent ? '   ↳ ' : ''
          entries.push({
            label: `${indent}${e.payload.msg}`,
            kind: 'done',
            subagent: e.payload.subagent || null,
          })
        } else if (e.kind === 'artifact_updated') {
          artifacts.push(e.payload.artifact_id)
        }
      }
      if (entries.length) {
        out.push({ type: 'thinking', entries, collapsed: true })
      }
      for (const aid of artifacts) {
        out.push({ type: 'artifact', artifactId: aid, action: 'created' })
      }
      userIdx++
    } else if (m.role === 'assistant') {
      out.push({ role: 'assistant', type: 'text', content: m.content })
    }
  }
  return out
}

async function handleConfirm(payload) {
  // payload: { action: 'approve'|'cancel', addToAutoApprove: bool }
  // 兼容老用法（直接传字符串）：
  const action = typeof payload === 'string' ? payload : payload.action
  const addToAutoApprove = typeof payload === 'string' ? false : !!payload.addToAutoApprove
  isInterrupted.value = false
  await fetch(`/api/chat/${userId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action,
      session_id: sseClient?.sessionId,
      add_to_auto_approve: addToAutoApprove,
    }),
  })
}

async function stopStream() {
  sseClient?.abort()
  await fetch(`/api/chat/${userId}/cancel`, { method: 'POST' })
  isStreaming.value = false
  stopTurnTimer()  // 用户主动停止也定格本轮耗时
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
  // metrics 栏复位——避免显示上一会话的脏数据
  latestMetrics.value = null
  turnTokens.value = { input: 0, output: 0, calls: 0 }
  turnSkills.value = []
  sessionTokens.value = { input: 0, output: 0, calls: 0 }
  stopTurnTimer()
  turnElapsedMs.value = 0
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
  background: #ffffff;
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

.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: transparent;
}

.step-tracker {
  padding: 12px 20px 16px;
  background: #ffffff;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  border-left: 3px solid #faad14;
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
  background: #ffffff;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  border-left: 3px solid #2f54eb;
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

/* LLM metrics 实时栏：每次 on_chat_model_end 覆盖式更新 */
.metrics-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  padding: 8px 20px;
  background: #fafafa;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  font-size: 12px;
  color: #595959;
  font-family: ui-monospace, SFMono-Regular, "Cascadia Mono", Menlo, monospace;
}
.metric-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.metric-group {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
}
.metric-group-label {
  font-size: 10px;
  color: #8c8c8c;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: 600;
  padding: 1px 6px;
  background: rgba(0, 0, 0, 0.04);
  border-radius: 8px;
}
.metric-divider-vert {
  color: rgba(0, 0, 0, 0.15);
  font-weight: 300;
  margin: 0 2px;
}
.metric-calls {
  color: #8c8c8c;
}
.metric-elapsed {
  /* 等宽数字 + 固定最小宽度，秒数滚动时不抖 */
  font-variant-numeric: tabular-nums;
  min-width: 48px;
  color: #595959;
}
.metric-skills {
  /* 调用链：低对比度灰色，跟其他次要 metric 一致；超过 200px 截断防止挤压布局 */
  color: #595959;
  font-size: 11px;
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.metric-model {
  background: rgba(199, 0, 11, 0.08);
  color: #c7000b;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
}
.metric-cache {
  color: #389e0d;
  font-size: 11px;
  margin-left: 2px;
}
.metric-context {
  position: relative;       /* popover 绝对定位锚点 */
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;        /* 推到右边 */
  cursor: help;
  padding: 4px 0;           /* 加大 hover 命中区，避免鼠标在元素间隙时 popover 闪烁 */
}

/* 上下文 breakdown 自定义 popover */
.ctx-popover {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 100;
  background: #1f2937;
  color: #f3f4f6;
  padding: 14px 16px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 1.6;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
  min-width: 320px;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, "Cascadia Mono", Menlo, monospace;
}
.ctx-popover-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.ctx-popover-head strong {
  font-size: 13px;
  color: #fff;
}
.ctx-popover-pct {
  margin-left: auto;
  font-weight: 600;
  color: #60a5fa;
}
.ctx-popover-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.1);
  margin: 10px -4px;
}
.ctx-popover-section {
  font-size: 11px;
  color: #9ca3af;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}
.ctx-popover-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
}
.ctx-popover-dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex-shrink: 0;
}
.ctx-popover-label {
  flex: 1;
  color: #e5e7eb;
}
.ctx-popover-value {
  color: #fff;
  font-weight: 500;
  min-width: 50px;
  text-align: right;
}
.ctx-popover-share {
  color: #9ca3af;
  min-width: 50px;
  text-align: right;
}
.ctx-popover-empty {
  padding: 8px 0;
  color: #fca5a5;
  font-size: 11px;
}
.ctx-popover-hint {
  font-size: 11px;
  color: #9ca3af;
  font-family: 'Inter', sans-serif;
}
.ctx-label {
  font-size: 11px;
  color: #8c8c8c;
}
.ctx-bar {
  display: inline-flex;     /* flex 让 4 段并排 */
  width: 100px;             /* 加宽以容纳 4 段堆叠 */
  height: 6px;
  background: rgba(0, 0, 0, 0.06);
  border-radius: 3px;
  overflow: hidden;
  box-shadow: inset 0 0 0 1px transparent;
  transition: box-shadow 0.2s ease;
}
.ctx-bar-warning {
  box-shadow: inset 0 0 0 1px #faad14;
}
.ctx-bar-danger {
  box-shadow: inset 0 0 0 1px #ff4d4f;
  animation: ctx-pulse 1s infinite;
}
.ctx-fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}
/* 4 段叠加：颜色与 tooltip 标签对应。无边框无圆角让相邻段无缝拼接 */
.ctx-seg {
  display: block;
  height: 100%;
  transition: width 0.3s ease;
}
.ctx-seg-system      { background: #4096ff; }  /* 蓝：系统提示 */
.ctx-seg-tool_results{ background: #fa8c16; }  /* 橙：工具结果（往往最大） */
.ctx-seg-history     { background: #722ed1; }  /* 紫：历史对话 */
.ctx-seg-current     { background: #52c41a; }  /* 绿：当前问题 */
.ctx-fill-normal {
  background: linear-gradient(90deg, #52c41a, #73d13d);
}
.ctx-fill-warning {
  background: linear-gradient(90deg, #faad14, #ffc53d);
}
.ctx-fill-danger {
  background: linear-gradient(90deg, #ff4d4f, #ff7875);
  animation: ctx-pulse 1s infinite;
}
.ctx-pct {
  min-width: 38px;
  text-align: right;
  font-size: 11px;
  color: #595959;
}
@keyframes ctx-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
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
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.bubble.user {
  align-self: flex-end;
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  color: white;
  border-bottom-right-radius: 4px;
}
.bubble.assistant {
  align-self: flex-start;
  background: #ffffff;
  color: #1a1a1a;
  border-bottom-left-radius: 4px;
  max-width: 90%;
  box-shadow: none;
  border: 1px solid rgba(0, 0, 0, 0.06);
}
.bubble.system {
  align-self: flex-start;
  background: #fafafa;
  color: #595959;
  border-bottom-left-radius: 4px;
  font-size: 13px;
  padding: 8px 14px;
  box-shadow: none;
  border: 1px solid rgba(0, 0, 0, 0.06);
}
.bubble.chart-bubble {
  width: 500px;
  max-width: 90%;
  min-width: unset;
  padding: 12px;
  background: #ffffff;
  box-shadow: none;
  border: 1px solid rgba(0, 0, 0, 0.08);
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
  font-size: 13px;
  line-height: 1.65;
  color: #595959;
}
.thinking-entry.entry-running {
  color: #c7000b;
}
.thinking-entry.entry-plan {
  color: #1a1a1a;
  font-weight: 500;
  /* 计划是多行任务列表，需要保留换行符；font 不用 monospace（中文标题不需要等宽） */
  white-space: pre-line;
}
.thinking-entry.entry-plan .entry-label {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  word-break: normal;
  overflow-wrap: normal;
}
.entry-label {
  /* CLI 命令通常很长，break-word 在 token 边界换行（保留命令可读性）；
     break-all 会从中间硬截断字符，看起来像乱码。 */
  word-break: break-word;
  overflow-wrap: anywhere;
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
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-left: 3px solid #1890ff;
  border-radius: 6px;
  font-size: 13px;
  color: #1890ff;
}

.input-area {
  display: flex;
  gap: 10px;
  padding: 16px 20px;
  background: #ffffff;
  border-top: 1px solid rgba(0, 0, 0, 0.08);
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

.artifact-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.artifact-modal {
  width: 90vw;
  height: 90vh;
  max-width: 1400px;
  background: white;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
}

</style>
