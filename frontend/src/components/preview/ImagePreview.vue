<!-- frontend/src/components/preview/ImagePreview.vue -->
<template>
  <div class="image-preview">
    <img :src="url" :alt="name" />
  </div>
</template>

<script setup>
defineProps({ url: String, name: String })
</script>

<style scoped>
.image-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  height: 100%;
  background: #fafbfc;
}
img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
</style>
