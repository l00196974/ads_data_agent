<template>
  <!-- KeepAlive 让 Chat / Artifacts 组件在路由切换时被缓存，state 不丢失。
       Artifacts.vue 用 onActivated 钩子在每次重新激活时刷新列表。 -->
  <RouterView v-slot="{ Component }">
    <KeepAlive>
      <component :is="Component" />
    </KeepAlive>
  </RouterView>
</template>

<script setup>
import { RouterView } from 'vue-router'
</script>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
</style>
