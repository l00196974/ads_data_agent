<!-- frontend/src/components/preview/DownloadFallback.vue -->
<template>
  <div class="download-fallback">
    <div class="dl-icon">📄</div>
    <div class="dl-name">{{ name }}</div>
    <a :href="url" :download="name" class="dl-button">下载到本地查看</a>
  </div>
</template>

<script setup>
defineProps({ url: String, name: String })
</script>

<style scoped>
.download-fallback {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 60px 20px;
  height: 100%;
}
.dl-icon { font-size: 48px; }
.dl-name { font-size: 14px; color: #595959; }
.dl-button {
  padding: 8px 20px;
  background: linear-gradient(135deg, #c7000b, #d81a24);
  color: white;
  border-radius: 8px;
  text-decoration: none;
  font-size: 13px;
}
.dl-button:hover { transform: translateY(-1px); }
</style>
