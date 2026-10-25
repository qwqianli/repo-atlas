<template>
  <div class="not-found">
    <h1>页面不存在</h1>
    <p class="hint">
      地址可能拼错了；如果是任务页，也可能任务已不在内存里（服务重启会清空任务表）。
    </p>
    <router-link to="/">← 返回首页</router-link>
  </div>
</template>
