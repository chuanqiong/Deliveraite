<script setup>
import { ref } from 'vue'

const reportData = ref({
  totalTasks: 100,
  completedTasks: 75,
  pendingTasks: 25,
  onTimeRate: 85
})
</script>

<template>
  <div class="report-statistics-page">
    <div class="page-header">
      <h1>报表统计</h1>
    </div>
    
    <div class="report-grid">
      <div class="report-card">
        <h2>总任务数</h2>
        <p>{{ reportData.totalTasks }}</p>
      </div>
      <div class="report-card">
        <h2>已完成任务</h2>
        <p>{{ reportData.completedTasks }}</p>
      </div>
      <div class="report-card">
        <h2>待完成任务</h2>
        <p>{{ reportData.pendingTasks }}</p>
      </div>
      <div class="report-card">
        <h2>按时完成率</h2>
        <p>{{ reportData.onTimeRate }}%</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.report-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1rem;
  margin-top: 2rem;
}

.report-card {
  padding: 1.5rem;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.report-card h2 {
  margin: 0;
  font-size: 1.2rem;
  color: #666;
}

.report-card p {
  margin: 1rem 0 0;
  font-size: 2rem;
  font-weight: bold;
  color: #333;
}
</style>