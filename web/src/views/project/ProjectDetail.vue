<script setup>
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { demoProjects } from '@/constants/demoData'
import { projectApi } from '@/apis/project_api'
import { fileApi, documentApi, databaseApi } from '@/apis/knowledge_api'
import { 
  ChevronLeft, 
  ChevronRight,
  Edit3, 
  Upload, 
  FileText, 
  CheckCircle2, 
  AlertCircle,
  Download,
  Trash2,
  ExternalLink,
  Sparkles,
  FileSearch,
  Briefcase,
  Loader2,
  RefreshCw,
  Wand2,
  Search
} from 'lucide-vue-next'
import { Modal } from 'ant-design-vue'

const route = useRoute()
const router = useRouter()
const projectId = route.params.id
const isDemoMode = computed(() => route.meta.isDemo === true)
const KB_ID = 'kb_0f7ffb5eec05a6132546d7f26a7fd32b'
let pollingTimer = null

const activeTab = ref('list')

const goBack = () => {
  if (isDemoMode.value) {
    router.push('/project/demo')
  } else {
    router.push('/project/list')
  }
}

const project = ref(isDemoMode.value ? (demoProjects.find(p => p.id === Number(projectId)) || demoProjects[0]) : {
  id: projectId,
  name: '加载中...',
  description: '',
  startDate: '',
  endDate: '',
  status: '待启动',
  deliverablesCompleted: 0,
  deliverablesTotal: 0
})

const isLoading = ref(false)
const fetchProjectDetail = async () => {
  if (isDemoMode.value) return
  
  isLoading.value = true
  try {
    const response = await projectApi.getProject(projectId)
    if (response.data) {
      const data = response.data
      // 确保 metadata 存在且包含 kb_files
      const metadata = data.metadata || {}
      if (!metadata.kb_files) metadata.kb_files = []
      
      project.value = {
        ...data,
        metadata: metadata, // 显式设置，确保响应式
        startDate: data.start_date ? data.start_date.split('T')[0] : '',
        endDate: data.end_date ? data.end_date.split('T')[0] : '',
        team: metadata.team || [],
        files: metadata.files || [],
        originalMetadata: metadata
      }
      editedProject.value = { ...project.value }
      
      // Fetch knowledge base files
      await fetchKBFiles()
      // Fetch deliverables
      await fetchDeliverables()
    }
  } catch (err) {
    console.error('获取项目详情失败:', err)
    message.error('获取项目详情失败')
  } finally {
    isLoading.value = false
  }
}

const isRefreshingKB = ref(false)
const uploadedFiles = ref([])
const pendingUploads = ref([]) // 存储等待关联的文件路径: { path, name }
const fileInput = ref(null)
const isUploading = ref(false)

// 交付物相关状态
const deliverableList = ref([])
const deliverableTotal = ref(0)
const deliverableSearch = ref('')
const currentPage = ref(1)
const pageSize = ref(10)
const isExtracting = ref(false)
const extractionStatus = ref('')
const isFirstLoad = ref(true) // 用于标记是否是页面初次加载
const processedFiles = ref(new Set()) // 记录已触发过提取的文件ID，避免重复触发
const newDeliverableIds = ref(new Set()) // 存储新添加的交付物ID
const savingDeliverableId = ref(null) // 正在保存的交付物ID

const handleWordCountChange = async (item) => {
  const value = item.word_count
  
  // 输入验证：仅允许正整数
  if (value === null || value === undefined || value === '' || isNaN(value) || value <= 0) {
    message.warning('请输入有效的正整数')
    fetchDeliverables() // 恢复原始值
    return
  }

  const intValue = parseInt(value, 10)
  if (intValue.toString() !== value.toString()) {
    item.word_count = intValue
  }

  try {
    savingDeliverableId.value = item.id
    await projectApi.updateDeliverable(projectId, item.id, {
      word_count: intValue
    })
    message.success({ content: '保存成功', key: 'save-word-count', duration: 2 })
  } catch (err) {
    console.error('更新预估字数失败:', err)
    message.error('保存失败')
    fetchDeliverables() // 恢复原始值
  } finally {
    savingDeliverableId.value = null
  }
}

const fetchDeliverables = async () => {
  if (isDemoMode.value) return
  console.log('[DEBUG] 正在获取交付物列表，项目ID:', projectId)
  try {
    const res = await projectApi.getDeliverables(projectId, {
      name: deliverableSearch.value,
      page: currentPage.value,
      page_size: pageSize.value
    })
    
    // 兼容处理：如果 data 是键值对数组格式，转换为对象
    let data = res.data
    if (Array.isArray(data) && data.length > 0 && Array.isArray(data[0])) {
      console.log('[DEBUG] 检测到数据格式为键值对数组，进行转换')
      data = Object.fromEntries(data)
    }
    
    console.log('[DEBUG] 获取交付物列表成功:', data?.items?.length || 0, '个交付物')
    if (data) {
      deliverableList.value = data.items || []
      deliverableTotal.value = data.total || 0
    }
  } catch (err) {
    console.error('获取交付物列表失败:', err)
  }
}

const fetchKBFiles = async (showLoading = false) => {
  if (isDemoMode.value) return
  if (showLoading) isRefreshingKB.value = true
  try {
    const res = await databaseApi.getDatabaseInfo(KB_ID)
    const filesData = res?.files || {}
    const allKBFiles = Object.values(filesData)
    
    // 检查并处理等待关联的文件
    if (pendingUploads.value.length > 0) {
      console.log('正在检查待关联文件:', pendingUploads.value.length)
      for (let i = pendingUploads.value.length - 1; i >= 0; i--) {
        const pending = pendingUploads.value[i]
        const foundFile = allKBFiles.find(f => f.path === pending.path || f.filename === pending.name)
        if (foundFile) {
          try {
            console.log('文件已自动关联项目:', foundFile.file_id)
            // 发现文件已入库，建立项目关联
            await projectApi.linkFile(projectId, foundFile.file_id)
            console.log('[DEBUG] 项目关联成功，准备检查是否触发提取:', foundFile.file_id)
            
            // 更新本地项目元数据
            if (!project.value.metadata.kb_files.includes(foundFile.file_id)) {
              project.value.metadata.kb_files.push(foundFile.file_id)
            }
            // 从等待队列移除
            pendingUploads.value.splice(i, 1)
            
            // 如果该文件刚刚上传成功且已解析完成，且未处理过
            if (foundFile.status === 'done' && !processedFiles.value.has(foundFile.file_id)) {
              console.log('[DEBUG] 文件状态已就绪，立即触发交付物提取:', foundFile.filename)
              processedFiles.value.add(foundFile.file_id)
              await processDeliverableExtraction(foundFile.file_id, foundFile.filename)
            } else {
              console.log('[DEBUG] 文件关联完成，当前状态:', foundFile.status, '是否已处理:', processedFiles.value.has(foundFile.file_id))
            }
          } catch (err) {
            console.error('[ERROR] 自动关联项目失败:', err)
          }
        }
      }
    }
    
    // 获取当前项目的关联文件ID列表
    const projectFiles = project.value?.metadata?.kb_files || []
    
    // 过滤本项目关联的文件
    const files = allKBFiles
      .filter(f => projectFiles.includes(f.file_id))
      .map(f => ({
        id: f.file_id,
        name: f.filename,
        status: f.status,
        size: '',
        created_at: f.created_at
      }))
    
    // 如果是第一次加载，记录已完成的文件，避免重复触发历史文件的提取
    if (isFirstLoad.value) {
      files.forEach(f => {
        if (f.status === 'done') {
          processedFiles.value.add(f.id)
        }
      })
      console.log('[DEBUG] 初次加载，已跳过历史文件提取，已完成文件数:', processedFiles.value.size)
    }
    
    // 检查是否有新完成解析的文件需要提取交付物
    console.log('[DEBUG] 正在检查文件解析状态，当前已完成提取列表:', Array.from(processedFiles.value))
    for (const file of files) {
      // 匹配旧文件状态，考虑 ID 变更情况 (pending-path -> file_id)
      const oldFile = uploadedFiles.value.find(f => 
        f.id === file.id || 
        (f.id.startsWith('pending-') && f.name === file.name)
      )
      
      if (file.status === 'done' && !processedFiles.value.has(file.id)) {
        const wasNotDone = !oldFile || oldFile.status !== 'done'
        
        // 触发条件：
        // 1. 之前不是 done 状态 (wasNotDone 为 true)
        // 2. 或者是新发现的文件 (oldFile 为 undefined) 且不是页面初次加载
        if (wasNotDone && !isFirstLoad.value) {
          console.log(`检测到文件状态从 ${oldFile?.status || 'unknown'} 变为 done, 触发提取:`, file.name)
          processedFiles.value.add(file.id)
          await processDeliverableExtraction(file.id, file.name)
        }
      }
    }

    // 页面已完成初次加载的数据处理
    isFirstLoad.value = false

    // 将还在等待关联的文件也加入展示列表（状态设为 processing）
    const pendingDisplayFiles = pendingUploads.value.map(p => ({
      id: 'pending-' + p.path,
      name: p.name,
      status: 'processing',
      size: '',
      created_at: new Date().toISOString()
    }))
    
    uploadedFiles.value = [...pendingDisplayFiles, ...files].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    
    // 如果有正在处理的文件或有等待关联的文件，启动轮询
    const hasUnfinished = uploadedFiles.value.some(f => f.status !== 'done' && f.status !== 'error')
    if (hasUnfinished || pendingUploads.value.length > 0) {
      startPolling()
    } else {
      stopPolling()
    }
  } catch (err) {
    console.error('获取知识库文件失败:', err)
  } finally {
    if (showLoading) isRefreshingKB.value = false
  }
}

const processDeliverableExtraction = async (fileId, fileName, retryCount = 0) => {
  isExtracting.value = true
  extractionStatus.value = `正在解析 ${fileName} 中的交付物...`
  
  try {
    const res = await projectApi.extractDeliverables(projectId, { file_id: fileId, db_id: KB_ID })
    if (res.code === 200) {
      const { items, existing } = res.data
      
      // 无论是否有新提取，都尝试刷新列表以确保同步，并切换到清单标签页
      activeTab.value = 'list'
      await fetchDeliverables()
      
      if (items && items.length > 0) {
        message.success(`从 ${fileName} 中提取了 ${items.length} 个新交付物`)
        // 标记为新添加
        items.forEach(item => {
          newDeliverableIds.value.add(item.name)
        })
        
        // 5秒后移除 New 标记
        setTimeout(() => {
          items.forEach(item => newDeliverableIds.value.delete(item.name))
        }, 5000)
      }
      
      if (existing && existing.length > 0) {
        message.info(`以下交付物已存在：${existing.join(', ')}`)
      }
      
      if (!items?.length && !existing?.length) {
        message.info(`${fileName} 中未发现交付物信息`)
      }
    }
  } catch (err) {
    console.error('提取交付物失败:', err)
    if (retryCount < 3) {
      extractionStatus.value = `解析失败，正在进行第 ${retryCount + 1} 次重试...`
      setTimeout(() => processDeliverableExtraction(fileId, fileName, retryCount + 1), 2000)
    } else {
      message.error(`${fileName} 内容解析异常，已停止重试`)
    }
  } finally {
    isExtracting.value = false
    extractionStatus.value = ''
  }
}

const startPolling = () => {
  if (pollingTimer) return
  pollingTimer = setInterval(async () => {
    await fetchKBFiles()
    await fetchDeliverables()
  }, 3000)
}

const stopPolling = () => {
  if (pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}

onBeforeUnmount(() => {
  stopPolling()
})

const isEditingInfo = ref(false)
const editedProject = ref({ ...project.value })

const saveProjectInfo = async () => {
  if (isDemoMode.value) {
    project.value = { ...editedProject.value }
    isEditingInfo.value = false
    return
  }

  try {
    const res = await projectApi.updateProject(projectId, {
      name: editedProject.value.name,
      description: editedProject.value.description,
      metadata: {
        ...project.value.originalMetadata
      }
    })
    if (res.data) {
      await fetchProjectDetail()
      isEditingInfo.value = false
      message.success('更新成功')
    }
  } catch (err) {
    console.error('更新项目失败:', err)
    message.error('更新失败')
  }
}

const triggerFileUpload = () => {
  if (isDemoMode.value) {
    message.info('演示模式不支持上传')
    return
  }
  fileInput.value.click()
}

const deleteFile = async (fileId) => {
  if (typeof fileId === 'string' && fileId.startsWith('pending-')) {
    const path = fileId.replace('pending-', '')
    pendingUploads.value = pendingUploads.value.filter(p => p.path !== path)
    return
  }

  Modal.confirm({
    title: '确认删除',
    content: '确定要从本项目中移除该文件吗？知识库中的原始文件不会被删除。',
    okText: '确定',
    cancelText: '取消',
    onOk: async () => {
      try {
        await projectApi.unlinkFile(projectId, fileId)
        message.success('已移除')
        // 更新本地状态
        project.value.metadata.kb_files = project.value.metadata.kb_files.filter(id => id !== fileId)
        await fetchKBFiles()
      } catch (err) {
        console.error('移除文件失败:', err)
        message.error('移除失败')
      }
    }
  })
}

const handleFileUpload = async (event) => {
  if (isUploading.value || isExtracting.value) return
  
  const files = event.target.files
  if (!files || files.length === 0) return
  
  // 限制单文件上传
  const file = files[0]
  isUploading.value = true
  
  try {
    // 1. 上传文件到 MinIO
    const uploadRes = await fileApi.uploadFile(file, KB_ID)
    if (!uploadRes || !uploadRes.file_path) {
      throw new Error('上传文件失败')
    }
    
    // 2. 将文件添加到知识库文档列表
    await documentApi.addDocuments(KB_ID, [uploadRes.file_path], {
      chunk_size: 1000,
      chunk_overlap: 200,
      enable_ocr: 'disable',
      use_qa_split: false,
      qa_separator: '\n\n\n',
      content_type: 'file'
    })
    
    // 3. 将文件加入等待关联队列
    pendingUploads.value.push({
      path: uploadRes.file_path,
      name: file.name
    })
    
    message.success('文件已提交，正在同步到项目...')
    
    // 立即触发一次轮询，以启动定时器并展示正在处理的文件
    await fetchKBFiles()
  } catch (err) {
    console.error('上传失败:', err)
    message.error('上传失败: ' + (err.message || '未知错误'))
  } finally {
    isUploading.value = false
    if (event.target) event.target.value = ''
  }
}

const downloadDeliverable = (deliverable) => {
  Modal.confirm({
    title: '确认下载',
    content: `确定要下载交付物 "${deliverable.name}" 吗？`,
    okText: '确定',
    cancelText: '取消',
    onOk: async () => {
      try {
        message.loading({ content: '正在准备下载...', key: 'downloading' })
        
        const response = await projectApi.exportDeliverable(projectId, deliverable.id)
        
        const blob = await response.blob()
        const url = window.URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        
        // 尝试从 Content-Disposition 获取文件名
        const disposition = response.headers.get('Content-Disposition')
        let filename = `${deliverable.name}.docx`
        if (disposition && disposition.indexOf('filename*=UTF-8\'\'') !== -1) {
          filename = decodeURIComponent(disposition.split('filename*=UTF-8\'\'')[1])
        } else if (disposition && disposition.indexOf('filename=') !== -1) {
          filename = disposition.split('filename=')[1].replace(/"/g, '')
        }
        
        a.download = filename
        document.body.appendChild(a)
        a.click()
        window.URL.revokeObjectURL(url)
        document.body.removeChild(a)
        
        message.success({ content: `交付物 ${deliverable.name} 下载成功`, key: 'downloading' })
      } catch (err) {
        console.error('下载失败:', err)
        message.error({ content: '下载失败: ' + (err.message || '未知错误'), key: 'downloading' })
      }
    }
  })
}

const deleteDeliverable = async (id) => {
  Modal.confirm({
    title: '确认删除',
    content: '确定要删除该交付物吗？删除后不可恢复。',
    okText: '确定',
    cancelText: '取消',
    onOk: async () => {
      try {
        await projectApi.deleteDeliverable(projectId, id)
        message.success('已删除')
        fetchDeliverables()
      } catch (err) {
        console.error('删除交付物失败:', err)
        message.error('删除失败')
      }
    }
  })
}

const generateDeliverable = (id) => {
  const pathPrefix = isDemoMode.value ? `/project/demo/${projectId}` : `/project/${projectId}`
  router.push(`${pathPrefix}/generate?deliverable=${id}`)
}


onMounted(() => {
  console.log('Fetching project details for:', projectId)
  fetchProjectDetail()
})
</script>

<template>
  <div class="project-detail-page">
    <!-- 头部导航区 -->
    <header class="page-header">
      <div class="header-left">
        <button class="back-btn" @click="goBack">
          <ChevronLeft :size="20" />
          <span>返回</span>
        </button>
        <div class="divider"></div>
        <h1 class="page-title">{{ isLoading ? '加载中...' : project.name }}</h1>
      </div>
      <div class="header-right">
      </div>
    </header>

    <div class="main-content">
      <!-- 上半部分：基本信息 + 知识库 -->
      <div class="top-section">
        <!-- 项目基本信息 -->
        <section class="info-card card">
          <div class="card-header">
            <div class="header-title">
              <Briefcase :size="18" class="icon-green" />
              <h2>项目基本信息</h2>
            </div>
            <button v-if="!isEditingInfo" class="icon-btn" @click="isEditingInfo = true">
              <Edit3 :size="18" />
            </button>
            <div v-else class="edit-actions">
              <button class="text-btn cancel" @click="isEditingInfo = false">取消</button>
              <button class="text-btn save" @click="saveProjectInfo">保存</button>
            </div>
          </div>
          
          <div v-if="!isEditingInfo" class="info-body scrollable-content">
            <div class="info-item">
              <label>项目描述</label>
              <p class="description truncate-desc" :class="{ 'empty-text': !project.description }">
                {{ project.description || '暂无项目描述，点击右上角编辑按钮添加' }}
              </p>
            </div>
            <div class="info-grid">
              <div class="info-item">
                <label>开始日期</label>
                <p :class="{ 'empty-text': !project.startDate }">{{ project.startDate || '未设置' }}</p>
              </div>
              <div class="info-item">
                <label>预计结束</label>
                <p :class="{ 'empty-text': !project.endDate }">{{ project.endDate || '未设置' }}</p>
              </div>
            </div>
            
            <!-- 新增：项目团队 -->
            <div v-if="project.team && project.team.length > 0" class="team-section">
              <label>项目团队</label>
              <div class="team-list">
                <div v-for="member in project.team" :key="member.name" class="team-member">
                  <div class="member-avatar">{{ member.name.charAt(0) }}</div>
                  <div class="member-info">
                    <span class="member-name">{{ member.name }}</span>
                    <span class="member-role">{{ member.role }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <div v-else class="info-body edit-mode scrollable-content">
            <div class="input-group">
              <label>项目名称</label>
              <input v-model="editedProject.name" type="text" placeholder="输入项目名称" />
            </div>
            <div class="input-group">
              <label>项目描述</label>
              <textarea v-model="editedProject.description" placeholder="输入项目描述，帮助AI理解背景" rows="4"></textarea>
            </div>
          </div>
        </section>

        <!-- 项目知识库 (原资源上传区) -->
        <section class="resource-card card">
          <div class="card-header">
            <div class="header-title">
              <FileSearch :size="18" class="icon-green" />
              <h2>项目知识库</h2>
            </div>
            <div class="header-actions">
              <span class="header-tip">支持 PDF, Word, TXT</span>
              <button 
                class="icon-btn upload-trigger-btn" 
                :class="{ disabled: isUploading || isExtracting }"
                @click="!isUploading && !isExtracting && triggerFileUpload()"
                title="上传资源文件"
              >
                <Upload v-if="!isUploading" :size="16" />
                <Loader2 v-else :size="16" class="animate-spin" />
              </button>
            </div>
          </div>
          
          <div class="knowledge-base-content">
            <input
              ref="fileInput"
              type="file"
              style="display: none"
              accept=".pdf,.doc,.docx,.ppt,.pptx,.txt"
              @change="handleFileUpload"
            />
            
            <!-- 提取状态提示 -->
            <div v-if="isExtracting" class="extraction-banner">
              <Sparkles :size="14" class="icon-sparkle" />
              <span>{{ extractionStatus || '正在分析交付物清单...' }}</span>
            </div>

            <div class="file-list-mini scrollable-content">
              <div v-for="file in uploadedFiles" :key="file.id" class="file-item-mini">
                <div class="file-icon-wrapper">
                  <FileText :size="14" />
                </div>
                <div class="file-info-main">
                  <div class="file-name-row">
                    <span class="file-name" :title="file.name">{{ file.name }}</span>
                  </div>
                </div>
                <div class="file-actions-mini">
                  <div v-if="file.status === 'done'" class="status-icon-mini parsed" title="已解析">
                    <CheckCircle2 :size="14" />
                  </div>
                  <div v-else-if="file.status === 'error'" class="status-icon-mini error" title="解析失败">
                    <AlertCircle :size="14" />
                  </div>
                  <div v-else class="status-icon-mini processing" title="处理中...">
                    <Loader2 :size="14" class="animate-spin" />
                  </div>
                  <button class="delete-file-btn" @click.stop="deleteFile(file.id)" title="删除文件">
                    <Trash2 :size="14" />
                  </button>
                </div>
              </div>
              
              <div v-if="uploadedFiles.length === 0" class="empty-files-placeholder">
                <p>暂无相关资源文件</p>
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- 下半部分：交付物管理 -->
      <div class="bottom-section">
        <section class="management-card card">
          <div class="management-header">
            <div class="tabs">
              <button :class="{ active: activeTab === 'list' }" @click="activeTab = 'list'">交付物清单</button>
              <button :class="{ active: activeTab === 'other' }" @click="activeTab = 'other'">其他(待迭代)</button>
            </div>
            <div v-if="activeTab === 'list'" class="search-bar-mini">
              <Search :size="16" class="search-icon" />
              <input 
                v-model="deliverableSearch" 
                type="text" 
                placeholder="搜索交付物名称..." 
                @input="() => { currentPage = 1; fetchDeliverables(); }"
              />
            </div>
          </div>
          
          <div v-if="activeTab === 'list'" class="tab-content">
            <div class="deliverable-table-container">
              <table class="deliverable-table">
                <thead>
                  <tr>
                    <th class="col-name">交付物名称</th>
                    <th class="col-qty">数量</th>
                    <th class="col-words">预估字数</th>
                    <th class="col-status">状态</th>
                    <th class="col-ops">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in deliverableList" :key="item.id" class="deliverable-row">
                    <td class="col-name">
                      <div class="name-with-tag">
                        <span class="name-text">{{ item.name }}</span>
                        <span v-if="newDeliverableIds.has(item.name)" class="new-tag">New</span>
                      </div>
                    </td>
                    <td class="col-qty">{{ item.quantity || 0 }}</td>
                    <td class="col-words">
                      <div class="editable-words-wrapper">
                        <input 
                          v-model.number="item.word_count" 
                          type="number" 
                          class="word-count-input"
                          :disabled="savingDeliverableId === item.id"
                          @change="handleWordCountChange(item)"
                        />
                        <div v-if="savingDeliverableId === item.id" class="save-spinner">
                          <Loader2 :size="12" class="animate-spin" />
                        </div>
                      </div>
                    </td>
                    <td class="col-status">
                      <span 
                        class="status-pill readonly" 
                        :class="item.status === '已撰写' ? 'done' : 'pending'"
                      >
                        {{ item.status }}
                      </span>
                    </td>
                    <td class="col-ops">
                      <div class="op-buttons">
                        <button 
                          class="op-btn write" 
                          title="撰写"
                          @click="generateDeliverable(item.id)"
                        >
                          <Wand2 :size="16" />
                        </button>
                        <button 
                          class="op-btn download" 
                          :title="item.can_download ? '下载' : '未撰写，无法下载'"
                          :disabled="!item.can_download"
                          @click="downloadDeliverable(item)"
                        >
                          <Download :size="16" />
                        </button>
                        <button 
                          class="op-btn delete" 
                          title="删除"
                          @click="deleteDeliverable(item.id)"
                        >
                          <Trash2 :size="16" />
                        </button>
                      </div>
                    </td>
                  </tr>
                  <tr v-if="!deliverableList || deliverableList.length === 0">
                    <td colspan="5" class="empty-table">
                      {{ deliverableSearch ? '未找到匹配的交付物' : '暂无交付物清单，请通过项目知识库上传文件自动提取' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 分页组件 -->
            <div v-if="deliverableTotal > pageSize" class="pagination-container">
              <div class="pagination-info">
                共 {{ deliverableTotal }} 条
              </div>
              <div class="pagination-actions">
                <button 
                  class="page-btn" 
                  :disabled="currentPage === 1" 
                  @click="currentPage--; fetchDeliverables()"
                >
                  <ChevronLeft :size="16" />
                </button>
                <span class="page-num">{{ currentPage }} / {{ Math.ceil(deliverableTotal / pageSize) }}</span>
                <button 
                  class="page-btn" 
                  :disabled="currentPage >= Math.ceil(deliverableTotal / pageSize)" 
                  @click="currentPage++; fetchDeliverables()"
                >
                  <ChevronRight :size="16" />
                </button>
              </div>
            </div>
          </div>
          
          <div v-else class="tab-content other-resources">
            <div class="empty-state">
              <ExternalLink :size="40" />
              <p>暂无关联的其他资源</p>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.project-detail-page {
  height: 100vh;
  width: 100%;
  background-color: #f8fafb;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Header */
.page-header {
  height: 64px;
  background: white;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.back-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: #595959;
  cursor: pointer;
  font-size: 14px;
  transition: color 0.3s;
}

.back-btn:hover {
  color: #86BC25;
}

.divider {
  width: 1px;
  height: 20px;
  background: #e8e8e8;
}

.page-title {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0;
}

.project-name-display {
  font-size: 14px;
  color: #8c8c8c;
  font-weight: 500;
}

.header-right {
  display: flex;
  align-items: center;
}

.status-badge {
  padding: 4px 12px;
  background: #f5f5f5;
  border: 1px solid #d9d9d9;
  color: #8c8c8c;
  border-radius: 4px;
  font-size: 12px;
}

.status-badge.待启动 {
  background: #f5f5f5;
  border-color: #d9d9d9;
  color: #8c8c8c;
}

.status-badge.进行中 {
  background: #e6f7ff;
  border-color: #91d5ff;
  color: #1890ff;
}

.status-badge.已完成 {
  background: #f6ffed;
  border-color: #b7eb8f;
  color: #52c41a;
}

.status-badge.已归档 {
  background: #fffbe6;
  border-color: #ffe58f;
  color: #faad14;
}

/* Main Content Layout */
.main-content {
  flex: 1;
  padding: 16px;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
  line-height: 1.4;
}

.top-section {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  flex-shrink: 0;
}

.bottom-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.bottom-section .card {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.icon-green {
  color: #86BC25;
}

.card {
  background: white;
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0; /* 使用 gap 代替 margin */
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 4px;
}

.header-title h2 {
  font-size: 15px;
  font-weight: 600;
  color: #262626;
  margin: 0;
}

.info-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.info-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  max-height: 400px;
}

.resource-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  max-height: 400px;
}

.scrollable-content {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}

.scrollable-content::-webkit-scrollbar {
  width: 4px;
}

.scrollable-content::-webkit-scrollbar-track {
  background: transparent;
}

.scrollable-content::-webkit-scrollbar-thumb {
  background: #e8e8e8;
  border-radius: 4px;
}

.scrollable-content::-webkit-scrollbar-thumb:hover {
  background: #d9d9d9;
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

/* Knowledge Base Styles */
.knowledge-base-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  overflow: hidden;
}

.extraction-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #f6ffed;
  border: 1px solid #b7eb8f;
  border-radius: 4px;
  color: #52c41a;
  font-size: 12px;
  margin-bottom: 4px;
}

.file-list-mini {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.file-item-mini {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: #f8fafb;
  border-radius: 4px;
  transition: all 0.2s;
}

.file-item-mini:hover {
  background: #f0f2f5;
}

.file-icon-wrapper {
  color: #8c8c8c;
  display: flex;
  align-items: center;
}

.file-info-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
}

.file-name-row {
  line-height: 1.4;
}

.file-name {
  font-size: 13px;
  color: #262626;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}

.file-actions-mini {
  display: flex;
  align-items: center;
  gap: 6px;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 4px;
  border: none;
  background: transparent;
  color: #8c8c8c;
  cursor: pointer;
  border-radius: 4px;
  transition: all 0.3s;
}

.icon-btn:hover {
  color: #86BC25;
  background: #f6ffed;
}

.icon-btn:active {
  transform: scale(0.92);
}

.upload-trigger-btn {
  transition: all 0.3s;
}

.upload-trigger-btn.disabled {
  color: #bfbfbf;
  cursor: not-allowed;
  background: transparent;
}

.status-icon-mini {
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-icon-mini.parsed {
  color: #52c41a;
}

.status-icon-mini.error {
  color: #ff4d4f;
}

.status-icon-mini.processing {
  color: #86BC25;
}

.animate-spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.delete-file-btn {
  padding: 4px;
  background: transparent;
  border: none;
  color: #bfbfbf;
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s;
}

.delete-file-btn:hover {
  color: #ff4d4f;
  background: #fff1f0;
}

.empty-files-placeholder {
  padding: 24px 0;
  text-align: center;
  color: #bfbfbf;
  font-size: 13px;
}

/* Pagination Styles */
.pagination-container {
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  border-top: 1px solid #f0f0f0;
  background: white;
}

.pagination-info {
  font-size: 13px;
  color: #8c8c8c;
}

.pagination-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.page-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid #d9d9d9;
  background: white;
  border-radius: 4px;
  color: #595959;
  cursor: pointer;
  transition: all 0.3s;
}

.page-btn:hover:not(:disabled) {
  border-color: #86BC25;
  color: #86BC25;
}

.page-btn:disabled {
  background: #f5f5f5;
  color: #bfbfbf;
  cursor: not-allowed;
  border-color: #d9d9d9;
}

.page-num {
  font-size: 14px;
  color: #262626;
  min-width: 40px;
  text-align: center;
}

.file-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.empty-text {
  color: #bfbfbf !important;
  font-style: italic;
}

.empty-files-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  color: #bfbfbf;
  font-size: 13px;
  border: 1px dashed #f0f0f0;
  border-radius: 8px;
}

.empty-deliverables {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 0;
  background: #fafafa;
  border-radius: 8px;
  border: 1px dashed #f0f0f0;
}

.empty-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: #bfbfbf;
}

.empty-content p {
  margin: 0;
  font-size: 14px;
}

.header-tip {
  font-size: 12px;
  color: #bfbfbf;
}

/* Info Card */
.info-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.info-item label {
  font-size: 12px;
  color: #8c8c8c;
  margin-bottom: 0;
}

.info-item p {
  font-size: 14px;
  color: #262626;
  margin: 0;
  line-height: 1.4;
}

.description {
  font-size: 14px;
  color: #595959;
  line-height: 1.6;
  margin: 0;
}

.truncate-desc {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3; /* 显示3行 */
  overflow: hidden;
  text-overflow: ellipsis;
}

.team-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed #e8e8e8;
}

.team-section label {
  display: block;
  font-size: 12px;
  color: #8c8c8c;
  margin-bottom: 8px;
}

.team-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.team-member {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f5f5f5;
  padding: 4px 10px;
  border-radius: 20px;
}

.member-avatar {
  width: 20px;
  height: 20px;
  background: #86BC25;
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
}

.member-info {
  display: flex;
  flex-direction: column;
}

.member-name {
  font-size: 12px;
  font-weight: 500;
  color: #262626;
  line-height: 1.2;
}

.member-role {
  font-size: 10px;
  color: #8c8c8c;
}

/* Edit Mode */
.input-group {
  margin-bottom: 20px;
}

.input-group label {
  font-size: 13px;
  font-weight: 500;
  color: #595959;
  display: block;
  margin-bottom: 8px;
}

.input-group input,
.input-group textarea,
.input-group select {
  width: 100%;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 14px;
  transition: all 0.3s;
  box-sizing: border-box;
}

.input-group input:focus,
.input-group textarea:focus,
.input-group select:focus {
  outline: none;
  border-color: #86BC25;
  box-shadow: 0 0 0 2px rgba(134, 188, 37, 0.1);
}

.number-input-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}

.number-input-wrapper input {
  padding-right: 40px;
}

.number-input-wrapper .unit {
  position: absolute;
  right: 12px;
  color: #8c8c8c;
  font-size: 14px;
}

.edit-actions {
  display: flex;
  gap: 12px;
}

.text-btn {
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.3s;
}

.text-btn.cancel {
  background: #f5f5f5;
  color: #595959;
}

.text-btn.save {
  background: #86BC25;
  color: white;
}

/* Resource Card */
.upload-area {
  border: 2px dashed #e8e8e8;
  border-radius: 12px;
  padding: 32px;
  text-align: center;
  background: #fafafa;
  cursor: pointer;
  transition: all 0.3s;
  margin-bottom: 24px;
}

.upload-area:hover {
  border-color: #86BC25;
  background: #f0f9eb;
}

.upload-hint {
  color: #8c8c8c;
}

.upload-hint p {
  margin: 12px 0 0;
  font-size: 14px;
}

.upload-hint span {
  color: #86BC25;
  font-weight: 500;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 24px;
}

.file-item {
  display: flex;
  align-items: center;
  padding: 12px;
  background: #f8fafb;
  border-radius: 10px;
  border: 1px solid #eee;
}

.file-icon {
  width: 40px;
  height: 40px;
  background: white;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #86BC25;
  margin-right: 12px;
}

.file-info {
  flex: 1;
}

.file-name {
  font-size: 14px;
  font-weight: 500;
  color: #1a1a1a;
}

.file-meta {
  font-size: 12px;
  color: #8c8c8c;
}

.parse-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: white;
  border: 1px solid #86BC25;
  color: #86BC25;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.3s;
}

.parse-btn:hover {
  background: #86BC25;
  color: white;
}

.parsed-status {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #52c41a;
  font-size: 12px;
  font-weight: 500;
}

.requirements-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.range-labels {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #bfbfbf;
  margin-top: 4px;
}

/* Management Card */
.management-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.management-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.tabs {
  display: flex;
  gap: 16px;
}

.tabs button {
  padding: 8px 0;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  font-size: 15px;
  font-weight: 500;
  color: #8c8c8c;
  cursor: pointer;
  transition: all 0.3s;
}

.tabs button.active {
  color: #86BC25;
  border-bottom-color: #86BC25;
}

.search-bar-mini {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f5f5f5;
  padding: 6px 12px;
  border-radius: 6px;
  width: 240px;
}

.search-icon {
  color: #bfbfbf;
}

.search-bar-mini input {
  border: none;
  background: transparent;
  font-size: 13px;
  outline: none;
  width: 100%;
}

.tab-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.deliverable-table-container {
  flex: 1;
  overflow-y: auto;
  position: relative;
  scroll-behavior: smooth;
}

.deliverable-table {
  width: 100%;
  border-collapse: collapse;
}

.deliverable-table th {
  text-align: left;
  padding: 10px 16px;
  background-color: #ffffff;
  color: #595959;
  font-size: 13px;
  font-weight: 500;
  position: sticky;
  top: 0;
  z-index: 10;
  border-bottom: 2px solid #f0f0f0;
}

.deliverable-table td {
  padding: 8px 16px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 13px;
  color: #262626;
  vertical-align: middle;
  line-height: 1.4;
}

.col-name { width: 35%; }
.col-qty { width: 10%; }
.col-words { width: 20%; }
.col-status { width: 15%; }
.col-ops { width: 20%; text-align: left; }

.editable-words-wrapper {
  display: flex;
  align-items: center;
  gap: 8px;
}

.word-count-input {
  width: 100px;
  border: 1px solid transparent;
  background: transparent;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 13px;
  transition: all 0.3s;
}

.word-count-input:hover {
  background: #f5f5f5;
  border-color: #d9d9d9;
}

.word-count-input:focus {
  background: white;
  border-color: #86BC25;
  outline: none;
  box-shadow: 0 0 0 2px rgba(134, 188, 37, 0.1);
}

.save-spinner {
  color: #86BC25;
  display: flex;
  align-items: center;
}

.name-with-tag {
  display: flex;
  align-items: center;
  gap: 8px;
}

.new-tag {
  background: #ff4d4f;
  color: white;
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 4px;
  font-weight: 600;
  line-height: 1;
}

.status-pill {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 12px;
  transition: all 0.3s;
}

.status-pill.readonly {
  cursor: default;
  user-select: none;
}

.status-pill.pending {
  background: #fff7e6;
  color: #faad14;
}

.status-pill.done {
  background: #f6ffed;
  color: #52c41a;
}

.op-buttons {
  display: flex;
  justify-content: flex-start;
  gap: 8px;
}

.op-btn {
  width: 28px;
  height: 28px;
  border-radius: 4px;
  border: 1px solid #d9d9d9;
  background: white;
  color: #595959;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
}

.op-btn:hover {
  border-color: #86BC25;
  color: #86BC25;
  background: #f6ffed;
}

.op-btn.delete:hover {
  border-color: #ff4d4f;
  color: #ff4d4f;
  background: #fff1f0;
}

.op-btn.download:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: #f5f5f5;
  border-color: #d9d9d9;
  color: #bfbfbf;
}

.empty-table {
  text-align: center;
  padding: 40px;
  color: #bfbfbf;
  font-style: italic;
}

.pagination-mini {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 16px 0;
  border-top: 1px solid #f0f0f0;
}

.page-btn {
  padding: 4px 12px;
  border: 1px solid #d9d9d9;
  background: white;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
}

.page-btn:disabled {
  color: #bfbfbf;
  cursor: not-allowed;
  background: #f5f5f5;
}

.page-info {
  font-size: 12px;
  color: #8c8c8c;
}

.other-resources {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  color: #bfbfbf;
}

.empty-state p {
  margin: 0;
  font-size: 14px;
}

.icon-sparkle {
  color: #86BC25;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.1); opacity: 0.8; }
  100% { transform: scale(1); opacity: 1; }
}

/* 响应式调整 */
@media (max-width: 1200px) {
  .top-section {
    grid-template-columns: 1fr;
    max-height: none;
    flex-shrink: 1;
  }
  
  .info-card, .resource-card {
    max-height: none;
  }
}

@media (max-width: 768px) {
  .main-content {
    padding: 12px;
    gap: 12px;
  }
}
</style>