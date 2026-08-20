<template>
  <div class="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900">
    <div class="w-full max-w-sm">
      <!-- Logo -->
      <div class="text-center mb-8">
        <h1 class="text-2xl font-bold text-white">A 股评分系统</h1>
        <p class="text-sm text-gray-400 mt-2">登录后数据自动同步到云端</p>
      </div>

      <!-- 登录/注册卡片 -->
      <div class="bg-gray-800 border border-gray-700 rounded-xl p-6 shadow-2xl">
        <!-- Tab 切换 -->
        <div class="flex mb-6 border-b border-gray-700">
          <button 
            @click="mode = 'login'"
            :class="[
              'flex-1 pb-2 text-sm font-medium transition-colors',
              mode === 'login' ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-gray-200'
            ]">
            登录
          </button>
          <button 
            @click="mode = 'register'"
            :class="[
              'flex-1 pb-2 text-sm font-medium transition-colors',
              mode === 'register' ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-gray-200'
            ]">
            注册
          </button>
        </div>

        <!-- 表单 -->
        <form @submit.prevent="handleSubmit" class="space-y-4">
          <div>
            <label class="block text-xs text-gray-400 mb-1">用户名</label>
            <input 
              v-model="username"
              type="text"
              placeholder="3-20 个字符"
              class="w-full px-3 py-2 rounded-lg bg-gray-700 border border-gray-600 text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
              required
            >
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-1">密码</label>
            <input 
              v-model="password"
              type="password"
              :placeholder="mode === 'login' ? '输入密码' : '至少 4 个字符'"
              class="w-full px-3 py-2 rounded-lg bg-gray-700 border border-gray-600 text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 text-sm"
              required
            >
          </div>

          <!-- 错误提示 -->
          <div v-if="error" class="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded px-3 py-2">
            {{ error }}
          </div>

          <!-- 提交按钮 -->
          <button 
            type="submit"
            :disabled="loading"
            class="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            {{ loading ? '处理中...' : (mode === 'login' ? '登录' : '注册') }}
          </button>
        </form>

        <!-- 提示 -->
        <p class="text-xs text-gray-500 mt-4 text-center">
          {{ mode === 'login' ? '没有账号？切换到注册' : '已有账号？切换到登录' }}
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { authLogin, authRegister, setToken } from '../api'

const router = useRouter()
const mode = ref('login')
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleSubmit() {
  error.value = ''
  loading.value = true
  
  try {
    const apiFn = mode.value === 'login' ? authLogin : authRegister
    const { data } = await apiFn(username.value, password.value)
    
    // 保存 token 和用户信息
    setToken(data.token, data.user)
    
    // 跳转到首页
    router.push('/')
  } catch (e) {
    error.value = e.response?.data?.detail || '操作失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>
