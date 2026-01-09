import { apiAdminGet, apiAdminPost, apiAdminPut, apiAdminDelete, apiRequest } from './base'

/**
 * 项目管理API模块
 * 包含项目管理和交付物管理功能
 */

export const projectApi = {
  // =============================================================================
  // === 项目管理 ===
  // =============================================================================

  /**
   * 创建项目
   * @param {Object} projectData - 项目数据
   * @returns {Promise} - 创建结果
   */
  createProject: async (projectData) => {
    return apiAdminPost('/api/projects', projectData)
  },

  /**
   * 获取项目列表
   * @param {Object} params - 查询参数
   * @param {number} params.page - 页码
   * @param {number} params.page_size - 每页数量
   * @returns {Promise} - 项目列表
   */
  getProjects: async (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.page) queryParams.append('page', params.page)
    if (params.page_size) queryParams.append('page_size', params.page_size)

    const url = `/api/projects${queryParams.toString() ? '?' + queryParams.toString() : ''}`
    return apiAdminGet(url)
  },

  /**
   * 搜索项目
   * @param {Object} params - 查询参数
   * @param {string} params.name - 项目名称（模糊搜索）
   * @param {string} params.status - 项目状态
   * @param {number} params.page - 页码
   * @param {number} params.page_size - 每页数量
   * @returns {Promise} - 搜索结果
   */
  searchProjects: async (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.name) queryParams.append('name', params.name)
    if (params.status) queryParams.append('status', params.status)
    if (params.page) queryParams.append('page', params.page)
    if (params.page_size) queryParams.append('page_size', params.page_size)

    const url = `/api/projects/search${queryParams.toString() ? '?' + queryParams.toString() : ''}`
    return apiAdminGet(url)
  },

  /**
   * 获取项目详情
   * @param {number} projectId - 项目ID
   * @returns {Promise} - 项目详情
   */
  getProject: async (projectId) => {
    return apiAdminGet(`/api/projects/${projectId}`)
  },

  /**
   * 更新项目
   * @param {number} projectId - 项目ID
   * @param {Object} updateData - 更新数据
   * @returns {Promise} - 更新结果
   */
  updateProject: async (projectId, updateData) => {
    return apiAdminPut(`/api/projects/${projectId}`, updateData)
  },

  /**
   * 删除项目（软删除）
   * @param {number} projectId - 项目ID
   * @returns {Promise} - 删除结果
   */
  deleteProject: async (projectId) => {
    return apiAdminDelete(`/api/projects/${projectId}`)
  },

  /**
   * 关联项目与知识库文件
   * @param {number} projectId - 项目ID
   * @param {string} fileId - 文件ID
   * @returns {Promise} - 关联结果
   */
  linkFile: async (projectId, fileId) => {
    return apiAdminPost(`/api/projects/${projectId}/files/${fileId}`)
  },

  /**
   * 取消项目与知识库文件的关联
   * @param {number} projectId - 项目ID
   * @param {string} fileId - 文件ID
   * @returns {Promise} - 取消关联结果
   */
  unlinkFile: async (projectId, fileId) => {
    return apiAdminDelete(`/api/projects/${projectId}/files/${fileId}`)
  },

  // =============================================================================
  // === 交付物管理 ===
  // =============================================================================

  /**
   * 获取项目交付物列表
   * @param {number} projectId - 项目ID
   * @param {Object} params - 查询参数
   * @param {number} params.page - 页码（可选）
   * @param {number} params.page_size - 每页数量（可选）
   * @param {number} params.id - 交付物ID（可选）
   * @param {string} params.name - 交付物名称（模糊搜索，可选）
   * @param {string} params.status - 交付物状态（可选）
   * @returns {Promise} - 交付物列表
   */
  getDeliverables: async (projectId, params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.page !== undefined) queryParams.append('page', params.page)
    if (params.page_size !== undefined) queryParams.append('page_size', params.page_size)
    if (params.id !== undefined) queryParams.append('id', params.id)
    if (params.name) queryParams.append('name', params.name)
    if (params.status) queryParams.append('status', params.status)

    const url = `/api/projects/${projectId}/deliverables${queryParams.toString() ? '?' + queryParams.toString() : ''}`
    return apiAdminGet(url)
  },

  /**
   * 创建项目交付物
   * @param {number} projectId - 项目ID
   * @param {Object} deliverableData - 交付物数据
   * @returns {Promise} - 创建结果
   */
  createDeliverable: async (projectId, deliverableData) => {
    return apiAdminPost(`/api/projects/${projectId}/deliverables`, deliverableData)
  },

  /**
   * 获取交付物内容
   * @param {number} projectId - 项目ID
   * @param {number} deliverableId - 交付物ID
   * @returns {Promise} - 交付物内容
   */
  getDeliverableContent: async (projectId, deliverableId) => {
    return apiAdminGet(`/api/projects/${projectId}/deliverables/${deliverableId}/content`)
  },

  /**
   * 更新项目交付物
   * @param {number} projectId - 项目ID
   * @param {number} deliverableId - 交付物ID
   * @param {Object} updateData - 更新数据
   * @returns {Promise} - 更新结果
   */
  updateDeliverable: async (projectId, deliverableId, updateData) => {
    return apiAdminPut(`/api/projects/${projectId}/deliverables/${deliverableId}`, updateData)
  },

  /**
   * 删除项目交付物（软删除）
   * @param {number} projectId - 项目ID
   * @param {number} deliverableId - 交付物ID
   * @returns {Promise} - 删除结果
   */
  deleteDeliverable: async (projectId, deliverableId) => {
    return apiAdminDelete(`/api/projects/${projectId}/deliverables/${deliverableId}`)
  },

  /**
   * 从文件中提取交付物清单
   * @param {number} projectId - 项目ID
   * @param {Object} data - 提取参数
   * @param {string} data.file_id - 文件ID
   * @param {string} data.db_id - 知识库ID
   * @returns {Promise} - 提取结果
   */
  extractDeliverables: async (projectId, data) => {
    return apiAdminPost(`/api/projects/${projectId}/deliverables/extract`, data)
  },

  /**
   * 导出交付物为 Word 文档
   * @param {number} projectId - 项目ID
   * @param {number} deliverableId - 交付物ID
   * @returns {Promise} - Response对象（blob）
   */
  exportDeliverable: async (projectId, deliverableId) => {
    return apiAdminGet(`/api/projects/${projectId}/deliverables/${deliverableId}/export`, {}, 'blob')
  },

  /**
   * 清理交付物元数据中的残留字段
   * @param {number} projectId - 项目ID
   * @returns {Promise} - 清理结果
   */
  cleanupDeliverablesMetadata: async (projectId) => {
    return apiAdminPost(`/api/projects/${projectId}/deliverables/cleanup`)
  }
}
