<script setup>
import { computed, ref, watch } from 'vue'
import { useAnalysis } from '../composables/useAnalysis'
import FileTree from '../components/FileTree.vue'
import DependencyGraph from '../components/DependencyGraph.vue'
import ModuleDetail from '../components/ModuleDetail.vue'
import StatsBar from '../components/StatsBar.vue'

const props = defineProps({ id: String })
const { task, error, poll } = useAnalysis()

// 同一个组件实例会复用于不同 task_id（路由参数变化不重新挂载），所以用 watch 而不是 onMounted
watch(
  () => props.id,
  (id) => {
    if (id) poll(id)
  },
  { immediate: true },
)

// 三栏共用一份选中状态：文件树和依赖图都能选中，右侧面板只认 path
const selectedPath = ref('')
const result = computed(() =>
  task.value && task.value.status === 'done' ? task.value.result : null,
)
const selectedFile = computed(() => {
  if (!result.value) return null
  return result.value.files.find((f) => f.path === selectedPath.value) || null
})

function select(path) {
  selectedPath.value = path
}
</script>

<template>
  <div class="task">
    <p v-if="error" class="error">{{ error }}</p>

    <p v-if="!task" class="hint">正在获取任务…</p>
    <template v-else>
      <div class="task-head">
        <h2>{{ task.name }}</h2>
        <span class="status" :class="task.status">{{ task.status }}</span>
      </div>

      <p v-if="task.status === 'pending' || task.status === 'running'" class="progress-line">
        分析中… 进度 {{ task.progress.done }} / {{ task.progress.total || '?' }}
      </p>

      <p v-if="task.status === 'failed'" class="error">分析失败：{{ task.error }}</p>

      <template v-if="result">
        <StatsBar :analysis="result" />
        <div class="workspace">
          <FileTree :files="result.files" :selected="selectedPath" @select="select" />
          <div class="graph-panel">
            <DependencyGraph :files="result.files" :edges="result.edges" @select="select" />
          </div>
          <ModuleDetail :file="selectedFile" />
        </div>
      </template>
    </template>
  </div>
</template>
