<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Rocket, ChevronRight, Loader2 } from 'lucide-vue-next'
import { projectApi } from '@/apis/project_api'

const router = useRouter()
const isLoading = ref(true)
const projectCount = ref(0)

const fetchProjectCount = async () => {
  try {
    isLoading.value = true
    const res = await projectApi.getProjects({ page: 1, page_size: 1 })
    projectCount.value = res.data?.total || 0
  } catch (err) {
    console.error('获取项目数量失败:', err)
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  fetchProjectCount()
})

const enterDemoMode = () => {
  router.push('/project/demo')
}

const handleAction = () => {
  if (projectCount.value > 0) {
    router.push('/project/list')
  } else {
    router.push({ path: '/project/list', query: { create: 'true' } })
  }
}
</script>

<template>
  <div class="start-page">
    <div class="content-wrapper">
      <!-- 品牌展示区 -->
      <div class="brand-section">
        <div class="logo-box">
          <Rocket :size="32" color="white" stroke-width="2" />
        </div>
        <h1 class="title">智能交付撰写专家</h1>
        <p class="subtitle">
          让AI成为你的项目文档伙伴，从繁杂的项目资料中智能提取关键信息，一键生成专业、标准的项目交付文档。
        </p>
      </div>

      <!-- 功能入口区 -->
      <div class="action-section">
        <div class="action-cards">
          <div class="action-card demo-card" @click="enterDemoMode">
            <div class="card-icon-wrapper">
              <Rocket :size="24" color="#333" />
            </div>
            <div class="card-text">
              <span class="card-title">进入演示模式</span>
              <span class="card-subtitle">预设示例项目</span>
            </div>
          </div>

          <div class="action-card create-card" :class="{ 'is-loading': isLoading }" @click="!isLoading && handleAction()">
            <div class="card-icon-wrapper circle-bg">
              <Loader2 v-if="isLoading" :size="24" color="white" class="animate-spin" />
              <ChevronRight v-else :size="24" color="white" />
            </div>
            <div class="card-text">
              <span class="card-title">{{ isLoading ? '正在加载...' : (projectCount > 0 ? '进入项目列表' : '创建首个项目') }}</span>
              <span class="card-subtitle">{{ isLoading ? '请稍候' : (projectCount > 0 ? '管理现有项目' : '立即开始撰写') }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 功能特点 -->
      <div class="features-section">
        <div class="features-divider"></div>
        <div class="features-list">
          <div class="feature-item">
            <span class="dot"></span>
            结构化分析
          </div>
          <div class="feature-item">
            <span class="dot"></span>
            AI对话生成
          </div>
          <div class="feature-item">
            <span class="dot"></span>
            标准模板转换
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.start-page {
  height: 100vh;
  width: 100vw;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #ffffff;
  overflow: hidden; /* 禁止出现滚动条 */
  font-family: 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
  position: fixed;
  top: 0;
  left: 0;
}

.content-wrapper {
  width: 100%;
  max-width: 1200px;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center; /* 垂直居中 */
  padding: 20px;
  animation: fadeIn 0.8s ease-out;
  box-sizing: border-box;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

.brand-section {
  text-align: center;
  margin-bottom: 5vh; /* 使用 vh 确保间距随高度缩放 */
}

.logo-box {
  width: 64px;
  height: 64px;
  background-color: var(--deloitte-green, #86bc25);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 24px;
  box-shadow: 0 8px 20px rgba(134, 188, 37, 0.25);
}

.title {
  font-size: clamp(24px, 4vh, 32px); /* 响应式字号，防止溢出 */
  font-weight: 600;
  color: var(--deloitte-gray-800, #1a1a1a);
  margin-bottom: 16px;
  letter-spacing: 1px;
}

.subtitle {
  font-size: clamp(14px, 2vh, 16px);
  color: var(--deloitte-gray-500, #666666);
  line-height: 1.6;
  max-width: 640px;
  margin: 0 auto;
}

.action-section {
  width: 100%;
  margin-bottom: 6vh;
}

.action-cards {
  display: flex;
  justify-content: center;
  gap: 32px;
}

.action-card {
  width: clamp(200px, 25vh, 260px); /* 随高度调整大小 */
  height: clamp(200px, 25vh, 260px);
  border-radius: 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  text-align: center;
}

.demo-card {
  background: white;
  border: 1px solid var(--deloitte-gray-100, #f0f0f0);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
}

.demo-card:hover {
  border-color: var(--deloitte-green, #86bc25);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.06);
  transform: translateY(-8px);
}

.create-card {
  background-color: var(--deloitte-green, #86bc25);
  color: white;
  box-shadow: 0 10px 24px rgba(134, 188, 37, 0.2);
}

.create-card:hover {
  background-color: var(--deloitte-green-dark, #79a921);
  box-shadow: 0 16px 40px rgba(134, 188, 37, 0.3);
  transform: translateY(-8px);
}

.create-card.is-loading {
  cursor: not-allowed;
  opacity: 0.8;
}

.create-card.is-loading:hover {
  transform: none;
  box-shadow: 0 10px 24px rgba(134, 188, 37, 0.2);
}

.animate-spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.card-icon-wrapper {
  margin-bottom: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.circle-bg {
  width: 48px;
  height: 48px;
  background: rgba(255, 255, 255, 0.25);
  border-radius: 50%;
}

.card-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.card-title {
  font-size: 18px;
  font-weight: 600;
}

.card-subtitle {
  font-size: 13px;
  opacity: 0.9;
}

.features-section {
  width: 100%;
  max-width: 800px;
}

.features-divider {
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--deloitte-gray-100, #f0f0f0), transparent);
  margin-bottom: 24px;
}

.features-list {
  display: flex;
  justify-content: center;
  gap: 48px;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  color: var(--deloitte-gray-400, #9e9e9e);
}

.dot {
  width: 6px;
  height: 6px;
  background-color: var(--deloitte-green, #86bc25);
  border-radius: 50%;
  opacity: 0.5;
}

@media (max-width: 768px) {
  .start-page {
    overflow-y: auto; /* 极小屏幕允许滚动 */
  }
  
  .content-wrapper {
    height: auto;
    min-height: 100%;
    justify-content: flex-start;
    padding-top: 40px;
  }

  .action-cards {
    flex-direction: column;
    align-items: center;
    gap: 16px;
  }
  
  .action-card {
    width: 100%;
    max-width: 280px;
    height: 160px;
  }
}
</style>