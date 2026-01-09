<script setup>
import { ref } from 'vue'

const teamMembers = ref([
  { id: 1, name: '张三', role: '项目经理', email: 'zhangsan@example.com' },
  { id: 2, name: '李四', role: '前端开发', email: 'lisi@example.com' },
  { id: 3, name: '王五', role: '后端开发', email: 'wangwu@example.com' },
  { id: 4, name: '赵六', role: '测试工程师', email: 'zhaoliu@example.com' }
])
</script>

<template>
  <div class="team-management-page">
    <div class="page-header">
      <h1>团队管理</h1>
    </div>
    
    <div class="team-grid">
      <div class="team-member-card" v-for="member in teamMembers" :key="member.id">
        <div class="member-avatar">
          <div class="avatar-placeholder">{{ member.name.charAt(0) }}</div>
        </div>
        <div class="member-info">
          <h3>{{ member.name }}</h3>
          <p class="role">{{ member.role }}</p>
          <p class="email">{{ member.email }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.team-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1rem;
  margin-top: 1rem;
}

.team-member-card {
  display: flex;
  align-items: center;
  padding: 1.5rem;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.member-avatar {
  margin-right: 1rem;
}

.avatar-placeholder {
  width: 60px;
  height: 60px;
  border-radius: 50%;
  background: #1890ff;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.5rem;
  font-weight: bold;
}

.member-info h3 {
  margin: 0;
  font-size: 1.2rem;
  color: #333;
}

.member-info .role {
  margin: 0.5rem 0;
  font-size: 1rem;
  color: #666;
}

.member-info .email {
  margin: 0;
  font-size: 0.9rem;
  color: #999;
}
</style>