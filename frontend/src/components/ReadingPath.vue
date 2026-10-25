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
</script>

<template>
  <section class="reading-path">
    <h3>建议阅读路线</h3>
    <p class="hint">{{ readingPath.reason }}</p>
    <ol class="rp-list">
      <li v-for="(path, i) in readingPath.order" :key="path" @click="emit('select', path)">
        <span class="rp-idx">{{ i + 1 }}</span>
        <span class="rp-path">{{ path }}</span>
        <span v-if="roleOf[path]" class="rp-role">{{ roleOf[path] }}</span>
        <span v-else class="rp-role missing">（未生成说明）</span>
      </li>
    </ol>

    <div v-if="readingPath.cycles && readingPath.cycles.length" class="cycles">
      <h4>循环依赖（无法确定先后）</h4>
      <p class="hint">这些模块互相 import，没有严格先后；挑一个当入口读进去，再回来看另一个。</p>
      <div v-for="group in readingPath.cycles" :key="group.join(' ⇄ ')" class="cycle-group">
        <span
          v-for="p in group"
          :key="p"
          class="chip clickable"
          @click="emit('select', p)"
        >{{ p }}</span>
      </div>
    </div>
  </section>
</template>
