<template>
  <div v-if="visible" class="overlay">
    <div class="dialog">
      <h3>{{ riskLevel === 'high' ? '⚠️ 高危操作 · 需要确认' : '需要确认' }}</h3>
      <p>{{ message }}</p>
      <div v-if="toolName" class="tool-tag">工具：<code>{{ toolName }}</code></div>
      <ul v-if="preview && preview.length" class="preview-list">
        <li v-for="(item, i) in preview" :key="i">{{ item }}</li>
      </ul>

      <!-- 免确认勾选：medium_risk 显示并可选；high_risk 显示但禁用 + 提示 -->
      <label class="remember-row" :class="{ disabled: riskLevel === 'high' }">
        <input
          type="checkbox"
          v-model="addToAutoApprove"
          :disabled="riskLevel === 'high'"
        />
        <span v-if="riskLevel === 'high'">高危操作必须每次确认（不可设免确认）</span>
        <span v-else>以后不再确认 <code>{{ toolName || '此动作' }}</code>（全局生效）</span>
      </label>

      <div class="actions">
        <button class="btn-danger" @click="emitConfirm('approve')">确认执行</button>
        <button class="btn-cancel" @click="emitConfirm('cancel')">取消</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  visible: Boolean,
  message: String,
  preview: Array,
  toolName: { type: String, default: '' },
  riskLevel: { type: String, default: 'medium' },  // 'high' | 'medium'
})
const emit = defineEmits(['confirm'])

const addToAutoApprove = ref(false)

// 弹窗每次新打开时重置 checkbox——不让上次勾选的状态遗留到新弹窗
watch(() => props.visible, (v) => { if (v) addToAutoApprove.value = false })

function emitConfirm(action) {
  // 取消时也重置，并且不传 add_to_auto_approve（无意义）
  emit('confirm', { action, addToAutoApprove: action === 'approve' && addToAutoApprove.value })
}
</script>

<style scoped>
.overlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center;
  z-index: 100;
}
.dialog {
  background: white;
  padding: 32px;
  border-radius: 10px;
  width: 480px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.15);
}
.dialog h3 { margin: 0 0 12px; font-size: 16px; }
.tool-tag { font-size: 12px; color: #666; margin-bottom: 8px; }
.tool-tag code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: ui-monospace, monospace; }
.preview-list { margin: 12px 0; padding-left: 20px; font-size: 13px; color: #555; }
.remember-row {
  display: flex; align-items: center; gap: 8px;
  margin: 16px 0 0; padding: 10px 12px;
  background: #fafafa; border-radius: 6px;
  font-size: 13px; cursor: pointer;
}
.remember-row.disabled { color: #999; cursor: not-allowed; background: #f5f5f5; }
.remember-row input { cursor: pointer; }
.remember-row.disabled input { cursor: not-allowed; }
.remember-row code { background: #fff; padding: 2px 6px; border-radius: 3px; font-family: ui-monospace, monospace; font-size: 12px; }
.actions { display: flex; gap: 12px; margin-top: 20px; justify-content: flex-end; }
.btn-danger { padding: 8px 20px; background: #c7000b; color: white; border: none; border-radius: 6px; cursor: pointer; }
.btn-cancel { padding: 8px 20px; background: #f0f0f0; border: none; border-radius: 6px; cursor: pointer; }
</style>
