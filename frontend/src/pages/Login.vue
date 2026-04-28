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
  background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}
.login-card {
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  padding: 56px 48px;
  border-radius: 20px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
  width: 400px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  border: 1px solid rgba(255, 255, 255, 0.8);
}
.logo {
  font-size: 26px;
  font-weight: 700;
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  text-align: center;
  letter-spacing: 0.5px;
}
.subtitle {
  color: #8c8c8c;
  text-align: center;
  margin: 0 0 12px;
  font-size: 15px;
}
.input {
  padding: 14px 18px;
  border: 1px solid #d9d9d9;
  border-radius: 12px;
  font-size: 15px;
  outline: none;
  transition: all 0.2s ease;
  font-family: 'Inter', sans-serif;
}
.input:focus {
  border-color: #c7000b;
  box-shadow: 0 0 0 3px rgba(199, 0, 11, 0.1);
}
.btn-primary {
  padding: 14px;
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  color: white;
  border: none;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 4px 12px rgba(199, 0, 11, 0.25);
  margin-top: 8px;
}
.btn-primary:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(199, 0, 11, 0.35);
}
.btn-primary:disabled {
  background: #d9d9d9;
  cursor: not-allowed;
  box-shadow: none;
  transform: none;
}

@media (max-width: 480px) {
  .login-card {
    width: 90%;
    padding: 40px 28px;
  }
}
</style>
