<script setup>
import { RouterView } from 'vue-router'
</script>

<template>
  <div class="blank-layout">
    <router-view />
  </div>
</template>

<style scoped>
.blank-layout {
  min-height: 100vh;
}
</style>