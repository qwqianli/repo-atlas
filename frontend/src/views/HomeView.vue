<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import RepoInput from '../components/RepoInput.vue'
import { request } from '../api/client'
import { useAnalysis } from '../composables/useAnalysis'

const router = useRouter()
const { submit, loading, error } = useAnalysis()
const repos = ref([])

async function refreshHistory() {
  try {
    repos.value = await request('/api/repos')
  } catch {
    // 历史列表拉不到不挡主流程：首页还能正常发起分析
    repos.value = []
  }
}

async function onStart(repo, withAI) {
  const taskId = await submit(repo, withAI)
  if (taskId) {
    refreshHistory()
    router.push(`/tasks/${taskId}`)
  }
}

onMounted(refreshHistory)
</script>

<template>
  <div class="home">
    <h1>看懂一个 Python 仓库</h1>
    <p class="hint">
      输入本地仓库路径（或 git 地址），生成文件树、依赖图和每个模块的一句话说明。
    </p>
    <RepoInput :loading="loading" @submit="onStart" />
    <p v-if="error" class="error">{{ error }}</p>

    <section class="history">
      <h2>最近分析</h2>
      <p v-if="repos.length === 0" class="hint">（还没有记录）</p>
      <ul v-else>
        <li v-for="item in repos" :key="item.repo">
          <router-link :to="`/tasks/${item.task_id}`">{{ item.name }}</router-link>
          <span class="meta">{{ item.last_run }} · {{ item.status }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>
