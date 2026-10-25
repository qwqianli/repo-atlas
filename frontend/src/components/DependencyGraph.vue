<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { Graph } from '@antv/g6'

const props = defineProps({
  files: Array,
  edges: Array,
})
const emit = defineEmits(['select'])
const container = ref(null)
let graph = null

// 节点 id 直接用文件 path：图上点一下就能在 files 里查到对应项
function buildData() {
  return {
    nodes: props.files.map((f) => ({ id: f.path })),
    edges: props.edges.map((e) => ({ source: e.source, target: e.target })),
  }
}

onMounted(() => {
  // G6 5.x：全部配置在构造时传入；4.x 的 graph.data()/graph.node() 写法已废弃
  graph = new Graph({
    container: container.value,   // 传 DOM 元素，不是选择器字符串
    autoFit: 'view',
    data: buildData(),
    // nodesep/ranksep 拉开间距：不设的话几十个节点会挤成一条窄带，标签互相压住
    layout: { type: 'dagre', rankdir: 'LR', nodesep: 12, ranksep: 90 },
    node: {
      style: {
        size: 26,
        // 标签超长截断：完整路径右侧面板里有，图上只要能认出文件即可
        labelText: (d) => {
          const name = d.id
          return name.length > 20 ? name.slice(0, 19) + '…' : name
        },
        labelPlacement: 'right',
        labelFontSize: 10,
      },
    },
    edge: {
      style: { endArrow: true },
    },
    behaviors: ['zoom-canvas', 'drag-canvas', 'drag-element'],
  })
  graph.on('node:click', (e) => emit('select', e.target.id))
  graph.render()
})

// 路由复用组件实例时数据会整体换掉，图要跟着重画
watch(
  () => [props.files, props.edges],
  () => {
    if (!graph) return
    graph.setData(buildData())
    graph.render()
  },
)

onUnmounted(() => {
  // 不销毁会残留 canvas 和事件监听，来回切几次任务内存就涨
  if (graph) {
    graph.destroy()
    graph = null
  }
})
</script>

<template>
  <!-- 容器必须有确定尺寸：高度为 0 时 G6 画出来"一片空白"且不报错 -->
  <div ref="container" class="graph-container"></div>
</template>

<style scoped>
.graph-container {
  width: 100%;
  height: 100%;
}
</style>
