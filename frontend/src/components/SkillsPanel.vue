<template>
  <div class="skills-panel-comp">
    <div class="skill-section">
      <div class="section-header">
        <span class="section-label">系统 Skills</span>
        <span class="section-count">{{ systemSkills.length }}</span>
      </div>
      <div v-for="s in systemSkills" :key="s.name" class="skill-item" :title="s.description || s.name">
        <span class="skill-dot system-dot">●</span>
        <span class="skill-name">{{ s.name }}</span>
        <span v-if="s.commands?.length" class="skill-cmds" :title="s.commands.join(', ')">
          {{ s.commands.length }} 命令
        </span>
      </div>
      <div v-if="!systemSkills.length" class="empty-tip">无</div>
    </div>

    <div class="skill-section">
      <div class="section-header">
        <span class="section-label">我的 Skills</span>
        <span class="section-count">{{ userSkills.length }}</span>
      </div>
      <div
        v-for="s in userSkills"
        :key="s.name"
        class="skill-item user-skill-item"
        :title="s.description || s.name"
      >
        <span class="skill-dot user-dot">●</span>
        <span class="skill-name">{{ s.name }}</span>
        <span v-if="s.commands?.length" class="skill-cmds">{{ s.commands.length }} 命令</span>
        <button
          v-if="s.type === 'skill_md'"
          class="skill-delete"
          :title="`删除 ${s.name}`"
          @click="deleteSkill(s.name)"
        >×</button>
      </div>
      <button class="add-btn" @click="triggerUpload">+ 上传 skill (.zip)</button>
      <input
        ref="fileInputRef"
        type="file"
        accept=".zip"
        style="display: none"
        @change="onFileChange"
      />
      <div v-if="uploadStatus" class="upload-status">{{ uploadStatus }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onActivated } from 'vue'

const props = defineProps({
  userId: { type: String, required: true },
})

const systemSkills = ref([])
const userSkills = ref([])
const fileInputRef = ref(null)
const uploadStatus = ref('')

async function refreshSkills() {
  try {
    const resp = await fetch(`/api/skills/${props.userId}`)
    const data = await resp.json()
    systemSkills.value = data.system || []
    userSkills.value = data.user || []
  } catch (_) {}
}

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
    const resp = await fetch(`/api/skills/${props.userId}/upload`, { method: 'POST', body: fd })
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
    const resp = await fetch(`/api/skills/${props.userId}/${encodeURIComponent(name)}`, { method: 'DELETE' })
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

// 首次进入 + KeepAlive 后重新进入都刷新
onMounted(refreshSkills)
onActivated(refreshSkills)
</script>

<style scoped>
.skills-panel-comp {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.skill-section {
  background: #fff;
  border: 1px solid #e9ecef;
  border-radius: 8px;
  padding: 16px 18px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f1f3f5;
}
.section-label {
  font-size: 13px;
  font-weight: 600;
  color: #1a1a1a;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  flex: 1;
}
.section-count {
  font-size: 12px;
  color: #8c8c8c;
  background: #f4f4f4;
  padding: 1px 8px;
  border-radius: 10px;
}

.skill-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 6px;
  font-size: 13px;
  color: #444;
  border-radius: 4px;
  transition: background 0.15s;
}
.skill-item:hover {
  background: rgba(199, 0, 11, 0.04);
}
.skill-dot {
  font-size: 8px;
}
.system-dot {
  color: #1890ff;
}
.user-dot {
  color: #c7000b;
}
.skill-name {
  flex: 1;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  color: #1a1a1a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-cmds {
  font-size: 11px;
  color: #8c8c8c;
  background: #f6f8fa;
  padding: 2px 8px;
  border-radius: 10px;
  cursor: help;
}

.user-skill-item {
  position: relative;
}
.skill-delete {
  background: transparent;
  border: none;
  color: #999;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 0 6px;
  border-radius: 4px;
  display: none;
}
.user-skill-item:hover .skill-delete {
  display: inline-block;
}
.skill-delete:hover {
  background: rgba(199, 0, 11, 0.1);
  color: #c7000b;
}

.add-btn {
  margin-top: 6px;
  width: 100%;
  padding: 8px 12px;
  background: rgba(199, 0, 11, 0.06);
  border: 1px dashed rgba(199, 0, 11, 0.4);
  color: #c7000b;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.15s;
}
.add-btn:hover {
  background: rgba(199, 0, 11, 0.1);
}

.upload-status {
  margin-top: 8px;
  padding: 6px 10px;
  background: rgba(199, 0, 11, 0.06);
  border-radius: 4px;
  font-size: 12px;
  color: #595959;
  word-break: break-all;
}

.empty-tip {
  padding: 8px;
  color: #adb5bd;
  font-size: 12px;
  text-align: center;
}
</style>
