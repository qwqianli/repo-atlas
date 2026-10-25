<script setup>
import { ref } from 'vue'

const props = defineProps({
  name: String,
  node: Object,     // {type:'dir', children:{}} 或 {type:'file', entry}
  depth: Number,
  selected: String,
})
const emit = defineEmits(['select'])
// 默认只展开第一层：大仓库不至于一屏糊满
const open = ref(props.depth < 1)
</script>

<template>
  <div class="tree-node">
    <div
      v-if="node.type === 'dir'"
      class="tree-row dir"
      :style="{ paddingLeft: depth * 14 + 8 + 'px' }"
      @click="open = !open"
    >
      <span class="arrow">{{ open ? '▾' : '▸' }}</span>{{ name }}/
    </div>
    <div
      v-else
      class="tree-row file"
      :class="{ active: selected === node.entry.path }"
      :style="{ paddingLeft: depth * 14 + 8 + 'px' }"
      :title="node.entry.path"
      @click="emit('select', node.entry.path)"
    >
      {{ name }}
    </div>

    <template v-if="node.type === 'dir' && open">
      <!--
        递归渲染自己：<script setup> 里直接用文件名自引用（Vue 3.3+ 支持）。
        千万不要 import 自己，那是循环引用。
      -->
      <FileTreeNode
        v-for="child in node.children"
        :key="child.name"
        :name="child.name"
        :node="child"
        :depth="depth + 1"
        :selected="selected"
        @select="emit('select', $event)"
      />
    </template>
  </div>
</template>
