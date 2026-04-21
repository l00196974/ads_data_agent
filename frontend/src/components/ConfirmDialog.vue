<template>
  <div v-if="visible" class="overlay">
    <div class="dialog">
      <h3>⚠️ 需要确认</h3>
      <p>{{ message }}</p>
      <ul v-if="preview && preview.length" class="preview-list">
        <li v-for="(item, i) in preview" :key="i">{{ item }}</li>
      </ul>
      <div class="actions">
        <button class="btn-danger" @click="$emit('confirm', 'approve')">确认执行</button>
        <button class="btn-cancel" @click="$emit('confirm', 'cancel')">取消</button>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ visible: Boolean, message: String, preview: Array })
defineEmits(['confirm'])
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
  width: 420px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.15);
}
.dialog h3 { margin: 0 0 12px; font-size: 16px; }
.preview-list { margin: 12px 0; padding-left: 20px; }
.actions { display: flex; gap: 12px; margin-top: 20px; justify-content: flex-end; }
.btn-danger { padding: 8px 20px; background: #c7000b; color: white; border: none; border-radius: 6px; cursor: pointer; }
.btn-cancel { padding: 8px 20px; background: #f0f0f0; border: none; border-radius: 6px; cursor: pointer; }
</style>
