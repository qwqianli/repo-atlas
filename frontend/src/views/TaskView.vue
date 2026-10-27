<script setup>
import { computed, ref } from 'vue'
import { useAnalysis } from '../composables/useAnalysis'
import { useTaskStream } from '../composables/useTaskStream'
import FileTree from '../components/FileTree.vue'
import DependencyGraph from '../components/DependencyGraph.vue'
import ModuleDetail from '../components/ModuleDetail.vue'
import StatsBar from '../components/StatsBar.vue'
import ReadingPath from '../components/ReadingPath.vue'

const props = defineProps({ id: String })
const { task, error, poll, refresh } = useAnalysis()

// 进度双通道：SSE 为主（数字逐格动），连不上/断流时 useTaskStream 自己调 onFallback
// 退回 poll——复用同一个 useAnalysis 实例，任务状态始终只有一份
const { progress: liveProgress, status: liveStatus, connected, error: streamError } = useTaskStream(props.id, {
  onDone: () => refresh(props.id),      // done 事件不带结果本体，再拉一次任务
  onFallback: () => poll(props.id),
})

// 三栏共用一份选中状态：文件树、依赖图、阅读路线都能选中，右侧面板只认 path
const selectedPath = ref('')
const result = computed(() =>
  task.value && task.value.status === 'done' ? task.value.result : null,
)
const selectedFile = computed(() => {
  if (!result.value) return null
  return result.value.files.find((f) => f.path === selectedPath.value) || null
})

// 展示用的进度：SSE 活着用它的（实时），退回轮询后用任务快照里的
const progress = computed(() => {
  if (connected.value) return liveProgress.value
  return (task.value && task.value.progress) || { done: 0, total: 0 }
})
// total 为 0 时 done/total 是 NaN，宽度样式直接失效——必须兜住
const progressPct = computed(() => {
  const { done, total } = progress.value
  return total ? Math.round((done / total) * 100) : 0
})
const phase = computed(() => liveStatus.value || (task.value && task.value.status) || '')

function select(path) {
  selectedPath.value = path
}
</script>

<template>
  <div class="task">
    <p v-if="error || streamError" class="error">{{ error || streamError }}</p>

    <p v-if="!task && !connected" class="hint">正在获取任务…</p>
    <template v-else>
      <div class="task-head">
        <h2>{{ task ? task.name : '…' }}</h2>
        <span class="status" :class="phase">{{ phase || 'pending' }}</span>
      </div>

      <div v-if="phase === 'pending' || phase === 'running'" class="progress-block">
        <div class="progress-bar">
          <div class="fill" :style="{ width: progressPct + '%' }"></div>
        </div>
        <p class="progress-line">
          分析中… {{ progress.done }} / {{ progress.total || '?' }}（{{ progressPct }}%）
        </p>
      </div>

      <p v-if="phase === 'failed' && task && task.error" class="error">
        分析失败：{{ task.error }}
      </p>

      <template v-if="result">
        <StatsBar :analysis="result" />
        <div class="workspace">
          <FileTree :files="result.files" :selected="selectedPath" @select="select" />
          <div class="graph-panel">
            <DependencyGraph :files="result.files" :edges="result.edges" @select="select" />
          </div>
          <ModuleDetail :file="selectedFile" />
        </div>
        <ReadingPath
          v-if="result.reading_path"
          :reading-path="result.reading_path"
          :files="result.files"
          @select="select"
        />
      </template>
    </template>
  </div>
</template>
