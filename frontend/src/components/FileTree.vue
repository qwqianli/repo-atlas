<script setup>
import { computed } from 'vue'
import FileTreeNode from './FileTreeNode.vue'

const props = defineProps({
  files: Array,      // files 数组（path 是 a/b.py 形式）
  selected: String,  // 当前选中的文件 path
})
const emit = defineEmits(['select'])

// 扁平 path 列表 → 嵌套树。目录节点 {type:'dir', name}，文件节点 {type:'file', name, entry}
// name 存进节点而不是用时从 key 取：递归组件往下传的是节点对象，拿不到父级的 key
const tree = computed(() => {
  const root = { type: 'dir', name: '', children: {} }
  for (const file of props.files) {
    const parts = file.path.split('/')
    let node = root
    for (const part of parts.slice(0, -1)) {
      if (!node.children[part]) {
        node.children[part] = { type: 'dir', name: part, children: {} }
      }
      node = node.children[part]
    }
    const fileName = parts[parts.length - 1]
    node.children[fileName] = { type: 'file', name: fileName, entry: file }
  }
  return root.children
})
</script>

<template>
  <div class="file-tree">
    <FileTreeNode
      v-for="child in tree"
      :key="child.name"
      :name="child.name"
      :node="child"
      :depth="0"
      :selected="selected"
      @select="emit('select', $event)"
    />
  </div>
</template>
