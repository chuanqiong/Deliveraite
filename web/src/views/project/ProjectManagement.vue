<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import { demoProjects } from '@/constants/demoData'
import { projectApi } from '@/apis/project_api'
import { 
  LayoutGrid, 
  Search, 
  History, 
  Plus, 
  MoreHorizontal, 
  Briefcase,
  Calendar,
  CheckCircle2,
  X,
  AlignLeft,
  Rocket,
  Trash2,
  ChevronLeft,
  ChevronRight
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const searchQuery = ref('')
const viewMode = ref('card') // 'card' or 'list'
const showModal = ref(false)
const isEdit = ref(false)
const currentProjectId = ref(null)

const projectForm = ref({
  name: '',
  description: '',
  start_date: new Date().toISOString().split('T')[0],
  end_date: ''
})

const isSubmitting = ref(false)
const isLoading = ref(false)
const isDemoMode = computed(() => route.meta.isDemo)

// 分页和列表数据
const projects = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// 获取项目列表
const fetchProjects = async () => {
  if (isDemoMode.value) {
    projects.value = demoProjects
    total.value = demoProjects.length
    return
  }

  isLoading.value = true
  try {
    let res
    if (searchQuery.value) {
      res = await projectApi.searchProjects({
        name: searchQuery.value,
        page: currentPage.value,
        page_size: pageSize.value
      })
    } else {
      res = await projectApi.getProjects({
        page: currentPage.value,
        page_size: pageSize.value
      })
    }
    
    if (res.data) {
      projects.value = res.data.items || []
      total.value = res.data.total || 0
    }
  } catch (err) {
    console.error('获取项目失败:', err)
  } finally {
    isLoading.value = false
  }
}

// 监听路由变化，切换演示/真实数据
watch(() => route.path, () => {
  currentPage.value = 1
  fetchProjects()
}, { immediate: true })

// 监听搜索词变化
let searchTimer = null
watch(searchQuery, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    currentPage.value = 1
    fetchProjects()
  }, 500)
})

const createNewProject = () => {
  isEdit.value = false
  currentProjectId.value = null
  projectForm.value = {
    name: '',
    description: '',
    start_date: new Date().toISOString().split('T')[0],
    end_date: ''
  }
  showModal.value = true
}

const editProject = (project, event) => {
  if (event) event.stopPropagation()
  isEdit.value = true
  currentProjectId.value = project.id
  projectForm.value = {
    name: project.name,
    description: project.description,
    start_date: project.start_date ? project.start_date.split('T')[0] : '',
    end_date: project.end_date ? project.end_date.split('T')[0] : ''
  }
  showModal.value = true
}

const handleSubmit = async () => {
  if (!projectForm.value.name.trim()) {
    message.warning('请输入项目名称')
    return
  }
  
  if (isDemoMode.value) {
    message.info('演示模式下无法保存修改')
    showModal.value = false
    return
  }

  isSubmitting.value = true
  try {
    if (isEdit.value) {
      await projectApi.updateProject(currentProjectId.value, projectForm.value)
      message.success('更新成功')
    } else {
      const res = await projectApi.createProject(projectForm.value)
      message.success('创建成功')
      // 如果是新建，成功后可以跳转
      if (res.data && res.data.id) {
        enterProject(res.data.id)
      }
    }
    showModal.value = false
    fetchProjects()
  } catch (err) {
    console.error('保存项目失败:', err)
    message.error('操作失败，请重试')
  } finally {
    isSubmitting.value = false
  }
}

const handleDeleteProject = async (projectId, event) => {
  if (event) event.stopPropagation()
  // 可以使用 ant-design-vue 的 Modal.confirm，但这里简单起见保持原样或改用 message
  if (!window.confirm('确定要删除该项目吗？此操作不可撤销。')) return
  
  if (isDemoMode.value) {
    projects.value = projects.value.filter(p => p.id !== projectId)
    message.success('演示模式：项目已从列表移除')
    return
  }

  try {
    await projectApi.deleteProject(projectId)
    message.success('删除成功')
    fetchProjects()
  } catch (err) {
    console.error('删除项目失败:', err)
    message.error('删除失败')
  }
}

const handlePageChange = (page) => {
  currentPage.value = page
  fetchProjects()
}

onMounted(() => {
  if (route.query.create === 'true') {
    createNewProject()
  }
})

const enterProject = (projectId) => {
  const pathPrefix = isDemoMode.value ? '/project/demo' : '/project'
  router.push(`${pathPrefix}/${projectId}`)
}
</script>

<template>
  <div class="project-management">
    <div class="content-container">
      <!-- 头部功能区 -->
      <div class="header-section">
        <div class="left-info">
          <div class="page-icon-title">
            <LayoutGrid :size="24" class="brand-icon" />
            <h1>项目管理</h1>
          </div>
          <p class="stats-text">共管理 {{ total }} 个撰写项目</p>
    </div>

    <div class="right-actions">
      <div class="search-wrapper">
        <Search :size="18" class="search-icon" />
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜索项目名称..."
          class="search-input"
        />
      </div>

      <div class="toggle-group">
        <button 
          class="toggle-btn" 
          :class="{ active: viewMode === 'card' }" 
          @click="viewMode = 'card'"
          title="卡片视图"
        >
          <LayoutGrid :size="20" />
        </button>
        <button 
          class="toggle-btn" 
          :class="{ active: viewMode === 'list' }" 
          @click="viewMode = 'list'"
          title="列表视图"
        >
          <History :size="20" />
        </button>
      </div>
    </div>
  </div>

  <!-- 项目展示区 -->
  <div class="projects-display" :class="{ loading: isLoading }">
    <template v-if="projects.length > 0">
      <!-- 卡片视图 -->
      <div v-if="viewMode === 'card'" class="projects-grid">
        <div
          v-for="project in projects"
          :key="project.id"
          class="project-card"
          @click="enterProject(project.id)"
        >
          <div class="card-top">
            <div class="project-avatar">
              <Briefcase :size="24" color="#86BC25" />
            </div>
            <div class="action-buttons">
              <button class="icon-btn" @click="editProject(project, $event)" title="编辑项目">
                <MoreHorizontal :size="18" />
              </button>
              <button class="icon-btn delete" @click="handleDeleteProject(project.id, $event)" title="删除项目">
                <Trash2 :size="18" />
              </button>
            </div>
          </div>

          <div class="card-body">
            <h3 class="project-name">{{ project.name }}</h3>
            <p v-if="project.description" class="project-desc">{{ project.description }}</p>
            <div class="project-meta">
              <div class="meta-item">
                <Calendar :size="14" />
                <span>{{ project.start_date ? project.start_date.split('T')[0] : '未设置' }}</span>
              </div>
              <span class="divider">|</span>
              <div class="meta-item">
                <CheckCircle2 :size="14" />
                <span>{{ project.metadata?.deliverablesCompleted || 0 }}/{{ project.metadata?.deliverablesTotal || 0 }} 个交付物</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 列表视图 -->
      <div v-else class="projects-list">
        <div
          v-for="project in projects"
          :key="project.id"
          class="list-item"
          @click="enterProject(project.id)"
        >
          <div class="item-left">
            <div class="project-avatar small">
              <Briefcase :size="20" color="#86BC25" />
            </div>
            <div class="item-info">
              <h3 class="project-name">{{ project.name }}</h3>
              <p class="project-meta">
                {{ project.start_date ? project.start_date.split('T')[0] : '未设置' }} - {{ project.end_date ? project.end_date.split('T')[0] : '未设置' }}
                <span class="dot"></span>
                交付物: {{ project.metadata?.deliverablesCompleted || 0 }}/{{ project.metadata?.deliverablesTotal || 0 }}
              </p>
            </div>
          </div>
          <div class="item-right">
            <div class="list-actions">
              <button class="list-edit-btn" @click="editProject(project, $event)" title="编辑项目">
                <MoreHorizontal :size="18" />
              </button>
              <button class="list-delete-btn" @click="handleDeleteProject(project.id, $event)" title="删除项目">
                <Trash2 :size="18" />
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 分页控件 -->
      <div v-if="total > pageSize" class="pagination-section">
        <div class="pagination-info">
          第 {{ (currentPage - 1) * pageSize + 1 }} - {{ Math.min(currentPage * pageSize, total) }} 条，共 {{ total }} 条
        </div>
        <div class="pagination-controls">
          <button 
            class="page-btn" 
            :disabled="currentPage === 1" 
            @click="handlePageChange(currentPage - 1)"
          >
            <ChevronLeft :size="18" />
          </button>
          <span class="page-num">{{ currentPage }} / {{ Math.ceil(total / pageSize) }}</span>
          <button 
            class="page-btn" 
            :disabled="currentPage >= Math.ceil(total / pageSize)" 
            @click="handlePageChange(currentPage + 1)"
          >
            <ChevronRight :size="18" />
          </button>
        </div>
      </div>
    </template>

    <!-- 空状态 -->
    <div v-else class="empty-placeholder">
      <div class="empty-box">
        <div class="empty-icon-circle">
          <Search :size="40" color="#e0e0e0" />
        </div>
        <h3>{{ isDemoMode ? '演示模式无数据' : '未找到相关项目' }}</h3>
        <p>{{ isDemoMode ? '演示模式下仅展示预设示例项目' : '您可以尝试更换搜索词或创建新项目' }}</p>
        <button v-if="!isDemoMode" class="primary-btn" @click="createNewProject">
          <Plus :size="18" />
          创建新项目
        </button>
      </div>
    </div>
  </div>
</div>

<!-- 悬浮按钮 (仅在非演示模式显示) -->
<button v-if="!isDemoMode" class="fab-btn" @click="createNewProject" title="创建新项目">
  <Plus :size="32" color="white" />
</button>

<!-- 项目弹窗 (新增/编辑) -->
<div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
  <div class="modal-container">
    <div class="modal-header">
      <div class="header-title-group">
        <div class="icon-circle">
          <Rocket :size="24" />
        </div>
        <div class="header-text">
          <h2>{{ isEdit ? '编辑项目' : '创建新项目' }}</h2>
          <p>{{ isEdit ? '修改项目基本信息' : '开启您的智能文档撰写之旅' }}</p>
        </div>
      </div>
      <button class="close-btn" @click="showModal = false">
        <X :size="24" />
      </button>
    </div>

    <div class="modal-body">
      <div class="input-group">
        <label>
          <Briefcase :size="16" />
          项目名称
        </label>
        <input 
          v-model="projectForm.name" 
          type="text" 
          placeholder="例如：2025年企业数字化转型咨询项目" 
          autofocus
        />
      </div>

      <div class="input-group">
        <label>
          <AlignLeft :size="16" />
          项目描述
        </label>
        <textarea 
          v-model="projectForm.description" 
          placeholder="简要描述项目背景，帮助 AI 更好地理解您的需求" 
          rows="4"
        ></textarea>
      </div>

      <div class="form-grid">
        <div class="input-group">
          <label>
            <Calendar :size="16" />
            开始日期
          </label>
          <input v-model="projectForm.start_date" type="date" />
        </div>
        <div class="input-group">
          <label>
            <Calendar :size="16" />
            预计结束
          </label>
          <input v-model="projectForm.end_date" type="date" />
        </div>
      </div>
    </div>

    <div class="modal-footer">
      <button class="cancel-btn" @click="showModal = false">取消</button>
      <button 
        class="submit-btn" 
        :disabled="isSubmitting" 
        @click="handleSubmit"
      >
        {{ isSubmitting ? '保存中...' : (isEdit ? '保存修改' : '立即创建') }}
      </button>
    </div>
  </div>
</div>
  </div>
</template>

<style scoped>
/* 列表视图操作按钮 */
.list-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.list-edit-btn, .list-delete-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: #8c8c8c;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.list-edit-btn:hover {
  background: #f5f5f5;
  color: #1a1a1a;
}

.list-delete-btn:hover {
  background: #fff1f0;
  color: #ff4d4f;
}

/* 分页样式 */
.pagination-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 32px;
  padding: 16px 0;
  border-top: 1px solid #f0f0f0;
}

.pagination-info {
  font-size: 14px;
  color: #8c8c8c;
}

.pagination-controls {
  display: flex;
  align-items: center;
  gap: 16px;
}

.page-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e8e8e8;
  background: white;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.3s ease;
  color: #595959;
}

.page-btn:disabled {
  background: #f5f5f5;
  color: #bfbfbf;
  cursor: not-allowed;
  border-color: #f0f0f0;
}

.page-btn:not(:disabled):hover {
  border-color: #86BC25;
  color: #86BC25;
}

.page-num {
  font-size: 14px;
  font-weight: 500;
  color: #1a1a1a;
  min-width: 60px;
  text-align: center;
}

/* 加载状态 */
.projects-display.loading {
  opacity: 0.6;
  pointer-events: none;
}

/* 模态框内部选择框和范围滑块 */
.input-group select {
  width: 100%;
  height: 44px;
  padding: 0 12px;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  background: white;
  font-size: 14px;
  color: #1a1a1a;
  outline: none;
}

.input-group select:focus {
  border-color: #86BC25;
}

.input-group input[type="range"] {
  width: 100%;
  height: 6px;
  background: #f0f0f0;
  border-radius: 3px;
  outline: none;
  -webkit-appearance: none;
  margin: 12px 0;
}

.input-group input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  background: #86BC25;
  border-radius: 50%;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(134, 188, 37, 0.3);
}

.project-management {
  height: 100vh;
  width: 100vw;
  background-color: #f8fafb;
  overflow: hidden;
  position: fixed;
  top: 0;
  left: 0;
  font-family: 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
}

.content-container {
  height: 100%;
  width: 100%;
  max-width: 1400px;
  margin: 0 auto;
  padding: 40px;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

/* 头部样式 */
.header-section {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 40px;
}

.page-icon-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.brand-icon {
  color: #86BC25;
}

.header-section h1 {
  font-size: 24px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0;
}

.stats-text {
  font-size: 14px;
  color: #8c8c8c;
  margin: 0;
}

.right-actions {
  display: flex;
  align-items: center;
  gap: 20px;
}

.search-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}

.search-icon {
  position: absolute;
  left: 16px;
  color: #bfbfbf;
}

.search-input {
  width: 320px;
  height: 44px;
  padding: 0 16px 0 44px;
  border: 1px solid #e8e8e8;
  border-radius: 22px;
  background: white;
  font-size: 14px;
  transition: all 0.3s ease;
}

.search-input:focus {
  outline: none;
  border-color: #86BC25;
  box-shadow: 0 0 0 4px rgba(134, 188, 37, 0.1);
}

.toggle-group {
  display: flex;
  background: white;
  padding: 4px;
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.toggle-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: #bfbfbf;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s ease;
}

.toggle-btn.active {
  background: #f0f9eb;
  color: #86BC25;
}

/* 项目展示区 */
.projects-display {
  flex: 1;
  overflow-y: auto;
  padding-right: 8px;
}

.projects-display::-webkit-scrollbar {
  width: 6px;
}

.projects-display::-webkit-scrollbar-thumb {
  background: #e8e8e8;
  border-radius: 3px;
}

/* 卡片样式 */
.projects-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 24px;
}

.project-card {
  background: white;
  border-radius: 24px;
  padding: 32px;
  border: 1px solid #f0f0f0;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  flex-direction: column;
}

.project-card:hover {
  transform: translateY(-6px);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.05);
  border-color: rgba(134, 188, 37, 0.3);
}

.card-top {
  display: flex;
  align-items: center;
  margin-bottom: 24px;
}

.project-avatar {
  width: 56px;
  height: 56px;
  background: #f0f9eb;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: auto;
}

.status-tag {
  padding: 4px 12px;
  background: #f5f5f5;
  color: #8c8c8c;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.status-tag.待启动 {
  background: #f5f5f5;
  color: #8c8c8c;
}

.status-tag.进行中 {
  background: #e6f7ff;
  color: #1890ff;
}

.status-tag.已完成 {
  background: #f6ffed;
  color: #52c41a;
}

.status-tag.已归档 {
  background: #fffbe6;
  color: #faad14;
}

.action-buttons {
  display: flex;
  gap: 8px;
}

.icon-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: #8c8c8c;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s ease;
}

.icon-btn:hover {
  background: #f5f5f5;
  color: #1a1a1a;
}

.icon-btn.delete:hover {
  background: #fff1f0;
  color: #ff4d4f;
}

.list-delete-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: #bfbfbf;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s ease;
  margin-left: 12px;
}

.list-delete-btn:hover {
  background: #fff1f0;
  color: #ff4d4f;
}

.project-name {
  font-size: 16px;
  font-weight: 600;
  color: #262626;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.4;
  height: 44px;
}

.project-desc {
  font-size: 12px;
  color: #8c8c8c;
  line-height: 1.5;
  margin: 8px 0 12px 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  height: 36px;
}

.project-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #bfbfbf;
  font-size: 13px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.divider {
  opacity: 0.3;
}

.card-footer {
  margin-top: 32px;
}

.progress-label {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #8c8c8c;
  margin-bottom: 8px;
}

.progress-percent {
  color: #86BC25;
  font-weight: 600;
}

.progress-track {
  height: 8px;
  background: #f5f5f5;
  border-radius: 4px;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: #86BC25;
  border-radius: 4px;
  transition: width 0.6s ease;
}

/* 列表样式 */
.projects-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.list-item {
  background: white;
  border-radius: 16px;
  padding: 16px 24px;
  border: 1px solid #f0f0f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  transition: all 0.3s ease;
}

.list-item:hover {
  border-color: rgba(134, 188, 37, 0.3);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.02);
}

.item-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.project-avatar.small {
  width: 40px;
  height: 40px;
  border-radius: 10px;
}

.item-info .project-name {
  font-size: 15px;
  margin-bottom: 4px;
}

.item-info .project-meta {
  font-size: 12px;
}

.dot {
  width: 3px;
  height: 3px;
  background: #bfbfbf;
  border-radius: 50%;
  margin: 0 4px;
}

.item-right {
  display: flex;
  align-items: center;
  gap: 24px;
}

.list-progress-wrapper {
  width: 160px;
}

.progress-track.small {
  height: 4px;
}

/* 空状态样式 */
.empty-placeholder {
  height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px dashed #f0f0f0;
  border-radius: 24px;
  background: white;
}

.empty-box {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.empty-icon-circle {
  width: 80px;
  height: 80px;
  background: #fafafa;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 24px;
}

.empty-box h3 {
  font-size: 18px;
  color: #1a1a1a;
  margin: 0 0 8px 0;
}

.empty-box p {
  font-size: 14px;
  color: #8c8c8c;
  margin: 0 0 32px 0;
}

.primary-btn {
  height: 44px;
  padding: 0 24px;
  background: #86BC25;
  color: white;
  border: none;
  border-radius: 22px;
  font-size: 14px;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  transition: all 0.3s ease;
}

.primary-btn:hover {
  background: #75a620;
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(134, 188, 37, 0.3);
}

/* 悬浮按钮 */
.fab-btn {
  position: fixed;
  bottom: 40px;
  right: 40px;
  width: 64px;
  height: 64px;
  background: #86BC25;
  border: none;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 8px 24px rgba(134, 188, 37, 0.4);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 100;
}

.fab-btn:hover {
  background: #75a620;
  transform: scale(1.1);
  box-shadow: 0 6px 20px rgba(134, 188, 37, 0.4);
}

/* Modal Styles */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}

.modal-container {
  width: 100%;
  max-width: 600px;
  background: white;
  border-radius: 16px;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: modal-in 0.3s ease-out;
}

@keyframes modal-in {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.modal-header {
  padding: 24px 32px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-title-group {
  display: flex;
  align-items: center;
  gap: 16px;
}

.icon-circle {
  width: 48px;
  height: 48px;
  background: rgba(134, 188, 37, 0.1);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #86BC25;
}

.header-text h2 {
  font-size: 20px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0 0 4px 0;
}

.header-text p {
  font-size: 13px;
  color: #8c8c8c;
  margin: 0;
}

.close-btn {
  border: none;
  background: transparent;
  color: #bfbfbf;
  cursor: pointer;
  padding: 4px;
  transition: color 0.3s;
}

.close-btn:hover {
  color: #595959;
}

.modal-body {
  padding: 32px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.input-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.input-group label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 500;
  color: #595959;
}

.input-group input,
.input-group textarea {
  width: 100%;
  padding: 12px 16px;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  font-size: 14px;
  background: #fafafa;
  transition: all 0.3s;
}

.input-group input:focus,
.input-group textarea:focus {
  outline: none;
  border-color: #86BC25;
  background: white;
  box-shadow: 0 0 0 3px rgba(134, 188, 37, 0.1);
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.modal-footer {
  padding: 24px 32px;
  background: #fafafa;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  border-top: 1px solid #f0f0f0;
}

.cancel-btn {
  padding: 10px 20px;
  border: 1px solid #d9d9d9;
  background: white;
  border-radius: 8px;
  font-size: 14px;
  color: #595959;
  cursor: pointer;
  transition: all 0.3s;
}

.cancel-btn:hover {
  color: #262626;
  border-color: #8c8c8c;
}

.submit-btn {
  padding: 10px 24px;
  border: none;
  background: #86BC25;
  color: white;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s;
}

.submit-btn:hover {
  background: #75a620;
  box-shadow: 0 4px 12px rgba(134, 188, 37, 0.3);
}

.submit-btn:disabled {
  background: #d9f7be;
  cursor: not-allowed;
  box-shadow: none;
}

@media (max-width: 1024px) {
  .search-input {
    width: 200px;
  }
}

@media (max-width: 768px) {
  .content-container {
    padding: 20px;
  }
  
  .header-section {
    flex-direction: column;
    gap: 20px;
  }
  
  .right-actions {
    width: 100%;
    justify-content: space-between;
  }
  
  .search-input {
    width: 100%;
  }
  
  .item-right {
    display: none;
  }
}
</style>