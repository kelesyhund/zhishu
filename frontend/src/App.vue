<template>
  <!-- Path changes remount page-scoped composables; query-only conversation changes do not. -->
  <router-view :key="$route.path" />
</template>
