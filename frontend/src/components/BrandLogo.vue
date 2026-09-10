<script setup lang="ts">
withDefaults(defineProps<{ compact?: boolean; inverse?: boolean }>(), {
  compact: false,
  inverse: false,
})
</script>

<template>
  <span class="brand-logo" :class="{ compact, inverse }">
    <span class="brand-symbol" aria-hidden="true">
      <span>枢</span>
      <i class="node node-a" />
      <i class="node node-b" />
    </span>
    <span v-if="!compact" class="brand-copy">
      <strong>知枢</strong>
      <small>KNOWLEDGE HUB</small>
    </span>
  </span>
</template>

<style scoped>
.brand-logo{display:inline-flex;align-items:center;gap:11px;color:#172033;line-height:1}.brand-symbol{position:relative;width:39px;height:39px;display:grid;place-items:center;overflow:hidden;color:#fff;background:linear-gradient(145deg,#1677ff,#2852d8);border-radius:12px;box-shadow:0 8px 20px rgba(37,99,235,.28)}.brand-symbol::after{content:"";position:absolute;width:28px;height:1px;right:-10px;top:11px;background:rgba(255,255,255,.48);transform:rotate(-32deg)}.brand-symbol span{position:relative;z-index:1;font-size:19px;font-weight:800}.node{position:absolute;z-index:2;width:4px;height:4px;background:#fff;border-radius:50%;box-shadow:0 0 0 2px rgba(255,255,255,.18)}.node-a{right:6px;top:6px}.node-b{right:5px;bottom:7px}.brand-copy{display:flex;flex-direction:column;gap:4px}.brand-copy strong{font-size:19px;font-weight:800;letter-spacing:.08em}.brand-copy small{color:#8290a7;font-size:8px;font-weight:700;letter-spacing:.16em}.inverse{color:#fff}.inverse .brand-copy small{color:rgba(255,255,255,.65)}.compact .brand-symbol{width:36px;height:36px;border-radius:11px}
</style>
