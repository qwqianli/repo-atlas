<script setup>
// 根组件只留外壳：标题栏 + 路由出口，页面内容都在 views/ 里
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <router-link to="/" class="brand">repo-atlas · 项目导读器</router-link>
    </header>
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>
