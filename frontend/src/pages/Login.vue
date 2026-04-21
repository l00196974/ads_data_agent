<template>
  <div class="login-container">
    <div class="login-card">
      <div class="logo">华为广告数据助手</div>
      <p class="subtitle">请输入您的账号</p>
      <input
        v-model="userId"
        placeholder="请输入 user_id"
        @keyup.enter="login"
        class="input"
      />
      <button @click="login" :disabled="!userId.trim()" class="btn-primary">
        进入
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const userId = ref('')

function login() {
  if (userId.value.trim()) {
    localStorage.setItem('user_id', userId.value.trim())
    router.push('/chat')
  }
}
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f2f5;
}
.login-card {
  background: white;
  padding: 48px 40px;
  border-radius: 12px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.08);
  width: 360px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.logo {
  font-size: 22px;
  font-weight: 600;
  color: #c7000b;
  text-align: center;
}
.subtitle {
  color: #888;
  text-align: center;
  margin: 0;
}
.input {
  padding: 10px 14px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
  outline: none;
}
.input:focus { border-color: #c7000b; }
.btn-primary {
  padding: 10px;
  background: #c7000b;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 15px;
  cursor: pointer;
}
.btn-primary:disabled { background: #ddd; cursor: not-allowed; }
</style>
