import { onUnmounted, ref } from 'vue'

// SSE 实时进度。onDone 在收到 done 事件时回调（结果本体要另拉一次任务）；
// onFallback 在 SSE 连不上 / 断流时回调（调用方通常传 useAnalysis 的 poll，退回轮询）。
export function useTaskStream(taskId, { onDone, onFallback } = {}) {
  const progress = ref({ done: 0, total: 0 })
  const status = ref('')
  const error = ref('')
  const connected = ref(false)
  let es = null        // EventSource 放普通变量：塞进 ref 会被 Vue 深层代理搅出怪毛病
  let watchdog = null
  let closed = false

  function close() {
    closed = true
    connected.value = false
    if (es) {
      es.close()
      es = null
    }
    if (watchdog) {
      clearTimeout(watchdog)
      watchdog = null
    }
  }

  // SSE 链路坏了：关流并让调用方接管（轮询兜底）
  function fallback(reason) {
    if (closed) return
    close()
    error.value = reason
    if (onFallback) onFallback()
  }

  function handle(event) {
    if (watchdog) {
      clearTimeout(watchdog)
      watchdog = null
    }
    if (event.type === 'snapshot') {
      status.value = event.status
      progress.value = event.progress || { done: 0, total: 0 }
    } else if (event.type === 'progress') {
      progress.value = { done: event.done, total: event.total }
    } else if (event.type === 'done') {
      status.value = 'done'
      close()   // 必须主动关：不关的话服务端结束流后 EventSource 会无限自动重连
      if (onDone) onDone()
    } else if (event.type === 'failed') {
      status.value = 'failed'
      error.value = event.error || '分析失败'
      close()
    }
  }

  es = new EventSource(`/api/tasks/${taskId}/stream`)
  // 3 秒内一条事件都没收到 = 链路有问题（典型：代理把 SSE 吞了），退回轮询
  watchdog = setTimeout(() => fallback('SSE 连接异常，已退回轮询'), 3000)
  es.onmessage = (e) => {
    connected.value = true
    handle(JSON.parse(e.data))
  }
  es.onerror = () => {
    // 不依赖 EventSource 的自动重连：那会掩盖真实故障，还会每轮新建一个订阅队列
    if (!closed) fallback('SSE 连接断开，已退回轮询')
  }

  onUnmounted(close)
  return { progress, status, error, connected, close }
}
