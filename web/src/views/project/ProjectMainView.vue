<script setup>
import { ref } from 'vue'

// 项目统计数据
const projectStats = ref({
  totalProjects: 12,
  activeProjects: 8,
  completedProjects: 4,
  pendingTasks: 24
})

// 最近更新的项目
const recentProjects = ref([
  { id: 1, name: '智能体平台开发', status: '进行中', updateTime: '2024-05-20', progress: 75 },
  { id: 2, name: '知识图谱构建', status: '进行中', updateTime: '2024-05-19', progress: 60 },
  { id: 3, name: '文档处理系统', status: '已完成', updateTime: '2024-05-18', progress: 100 },
  { id: 4, name: '数据分析平台', status: '待启动', updateTime: '2024-05-17', progress: 0 }
])
</script>

<template>
  <div class="project-home">
    <div class="page-header">
      <h1>项目概览</h1>
      <p>欢迎使用项目管理系统</p>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon">📊</div>
        <div class="stat-content">
          <h3>总项目数</h3>
          <p>{{ projectStats.totalProjects }}</p>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon">🚀</div>
        <div class="stat-content">
          <h3>进行中项目</h3>
          <p>{{ projectStats.activeProjects }}</p>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon">✅</div>
        <div class="stat-content">
          <h3>已完成项目</h3>
          <p>{{ projectStats.completedProjects }}</p>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon">📋</div>
        <div class="stat-content">
          <h3>待办任务</h3>
          <p>{{ projectStats.pendingTasks }}</p>
        </div>
      </div>
    </div>

    <!-- 最近项目 -->
    <div class="recent-projects">
      <h2>最近更新的项目</h2>
      <div class="project-list">
        <div class="project-item" v-for="project in recentProjects" :key="project.id">
          <div class="project-info">
            <h3>{{ project.name }}</h3>
            <p>更新时间: {{ project.updateTime }}</p>
          </div>
          <div class="project-status">
            <span class="status-badge" :class="project.status">
              {{ project.status }}
            </span>
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: project.progress + '%' }"></div>
            </div>
            <span class="progress-text">{{ project.progress }}%</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-header {
  margin-bottom: 2rem;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}

.stat-card {
  display: flex;
  align-items: center;
  padding: 1.5rem;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.stat-icon {
  font-size: 2rem;
  margin-right: 1rem;
}

.stat-content h3 {
  margin: 0;
  font-size: 0.9rem;
  color: #666;
}

.stat-content p {
  margin: 0.5rem 0 0;
  font-size: 1.5rem;
  font-weight: bold;
  color: #333;
}

.recent-projects {
  margin-top: 2rem;
}

.project-list {
  margin-top: 1rem;
}

.project-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem;
  margin-bottom: 0.5rem;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.project-info h3 {
  margin: 0;
  font-size: 1.1rem;
  color: #333;
}

.project-info p {
  margin: 0.5rem 0 0;
  font-size: 0.9rem;
  color: #666;
}

.status-badge {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: bold;
  color: white;
}

.status-badge.进行中 {
  background: #1890ff;
}

.status-badge.已完成 {
  background: #52c41a;
}

.status-badge.待启动 {
  background: #faad14;
}

.progress-bar {
  width: 100px;
  height: 8px;
  background: #f0f0f0;
  border-radius: 4px;
  margin: 0.5rem 0;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: #1890ff;
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 0.8rem;
  color: #666;
}
</style>