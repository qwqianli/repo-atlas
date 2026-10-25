<script setup>
defineProps({
  file: Object,   // files[] 里的一项；没选中时为 null
})
</script>

<template>
  <div class="module-detail">
    <template v-if="file">
      <h3 class="path">{{ file.path }}</h3>
      <dl>
        <dt>角色</dt>
        <dd>{{ file.role || '（未生成）' }}</dd>

        <dt>说明</dt>
        <dd>{{ file.summary || '（未生成）' }}</dd>

        <template v-if="file.key_entities && file.key_entities.length">
          <dt>关键成员</dt>
          <dd>
            <span v-for="name in file.key_entities" :key="name" class="chip">{{ name }}</span>
          </dd>
        </template>

        <dt>内部依赖</dt>
        <dd class="mono">{{ file.internal.join(', ') || '无' }}</dd>

        <dt>第三方依赖</dt>
        <dd class="mono">{{ file.external.join(', ') || '无' }}</dd>
      </dl>
    </template>
    <p v-else class="hint">点击左侧文件或图中节点，这里会显示模块说明。</p>
  </div>
</template>
