<script setup>
import { computed } from 'vue'

const props = defineProps({
  readingPath: Object,   // {order, cycles, reason}
  files: Array,          // analysis.files，用来查每个文件的一句话说明
})
const emit = defineEmits(['select'])

// path → role。role 可能是 null（免费档限流没生成），展示层必须容错，不能冒出 undefined
const roleOf = computed(() => {
  const map = {}
  for (const f of props.files || []) {
    map[f.path] = f.role
  }
  return map
})

// 环成员集合：编号列表里全量保留，只加标记，不在下方重复列一遍文件名
const cyclePaths = computed(() => {
  const set = new Set()
  for (const group of props.readingPath.cycles || []) {
    for (const p of group) set.add(p)
  }
  return set
})

const cycleFileCount = computed(() => cyclePaths.value.size)
</script>

<template>
  <section class="reading-path">
    <h3>建议阅读路线</h3>
    <p class="hint">{{ readingPath.reason }}</p>
    <ol class="rp-list">
      <li v-for="(path, i) in readingPath.order" :key="path" @click="emit('select', path)">
        <span class="rp-idx">{{ i + 1 }}</span>
        <span class="rp-path">{{ path }}</span>
        <span v-if="cyclePaths.has(path)" class="rp-tag">循环依赖</span>
        <span v-if="roleOf[path]" class="rp-role">{{ roleOf[path] }}</span>
        <span v-else class="rp-role missing">（未生成说明）</span>
      </li>
    </ol>

    <div v-if="readingPath.cycles && readingPath.cycles.length" class="cycles">
      <h4>循环依赖（无法确定先后）</h4>
      <p class="hint">
        检测到 {{ readingPath.cycles.length }} 组互相 import 的模块（共
        {{ cycleFileCount }} 个文件），它们之间没有严格的先后顺序。
        这些文件已在上方列表里连续排在一起并标了「循环依赖」：
        挑其中一个当入口读进去，读完再回来看另一个。
      </p>
    </div>
  </section>
</template>
