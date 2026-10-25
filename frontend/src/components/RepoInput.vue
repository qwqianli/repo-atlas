<script setup>
import { ref } from 'vue'

// 只管输入框和按钮；校验、提交、跳转都交给父组件
defineProps({ loading: Boolean })
const emit = defineEmits(['submit'])
const repo = ref('')
const withAI = ref(true)

function go() {
  const path = repo.value.trim()
  if (path) emit('submit', path, withAI.value)
}
</script>

<template>
  <form class="repo-input" @submit.prevent="go">
    <input v-model="repo" class="repo-path" type="text"
           placeholder="C:/path/to/repo 或 https://github.com/user/repo.git" />
    <label class="ai-toggle">
      <input v-model="withAI" type="checkbox" />
      生成 AI 说明
    </label>
    <button type="submit" :disabled="loading || !repo.trim()">
      {{ loading ? '分析中…' : '开始分析' }}
    </button>
  </form>
</template>
