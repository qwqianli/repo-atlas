<script setup>
defineProps({
  analysis: Object,   // 完整的 analysis dict（含 ai_stats）
})
</script>

<template>
  <div class="stats-bar">
    <span>文件 <b>{{ analysis.files.length }}</b></span>
    <span>依赖边 <b>{{ analysis.edges.length }}</b></span>
    <span v-if="analysis.ai_stats">
      AI 命中 <b>{{ analysis.ai_stats.hit }}</b> · 未命中 <b>{{ analysis.ai_stats.miss }}</b> · 失败
      <b>{{ analysis.ai_stats.failed }}</b>
    </span>
    <span v-if="analysis.ai_stats">
      tokens <b>{{ analysis.ai_stats.tokens_in }}→{{ analysis.ai_stats.tokens_out }}</b>
    </span>
    <span>第三方 <b>{{ analysis.external_all.join(', ') || '无' }}</b></span>
  </div>
</template>
