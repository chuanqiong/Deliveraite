<script setup>
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const agentStore = useAgentStore();
const userStore = useUserStore();
const themeStore = useThemeStore();
const route = useRoute();
const router = useRouter();

onMounted(async () => {
  if (userStore.isLoggedIn) {
    // 等待路由准备就绪，确保能正确获取当前页面信息
    await router.isReady();
    
    const isDocGenPage = 
      route.name === 'DocumentGenerator' || 
      route.name === 'ProjectDemoGenerate' || 
      route.path.includes('/generate') ||
      window.location.pathname.includes('/generate');

    // 如果是交付物生成页面，跳过通用智能体初始化，避免加载默认智能体
    if (isDocGenPage) {
      console.log('DocumentGenerator page detected, skipping global agent initialization');
      return;
    }
    await agentStore.initialize();
  }
})
</script>
<template>
  <a-config-provider
    :theme="themeStore.currentTheme"
  >
    <router-view />
  </a-config-provider>
</template>
