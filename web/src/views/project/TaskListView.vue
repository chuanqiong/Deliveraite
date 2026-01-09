<script setup>
import { ref } from 'vue'

const tasks = ref([
  { id: 1, title: '完成项目需求分析', status: '进行中', priority: '高', assignee: '张三' },
  { id: 2, title: '设计数据库架构', status: '待启动', priority: '中', assignee: '李四' },
  { id: 3, title: '开发前端页面', status: '已完成', priority: '高', assignee: '王五' },
  { id: 4, title: '编写API文档', status: '进行中', priority: '低', assignee: '赵六' }
])
</script>

<template>
  <div class="task-list-page">
    <div class="page-header">
      <h1>任务管理</h1>
    </div>
    
    <div class="task-table">
      <table>
        <thead>
          <tr>
            <th>任务标题</th>
            <th>状态</th>
            <th>优先级</th>
            <th>负责人</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="task in tasks" :key="task.id">
            <td>{{ task.title }}</td>
            <td>
              <span class="status-badge" :class="task.status">
                {{ task.status }}
              </span>
            </td>
            <td>
              <span class="priority-badge" :class="task.priority">
                {{ task.priority }}
              </span>
            </td>
            <td>{{ task.assignee }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.task-table {
  margin-top: 1rem;
}

table {
  width: 100%;
  border-collapse: collapse;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

th, td {
  padding: 1rem;
  text-align: left;
  border-bottom: 1px solid #f0f0f0;
}

th {
  background: #fafafa;
  font-weight: bold;
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

.status-badge.待启动 {
  background: #faad14;
}

.status-badge.已完成 {
  background: #52c41a;
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
</style>