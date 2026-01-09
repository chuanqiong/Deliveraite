<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const task = ref({
  id: 1,
  title: '完成项目需求分析',
  description: '详细分析项目需求，制定开发计划',
  status: '进行中',
  priority: '高',
  assignee: '张三',
  createTime: '2024-05-20',
  deadline: '2024-05-25'
})

onMounted(() => {
  const taskId = route.params.id
  console.log('查看任务详情:', taskId)
})
</script>

<template>
  <div class="task-detail-page">
    <div class="page-header">
      <h1>任务详情</h1>
    </div>
    
    <div class="task-detail-card">
      <div class="task-header">
        <h2>{{ task.title }}</h2>
        <span class="status-badge" :class="task.status">
          {{ task.status }}
        </span>
      </div>
      
      <div class="task-info">
        <div class="info-item">
          <label>优先级:</label>
          <span class="priority-badge" :class="task.priority">
            {{ task.priority }}
          </span>
        </div>
        <div class="info-item">
          <label>负责人:</label>
          <span>{{ task.assignee }}</span>
        </div>
        <div class="info-item">
          <label>创建时间:</label>
          <span>{{ task.createTime }}</span>
        </div>
        <div class="info-item">
          <label>截止日期:</label>
          <span>{{ task.deadline }}</span>
        </div>
      </div>
      
      <div class="task-description">
        <label>任务描述:</label>
        <p>{{ task.description }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.task-detail-card {
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  padding: 2rem;
  margin-top: 1rem;
}

.task-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2rem;
}

.task-header h2 {
  margin: 0;
  font-size: 1.5rem;
  color: #333;
}

.task-info {
  margin-bottom: 2rem;
}

.info-item {
  display: flex;
  margin-bottom: 1rem;
}

.info-item label {
  width: 100px;
  font-weight: bold;
  color: #666;
}

.info-item span {
  color: #333;
}

.status-badge {
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

.priority-badge {
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: bold;
  color: white;
}

.priority-badge.高 {
  background: #f5222d;
}

.priority-badge.中 {
  background: #faad14;
}

.priority-badge.低 {
  background: #52c41a;
}

.task-description label {
  display: block;
  font-weight: bold;
  color: #666;
  margin-bottom: 0.5rem;
}

.task-description p {
  color: #333;
  line-height: 1.6;
}
</style>