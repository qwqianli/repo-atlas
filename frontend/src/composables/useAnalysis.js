import { onUnmounted, ref } from 'vue'
import { request } from '../api/client'

// 封装"提交 + 轮询"。P2 用轮询；P3 换 SSE 时只改这一个文件，页面不用动。
export function useAnalysis() {
  const task = ref(null)
  const loading = ref(false)
  const error = ref('')
  let timer = null

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    loading.value = false
  }

  async function tick(taskId) {
    try {
      task.value = await request(`/api/tasks/${taskId}`)
      error.value = ''
      if (task.value.status === 'done' || task.value.status === 'failed') {
        stop()
      }
    } catch (e) {
      error.value = e.message
    }
  }

  // 轮询一个已有任务（结果页直接进来的场景）
  function poll(taskId) {
    stop()
    loading.value = true
    error.value = ''
    tick(taskId)
    timer = setInterval(() => tick(taskId), 1000)
  }

  // 提交新任务并开始轮询，返回 task_id（失败返回 null）
  async function submit(repo, withAI = true) {
    loading.value = true
    error.value = ''
    try {
      const data = await request('/api/analyze', {
        method: 'POST',
        body: JSON.stringify({ repo, with_ai: withAI }),
      })
      poll(data.task_id)
      return data.task_id
    } catch (e) {
      loading.value = false
      error.value = e.message
      return null
    }
  }

  // 组件卸载必须停轮询，否则路由切走了还在每秒打接口
  onUnmounted(stop)

  return { task, loading, error, submit, poll, stop }
}
