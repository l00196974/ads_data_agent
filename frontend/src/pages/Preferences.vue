<template>
  <div class="page">
    <header class="topbar">
      <span class="brand">⚙️ 设置</span>
      <span class="user-tag">{{ userId }}</span>
      <button class="btn-back" @click="$router.push('/chat')">← 返回对话</button>
    </header>

    <main class="main">
      <!-- 已注册能力（系统 + 用户上传）—— 不是常用信息，从对话主界面挪到这里 -->
      <section class="card">
        <h2 class="card-title">📚 已注册能力</h2>
        <p class="card-desc">系统内置 skill 来自部署方；用户 skill 是上传的 SKILL.md 包。同名时用户覆盖系统。</p>
        <SkillsPanel :user-id="userId" />
      </section>

      <!-- 免确认动作白名单 -->
      <section class="card">
        <h2 class="card-title">🔓 免确认动作</h2>
        <div class="hint">
          当某个动作（如<code>write_file</code>）触发了 HitL 弹窗，你勾选"以后不再确认"后，
          该工具名会被加入下面的全局白名单，下次再触发时会**自动放行**不再打扰。
          <br/>
          <span class="hint-warn">⚠️ 高危动作（high_risk）即使列在下面也不会被自动放行——硬安全约束。</span>
        </div>

        <div v-if="loading" class="state">加载中...</div>
        <div v-else-if="!tools.length" class="empty">
          <div class="empty-icon">📋</div>
          <div>暂无免确认动作</div>
          <div class="empty-hint">在确认弹窗里勾选"以后不再确认 [工具名]"会出现在这里</div>
        </div>
        <ul v-else class="tool-list">
          <li v-for="t in tools" :key="t" class="tool-item">
            <code class="tool-name">{{ t }}</code>
            <button class="btn-revoke" @click="revoke(t)">撤销</button>
          </li>
        </ul>

        <div v-if="lastMsg" class="last-msg">{{ lastMsg }}</div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, onActivated } from 'vue'
import SkillsPanel from '../components/SkillsPanel.vue'

const userId = localStorage.getItem('user_id') || 'anon'
const tools = ref([])
const loading = ref(true)
const lastMsg = ref('')

async function load() {
  loading.value = true
  try {
    const resp = await fetch('/api/chat/preferences/auto_approve')
    const data = await resp.json()
    tools.value = data.tools || []
  } finally {
    loading.value = false
  }
}

async function revoke(name) {
  if (!confirm(`确认撤销 "${name}" 的免确认设置？\n\n撤销后下次该动作触发时会重新弹窗。`)) return
  const resp = await fetch(`/api/chat/preferences/auto_approve/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
  if (resp.ok) {
    const data = await resp.json()
    tools.value = data.tools || []
    lastMsg.value = `已撤销 ${name}`
    setTimeout(() => { lastMsg.value = '' }, 3000)
  } else {
    lastMsg.value = `撤销失败 (HTTP ${resp.status})`
  }
}

// onMounted 处理首次进入；onActivated 处理 KeepAlive 缓存后重新进入（参考 Artifacts.vue）
onMounted(load)
onActivated(load)
</script>

<style scoped>
.page {
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
  background: rgba(255,255,255,0.15);
  border-radius: 12px;
}
.btn-back {
  padding: 6px 16px;
  background: rgba(255,255,255,0.18);
  border: 1px solid rgba(255,255,255,0.3);
  color: white;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.btn-back:hover { background: rgba(255,255,255,0.25); }

.main {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
  max-width: 900px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.card {
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid #e9ecef;
  border-radius: 10px;
  padding: 20px 24px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.03);
}
.card-title {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0 0 6px;
}
.card-desc {
  font-size: 12px;
  color: #8c8c8c;
  margin: 0 0 18px;
}
.hint {
  background: #fff;
  border: 1px solid #e9ecef;
  border-left: 4px solid #c7000b;
  padding: 14px 18px;
  border-radius: 6px;
  font-size: 13px;
  color: #444;
  line-height: 1.7;
  margin-bottom: 24px;
}
.hint code {
  background: #f4f4f4;
  padding: 2px 6px;
  border-radius: 3px;
  font-family: ui-monospace, monospace;
  font-size: 12px;
}
.hint-warn { color: #c7000b; font-weight: 600; }

.state, .empty {
  text-align: center;
  padding: 60px 20px;
  color: #8c8c8c;
}
.empty-icon { font-size: 48px; margin-bottom: 12px; }
.empty-hint { font-size: 12px; margin-top: 6px; color: #adb5bd; }

.tool-list { list-style: none; padding: 0; margin: 0; }
.tool-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  background: white;
  border: 1px solid #e9ecef;
  border-radius: 6px;
  margin-bottom: 8px;
}
.tool-name {
  font-family: ui-monospace, monospace;
  font-size: 13px;
  background: #f4f4f4;
  padding: 4px 10px;
  border-radius: 4px;
}
.btn-revoke {
  padding: 5px 14px;
  background: transparent;
  border: 1px solid #ffccc7;
  color: #c7000b;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: background 0.15s;
}
.btn-revoke:hover { background: #fff1f0; }

.last-msg {
  margin-top: 16px;
  padding: 8px 14px;
  background: #f6ffed;
  border: 1px solid #b7eb8f;
  color: #389e0d;
  border-radius: 6px;
  font-size: 13px;
  text-align: center;
}
</style>
