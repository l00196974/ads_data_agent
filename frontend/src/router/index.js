import { createRouter, createWebHistory } from 'vue-router'
import Login from '../pages/Login.vue'
import Chat from '../pages/Chat.vue'
import Artifacts from '../pages/Artifacts.vue'
import Preferences from '../pages/Preferences.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Login },
    { path: '/chat', component: Chat, meta: { requiresAuth: true } },
    { path: '/artifacts', component: Artifacts, meta: { requiresAuth: true } },
    { path: '/preferences', component: Preferences, meta: { requiresAuth: true } },
  ],
})

router.beforeEach((to, from, next) => {
  if (to.meta.requiresAuth && !localStorage.getItem('user_id')) {
    next('/')
  } else {
    next()
  }
})

export default router
