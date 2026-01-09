<script setup>
import { RouterView } from 'vue-router'
import '@/assets/css/project-theme.css'
</script>

<template>
  <div class="project-layout">
    <router-view />
  </div>
</template>

<style scoped>
.project-layout {
  width: 100%;
  min-height: 100vh;
}
</style>