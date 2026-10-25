<script setup>
// 根组件只留外壳：标题栏 + 路由出口，页面内容都在 views/ 里
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <router-link to="/" class="brand">repo-atlas · 项目导读器</router-link>
    </header>
    <main class="app-main">
      <!-- key 绑 fullPath：同一个路由组件在不同 task_id 之间切换时强制重建，
           让 useTaskStream / useAnalysis 这类 composable 干净地重新初始化 -->
      <router-view :key="$route.fullPath" />
    </main>
  </div>
</template>
