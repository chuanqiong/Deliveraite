<script setup>
import { ref, onMounted, onUnmounted, nextTick, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import { message, Tree as ATree, Modal } from 'ant-design-vue'
import { useUserStore } from '@/stores/user'
import { demoProjects } from '@/constants/demoData'
import { projectApi } from '@/apis/project_api'
import { agentApi } from '@/apis/agent_api'
import { 
  smartParseJson, 
  validateAndFixTargetWords, 
  repairTruncatedJson, 
  parseNestedOutline 
} from '@/utils/jsonUtils'
import { logger, setTraceId } from '@/utils/logger'
import { 
  ChevronLeft, 
  Send, 
  Sparkles, 
  Download, 
  FileText,
  FileUp,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ListTree,
  Target,
  PlayCircle,
  ChevronLeftSquare,
  ChevronRightSquare
} from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const projectId = route.params.id
const deliverableId = route.query.deliverable
const isDemoMode = computed(() => route.meta.isDemo === true)

const deliverable = ref({
  id: deliverableId || 1,
  name: '正在加载...',
  type: 'report',
  status: '进行中',
  targetWords: 5000,
  word_count: 5000
})

// 交付物信息
const fetchDeliverableInfo = async () => {
  if (isDemoMode.value) {
    const project = demoProjects.find(p => p.id === Number(projectId)) || demoProjects[0]
    deliverable.value = project.deliverables.find(d => d.id === Number(deliverableId)) || project.deliverables[0]
    return
  }

  if (!deliverableId) return

  try {
    console.log('[Info] >>> 正在获取交付物大纲详情:', { projectId, deliverableId })
    const res = await projectApi.getDeliverables(projectId, { id: deliverableId })
    const items = res.data?.items || []
    const found = items.find(item => String(item.id) === String(deliverableId))
    
    if (found) {
      console.log('[Info] 成功获取到交付物数据:', { 
        name: found.name, 
        status: found.status, 
        hasOutline: !!found.metadata?.outline,
        outlineCount: found.metadata?.outline?.length || 0
      })
      deliverable.value = {
        ...deliverable.value,
        ...found,
        // 确保 targetWords 和 word_count 同步
        targetWords: found.word_count || found.targetWords || 10000,
        word_count: found.word_count || found.targetWords || 10000
      }
      
      // 1. 先加载大纲结构，并确保 ID 唯一且无重复标题
      if (found.metadata?.outline && found.metadata.outline.length > 0) {
        const seenTitles = new Set()
        const rawOutline = found.metadata.outline
          .filter(section => {
            const normalizedTitle = getNormalizedTitle(section.title)
            if (seenTitles.has(normalizedTitle)) return false
            seenTitles.add(normalizedTitle)
            return true
          })
          .map(section => {
            // 尝试保留现有内容，防止 fetchDeliverableInfo 重置导致页面闪烁或内容丢失
            const existingSection = documentData.value.find(s => s.id === section.id)
            return {
              ...section,
              content: section.content || (existingSection ? existingSection.content : ''),
              status: section.status || (section.content ? 'completed' : (existingSection && existingSection.content ? 'completed' : 'pending'))
            }
          })

        // 检查是否已经包含 parentId（已保存层级关系），如果是则直接使用
        const hasParentId = rawOutline.some(section => section.parentId !== undefined)
        if (hasParentId) {
          // 数据库已保存层级关系，直接使用
          documentData.value = rawOutline
        } else {
          // 数据库未保存层级关系，需要重新构建
          documentData.value = reconstructHierarchy(rawOutline)
        }

        // 默认展开所有章节（优化：仅展开第一级章节）
        expandedKeys.value = documentData.value
          .filter(s => !s.parentId && documentData.value.some(child => child.parentId === s.id))
          .map(s => s.id)
        
        console.log('[Info] 已加载大纲结构，当前章节数:', documentData.value.length)
      }
      
      // 2. 异步加载完整正文内容
      console.log('[Info] 开始异步加载完整正文...')
      await fetchFullContent()

      // 加载已保存的 thread_id
      if (found.metadata && found.metadata.thread_id) {
        currentThreadId.value = found.metadata.thread_id
      }
    }
  } catch (err) {
    console.error('获取交付物详情失败:', err)
    message.error('获取交付物信息失败')
  }
}

// 异步获取完整正文并解析
const fetchFullContent = async () => {
  try {
    console.log('[FullContent] >>> 正在获取完整正文内容...')
    const contentRes = await projectApi.getDeliverableContent(projectId, deliverableId)
    const savedContent = contentRes.data?.content || ''
    
    if (!savedContent) {
      console.log('[FullContent] 数据库中尚无正文内容')
      return
    }

    console.log('[FullContent] 成功获取正文，长度:', savedContent.length)

    // 如果大纲已经存在，尝试从正文中提取内容补全
    if (documentData.value.length > 0) {
      let updatedCount = 0
      
      // 预先计算所有章节在正文中的匹配位置
      const sectionMatches = documentData.value.map(section => {
        const escapedTitle = section.title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        // 优化正则表达式：使用非贪婪匹配前缀，防止前缀匹配了标题本身的内容
        const titlePattern = new RegExp(`(?:^|\\n)(#+|\\*\\*)\\s*(?:[\\d\\.\\s\\(\\)\\u4e00-\\u9fa5]+)??${escapedTitle}\\s*(?:\\*\\*)?\\n*`, 'i')
        const match = savedContent.match(titlePattern)
        return {
          id: section.id,
          title: section.title,
          match: match,
          startPos: match ? match.index : -1,
          contentStartPos: match ? match.index + match[0].length : -1
        }
      }).filter(m => m.startPos !== -1)
      
      // 按照在正文中出现的先后顺序排序
      sectionMatches.sort((a, b) => a.startPos - b.startPos)

      const newDocumentData = documentData.value.map(section => {
        const currentMatchInfo = sectionMatches.find(m => m.id === section.id)
        
        let extractedContent = ''
        if (currentMatchInfo) {
          const startIdx = currentMatchInfo.contentStartPos
          
          // 寻找下一个已知章节的起始位置作为结束位置
          const nextMatch = sectionMatches.find(m => m.startPos > currentMatchInfo.startPos)
          const endIdx = nextMatch ? nextMatch.startPos : savedContent.length
          
          let rawExtracted = savedContent.substring(startIdx, endIdx)
          
          // 剥离可能重复出现的标题文本（处理污染数据）
          const sectionTitle = section.title
          const escapedTitleForStrip = sectionTitle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
          const redundantPattern = new RegExp(`^\\s*(?:#+|\\*\\*|)\\s*(?:[\\d\\.\\s\\(\\)\\u4e00-\\u9fa5]+)?${escapedTitleForStrip}\\s*(?:\\*\\*)?\\n+`, 'i')
          extractedContent = rawExtracted.replace(redundantPattern, '').trim()
          
          if (extractedContent) {
            console.log(`[FullContent] 成功为章节 "${section.title}" 提取内容，长度: ${extractedContent.length}`)
            updatedCount++
          }
        }
        
        return {
          ...section,
          content: extractedContent.trim() || section.content || '',
          status: (extractedContent.trim() || section.content) ? 'completed' : section.status
        }
      })
      
      documentData.value = newDocumentData
      console.log(`[FullContent] 正文提取完成，更新了 ${updatedCount} 个章节的内容`)
    } else {
      // 如果没有大纲，尝试从正文解析大纲，并进行去重处理
      const titleMatches = [...savedContent.matchAll(/^(?:##+|\*\*)\s*(.+?)\s*(?:\*\*)?$/gm)]
      if (titleMatches.length > 0) {
        const seenTitles = new Set()
        const uniqueSections = []
        
        titleMatches.forEach((match, index) => {
          const title = match[1].trim()
          const normalizedTitle = title.toLowerCase()
          if (seenTitles.has(normalizedTitle)) return
          seenTitles.add(normalizedTitle)
          
          const startIdx = match.index + match[0].length
          const restContent = savedContent.substring(startIdx)
          const nextTitlePattern = /\n\n(?:##+|\*\*)\s+/
          const endMatch = restContent.match(nextTitlePattern)
          const content = endMatch ? restContent.substring(0, endMatch.index) : restContent
          
          uniqueSections.push({
            id: Date.now() + index,
            title: title,
            targetWords: 500,
            content: content.trim(),
            status: 'completed'
          })
        })
        
        documentData.value = reconstructHierarchy(uniqueSections)
        
        // 默认展开所有章节
        expandedKeys.value = documentData.value
          .filter(s => documentData.value.some(child => child.parentId === s.id))
          .map(s => s.id)
      } else {
        // 单个章节兜底
        documentData.value = [{
          id: Date.now(),
          title: '交付物正文',
          targetWords: deliverable.value.targetWords || 5000,
          content: savedContent.trim(),
          status: 'completed'
        }]
      }
    }
    
    // 自动选中
    if (documentData.value.length > 0 && !activeSectionId.value) {
      activeSectionId.value = documentData.value[0].id
    }
  } catch (err) {
    console.error('获取正文内容失败:', err)
  }
}

// 文档数据结构
const documentData = ref([])
const activeSectionId = ref(null)
const expandedKeys = ref([])
const isLocalMode = computed(() => !!activeSectionId.value)

// 转换 documentData 为 tree 格式
const treeData = computed(() => {
  // 确保 parentId 统一：undefined -> null
  const normalizedData = documentData.value.map(item => ({
    ...item,
    parentId: item.parentId ?? null
  }))

  const buildTree = (list, parentId = null, depth = 0) => {
    // 防止无限循环（最大深度保护）
    if (depth > 10) return []

    return list
      .filter(item => item.parentId === parentId)
      .map(item => {
        const children = buildTree(list, item.id, depth + 1)
        return {
          key: item.id,
          title: item.title || '未命名章节',
          targetWords: item.targetWords || 0,
          status: item.status || 'pending',
          isLeaf: children.length === 0,
          children: children.length > 0 ? children : undefined
        }
      })
  }

  try {
    return buildTree(normalizedData)
  } catch (error) {
    console.error('Error building tree data:', error)
    // 返回空树以防止崩溃
    return []
  }
})

// 重构章节层级关系
const reconstructHierarchy = (data) => {
  if (!data || data.length === 0) return []

  // 1. 建立编号到 ID 的映射
  const prefixMap = {}
  data.forEach(item => {
    const prefix = getSectionNumberPrefix(item.title)
    if (prefix) {
      prefixMap[prefix] = item.id
    }
  })

  // 2. 建立层级关系，确保 parentId 始终为 null 或有效值
  const listWithParents = data.map(item => {
    const prefix = getSectionNumberPrefix(item.title)
    if (!prefix) {
      // 没有编号的章节，确保 parentId 为 null
      return { ...item, parentId: item.parentId ?? null }
    }

    // 寻找父级编号：如 "1.1.1" -> "1.1", "1.1" -> "1"
    const parts = prefix.split('.')
    if (parts.length > 1) {
      const parentPrefix = parts.slice(0, -1).join('.')
      const parentId = prefixMap[parentPrefix]
      if (parentId) {
        return { ...item, parentId }
      }
    }

    // 根节点或未找到父节点，确保 parentId 为 null
    return { ...item, parentId: null }
  })

  // 3. 按照编号排序，确保顺序正确（Word 风格）
  return listWithParents.sort((a, b) => {
    const prefixA = getSectionNumberPrefix(a.title)
    const prefixB = getSectionNumberPrefix(b.title)

    if (!prefixA && !prefixB) return 0
    if (!prefixA) return 1
    if (!prefixB) return -1

    const partsA = prefixA.split('.').map(Number)
    const partsB = prefixB.split('.').map(Number)

    const maxLen = Math.max(partsA.length, partsB.length)
    for (let i = 0; i < maxLen; i++) {
      const vA = partsA[i] === undefined ? -1 : partsA[i]
      const vB = partsB[i] === undefined ? -1 : partsB[i]
      if (vA !== vB) return vA - vB
    }
    return 0
  })
}

// 监听目录点击
const onTreeSelect = (selectedKeys) => {
  if (selectedKeys.length > 0) {
    activeSectionId.value = selectedKeys[0]
    scrollToSection(selectedKeys[0])
  } else {
    activeSectionId.value = null
  }
}

const onTreeExpand = (keys, info) => {
  if (info && !info.expanded) {
    // 如果是收起操作，移除该节点及其所有子节点的展开状态
    const key = info.node.key
    const getAllChildIds = (parentId) => {
      const children = documentData.value.filter(s => s.parentId === parentId)
      let ids = children.map(s => s.id)
      children.forEach(c => {
        ids = [...ids, ...getAllChildIds(c.id)]
      })
      return ids
    }
    const idsToRemove = getAllChildIds(key)
    expandedKeys.value = keys.filter(k => !idsToRemove.includes(k))
  } else {
    expandedKeys.value = keys
  }
}

const handleNodeClick = (event, key, title) => {
  // 我们已经在 onTreeSelect 中处理了选中逻辑
  // 这里只处理展开/收起逻辑
  const isLeaf = !documentData.value.some(s => s.parentId === key)
  if (!isLeaf) {
    const index = expandedKeys.value.indexOf(key)
    if (index > -1) {
      // 收起：移除该节点及其所有子节点的展开状态
      const getAllChildIds = (parentId) => {
        const children = documentData.value.filter(s => s.parentId === parentId)
        let ids = children.map(s => s.id)
        children.forEach(c => {
          ids = [...ids, ...getAllChildIds(c.id)]
        })
        return ids
      }
      const idsToRemove = [key, ...getAllChildIds(key)]
      expandedKeys.value = expandedKeys.value.filter(k => !idsToRemove.includes(k))
    } else {
      // 展开：仅展开当前节点
      expandedKeys.value.push(key)
    }
  }
}

const scrollToSection = (id) => {
  nextTick(() => {
    const element = document.getElementById(`section-${id}`)
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  })
}

// 获取章节层级
const getSectionLevel = (section) => {
  if (!section.parentId) return 2 // 一级章节对应 h2
  
  // 递归计算层级
  let level = 2
  let current = section
  while (current.parentId) {
    const parent = documentData.value.find(s => s.id === current.parentId)
    if (!parent) break
    level++
    current = parent
  }
  return level
}

// 渲染 Markdown
const renderMarkdown = (content, sectionTitle = '') => {
  if (!content) return ''
  
  // 提取 content 标签内部内容，如果不存在则使用全文
  const contentMatch = content.match(/<content>([\s\S]*?)(?:<\/content>|$)/)
  let processedContent = contentMatch ? contentMatch[1] : content
  
  processedContent = processedContent
    .replace(/<think>[\s\S]*?(?:<\/think>|$)/g, '')
    .replace(/<summary>[\s\S]*?(?:<\/summary>|$)/g, '')
    .replace(/<check>[\s\S]*?(?:<\/check>|$)/g, '')
    // 移除 AI 生成的内容过滤标记 (兼容 Markdown 和 HTML 格式)
    .replace(/---\s*[\s\S]*?📊\s*字数统计：[\s\S]*$/g, '') // 移除末尾的分隔符和字数统计
    .replace(/[#\s-]*📊\s*字数统计：[\s\S]*?(?:目标字数|实际字数|误差率)[\s\S]*?(?:\n|$)/g, '') // 移除单行的统计
    // 处理通用 AI 生成特征标记 (移除包含特定关键字的行)
    .replace(/(?:<p>|###?\s+)[^<]*AI生成[^<]*(?:<\/p>)?/gi, '')
    .trim()

  // 防御性剥离：处理各种标题变体（包括无符号的纯文本标题）
  if (sectionTitle) {
    // 移除章节标题中的编号部分，用于更宽泛的匹配
    const cleanTitle = sectionTitle.replace(/^\d+\.?\s*/, '').trim()
    const escapedTitle = cleanTitle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    
    // 匹配开头可能的：1. 标题文本 2. # 标题 3. **标题** 4. ## 标题
    // 支持带编号或不带编号的匹配，且不区分大小写
    const titlePattern = new RegExp(`^\\s*(?:##+|#|\\*\\*|)\\s*(?:\\d+\\.?\\s*)?${escapedTitle}\\s*(?:\\*\\*)?(?:\\r?\\n)*`, 'i')
    processedContent = processedContent.replace(titlePattern, '').trim()
    
    // 额外修复：如果剥离后内容以“下一个”章节的标题开头，也应视为无内容（针对 AI 直接输出下一章节标题的情况）
    // 仅在内容非常短且匹配到标题时才进行剥离，防止误删正文中的子标题
    const nextTitlePattern = /^(?:##+|#|\*\*|)\s*(?:\d+\.\d*|[2-9]\.)\s+.*$/m
    if (processedContent.length < 200 && nextTitlePattern.test(processedContent)) {
      processedContent = processedContent.split(nextTitlePattern)[0].trim()
    }
  }
  
  if (!processedContent) return ''
  return marked(processedContent)
}

// 标准化标题：移除 Markdown 符号、编号和多余空格
 const getNormalizedTitle = (title) => {
    if (!title) return ''
    return title.trim()
      .toLowerCase()
      .replace(/^#+\s+/, '') // 移除 #
      .replace(/^[\d.]+\s*/, '') // 移除开头的数字和点，如 "1. " 或 "1.1 "
      .replace(/\*\*/g, '') // 移除 **
      .replace(/\s+/g, ' ') // 合并多个空格
  }

// 获取章节的编号前缀（支持中文数字和阿拉伯数字）
const getSectionNumberPrefix = (title) => {
  // 1. 匹配阿拉伯数字编号（如 "1.", "1.1", "1.1.1"）
  const arabicMatch = title.match(/^(\d+(\.\d+)*)\.?\s*/)
  if (arabicMatch) return arabicMatch[1]

  // 2. 匹配中文数字编号（如 "一、", "二、", "三、"）
  const chineseNums = {
    '一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
    '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
    '十一': '11', '十二': '12', '十三': '13', '十四': '14', '十五': '15',
    '十六': '16', '十七': '17', '十八': '18', '十九': '19', '二十': '20'
  }
  const chineseMainMatch = title.match(/^([一二三四五六七八九十]+)、/)
  if (chineseMainMatch) return chineseNums[chineseMainMatch[1]] || chineseMainMatch[1]

  // 3. 匹配括号中文数字编号（如 "（一）", "（二）"）
  const chineseParensMatch = title.match(/^（([一二三四五六七八九十]+）)/)
  if (chineseParensMatch) {
    const num = chineseParensMatch[1].replace('（', '').replace('）', '')
    return chineseNums[num] || num
  }

  return ''
}

// 强制修正子章节标题中的编号
const fixSectionTitleNumbering = (parentTitle, childTitle, index) => {
  const parentPrefix = getSectionNumberPrefix(parentTitle)
  const normalizedChildTitle = getNormalizedTitle(childTitle)
  
  if (parentPrefix) {
    // 强制构造正确编号，例如 "2." -> "2.1", "2.1" -> "2.1.1"
    return `${parentPrefix}.${index + 1} ${normalizedChildTitle}`
  }
  return childTitle
}

// 状态管理
const userInput = ref('')
const chatMessages = ref([
  { role: 'assistant', content: '您好！我是您的交付物生成助手。我已经准备好为您生成一份专业的文档。请输入您的需求，或者让我开始规划大纲。' }
])
const isGenerating = ref(false)
const writingSectionId = ref(null) // 当前正在撰写的章节 ID
const currentThreadId = ref(null) // 当前对话线程 ID

// 辅助函数：根据总字数确定文档规模
const getDocumentScale = (totalWords) => {
  if (totalWords >= 100000) return '超大型文档（≥10万字，最大4级）'
  if (totalWords >= 50000) return '大型文档（5-10万字，最大3-4级）'
  if (totalWords >= 10000) return '中型文档（1-5万字，最大2-3级）'
  return '小型文档（<1万字，最大2级）'
}

// 生成大纲
const generateOutline = async () => {
  if (isGenerating.value) return

  // 检查是否已存在大纲，如果存在则提示用户确认
  const hasExistingOutline = documentData.value.length > 0
  if (hasExistingOutline) {
    try {
      await Modal.confirm({
        title: '确认重新生成大纲？',
        content: '再次生成将会清空已生成的大纲、初稿和润色内容，此操作不可恢复。是否继续？',
        okText: '确认',
        okType: 'danger',
        cancelText: '取消'
      })
      // 用户确认后，不清空数据，等待生成完成后再替换
    } catch {
      // 用户取消，直接返回
      return
    }
  }

  await executeGenerateOutline()
}

// 包装函数：用于模板中的按钮点击事件
const handleGenerateOutline = async () => {
  await generateOutline()
}

// 执行生成大纲的实际逻辑
const executeGenerateOutline = async () => {
  isGenerating.value = true
  
  // 记录原始大纲，以便失败时恢复或判断是否更新
  const originalOutlineCount = documentData.value.length
  
  const planningMsg = { 
    role: 'assistant', 
    content: '正在基于知识库与目标字数规划大纲...',
    isPlanning: true 
  }
  chatMessages.value.push(planningMsg)

  try {
    const agentId = 'DeliverableAgent'
    console.log('[Outline] 开始调用 AI 生成大纲, agentId:', agentId)
    
    // 优先尝试调用 AI 生成大纲
    try {
      const res = await agentApi.sendAgentMessage(agentId, {
        query: `请为项目 ID ${projectId} 的交付物"${deliverable.value.name}"生成一份专业文档大纲。

### 基本信息
- 目标总字数：${deliverable.value.word_count || deliverable.value.targetWords || 5000} 字
- 文档规模：${getDocumentScale(deliverable.value.word_count || deliverable.value.targetWords || 5000)}

### 要求
1. **结构要求**：
   - 根据文档规模和字数预算，智能生成 2-4 级嵌套大纲结构
   - 对于字数预算 ≥ 总字数 8% 且绝对字数 ≥ 800 的章节，必须展开下一级
   - 一级章节：使用中文数字（一、二、三、...）
   - 二级章节：使用括号数字（（一）、（二）、...）
   - 三级章节：使用阿拉伯数字（1、2、3、...）
   - 四级章节：使用点分数字（1.1、1.2、...）

2. **内容要求**：每个章节必须包含至少200个汉字的初始内容
   - 初始内容应包含：本章节核心要点、与上下文的逻辑关系、需要展开的关键方向
   - 初始内容作为后续细化的基础，不是简单的摘要

3. **字数分配**：
   - 严格按照提示词中的"智能层级生成规则"分配字数
   - 确保所有章节的 targetWords 之和接近总目标字数
   - 避免出现 targetWords = 800000 这种异常值

**参考**：请严格按照提示词中的"智能层级生成规则"执行，包括：
- 根据总字数确定文档规模
- 逐层展开，每层检查展开条件（字数条件 + 内容复杂度条件）
- 使用相对比例（8%）而非绝对数字判断

### 输出格式
严格遵循以下嵌套JSON结构（支持children字段）：
[
  {
    "id": "1",
    "title": "一、项目背景",
    "level": 1,
    "targetWords": 1000,
    "content": "至少200个汉字的初始内容...",
    "children": [
      {
        "id": "1.1",
        "title": "（一）行业现状",
        "level": 2,
        "targetWords": 400,
        "content": "至少200个汉字的初始内容...",
        "children": []
      }
    ]
  }
]`,
        config: {
          thread_id: currentThreadId.value
        },
        meta: {
          context: {
            projectId,
            deliverableId,
            mode: 'global',
            scenario: 'outline'
          }
        }
      })
      
      let aiResponse = ''
      if (res.ok) {
        console.log('[Outline] AI 响应流已建立')
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          
          for (const line of lines) {
            const trimmedLine = line.trim()
            if (!trimmedLine) continue

            // 兼容 SSE 格式 (data: {...}) 和 纯 JSON 行格式 ({...})
            const rawJson = trimmedLine.startsWith('data: ') 
              ? trimmedLine.slice(6) 
              : trimmedLine

            try {
              const data = JSON.parse(rawJson)

              // 处理 agent_state 更新
              if (data.status === 'agent_state' && data.agent_state && data.agent_state.documentStructure) {
                console.log('收到智能体状态更新 (大纲):', data.agent_state.documentStructure)
                documentData.value = data.agent_state.documentStructure
              }

              // 兼容多种内容字段格式
              const content = data.response || (data.msg && data.msg.content) || data.content || data.answer || ''
              if (content) aiResponse += content
              
              // 保存 thread_id
              if (data.thread_id) {
                currentThreadId.value = data.thread_id
              } else if (data.meta && data.meta.thread_id) {
                currentThreadId.value = data.meta.thread_id
              }
            } catch (e) {
              console.warn('解析大纲数据行失败:', e, 'Line:', trimmedLine)
            }
          }
        }

        // 处理最后剩余的 buffer
        if (buffer.trim()) {
          const trimmedLine = buffer.trim()
          const rawJson = trimmedLine.startsWith('data: ') ? trimmedLine.slice(6) : trimmedLine
          try {
            const data = JSON.parse(rawJson)
            // 兼容多种内容字段格式
            const content = data.response || (data.msg && data.msg.content) || data.content || data.answer || ''
            if (content) aiResponse += content
          } catch (e) {
            // 忽略最后可能的截断
          }
        }
      }
      
      // 尝试解析 AI 返回的嵌套 JSON
      console.log('[Outline] AI 响应内容接收完毕, 长度:', aiResponse.length)
      // 先尝试从 <content> 标签中提取
      let jsonStr = ''
      const contentMatch = aiResponse.match(/<content>([\s\S]*?)<\/content>/)
      if (contentMatch) {
        jsonStr = contentMatch[1].trim()
        
        // 如果 content 标签内混合了 Markdown 标题和 JSON，尝试只提取 JSON 部分
        if (jsonStr.includes('##') || jsonStr.includes('#')) {
          console.log('[Outline] <content> 标签内检测到 Markdown 标记，尝试进一步提取 JSON 数组')
          const arrayMatch = jsonStr.match(/\[\s*\{[\s\S]*\}\s*\]/)
          if (arrayMatch) {
            jsonStr = arrayMatch[0].trim()
          }
        }
      }

      // 如果没找到 content 标签，尝试直接匹配 JSON 数组
      if (!jsonStr) {
        // 使用更可靠的正则表达式，找到最外层的 [] 对
        let depth = 0
        let startIdx = -1
        let lastEndIdx = -1
        for (let i = 0; i < aiResponse.length; i++) {
          if (aiResponse[i] === '[') {
            if (depth === 0) startIdx = i
            depth++
          } else if (aiResponse[i] === ']') {
            depth--
            if (depth === 0 && startIdx !== -1) {
              lastEndIdx = i
              // 不 break，继续找，可能后面还有更完整的（虽然通常只有一个）
            }
          }
        }
        
        if (startIdx !== -1) {
          if (lastEndIdx !== -1) {
            // 找到了完整的数组
            jsonStr = aiResponse.substring(startIdx, lastEndIdx + 1)
          } else {
            // 没找到完整的数组，但找到了开始，说明被截断了
            jsonStr = aiResponse.substring(startIdx)
            console.warn('⚠️ 检测到 JSON 数组可能被截断，尝试提取起始部分')
          }
        }
      }

      // 提取到 JSON 字符串后，清理可能的 markdown 代码块标记
      jsonStr = jsonStr.replace(/```json\n?/g, '').replace(/```\n?/g, '').trim()

      if (jsonStr) {
        console.log('[Outline] 提取到 JSON 字符串, 准备解析...')
        try {
          // smartParseJson 内部已包含 repairTruncatedJson 和 tryExtractValidJson 逻辑
          let parsedOutline = smartParseJson(jsonStr)

          // 兼容性处理：如果返回的是 {outline: [...]} 格式，提取 outline 数组
          if (parsedOutline.outline && Array.isArray(parsedOutline.outline)) {
            parsedOutline = parsedOutline.outline
          }

          // 确保解析结果是一个数组
          if (!Array.isArray(parsedOutline)) {
            throw new Error('解析结果不是数组格式')
          }

          parsedOutline = validateAndFixTargetWords(parsedOutline)

          // 使用新的 parseNestedOutline 函数解析嵌套结构
          const flatOutline = parseNestedOutline(parsedOutline)
          console.log('[Outline] 嵌套 JSON 已扁平化, 章节数:', flatOutline.length)
          
          documentData.value = flatOutline.map(item => ({
            ...item,
            content: item.content || '',
            status: item.content ? 'completed' : 'pending'
          }))

          // 自动展开第一级章节
          expandedKeys.value = documentData.value
            .filter(s => !s.parentId && documentData.value.some(child => child.parentId === s.id))
            .map(s => s.id)

          console.log('✅ [Outline] 成功解析 AI 生成的大纲，已更新 documentData')
          console.log('📊 [Outline] 总目标字数:', documentData.value.reduce((sum, s) => sum + (s.targetWords || 0), 0))
          
          // 如果长度发生了变化，说明进行了自动修复
          if (jsonStr.length !== JSON.stringify(parsedOutline).length) {
            console.log('检测到 AI 返回的大纲数据不完整，已尝试自动修复并加载。')
          }
        } catch (parseErr) {
          console.error('❌ [Outline] JSON 解析彻底失败:', parseErr)
          console.log('[Outline] 原始 JSON 字符串（前 500 字符）:', jsonStr.substring(0, 500))
          documentData.value = []
          console.log('大纲解析失败，AI 返回数据格式异常。')
        }
      } else {
        console.warn('⚠️ [Outline] 未能提取到 JSON 字符串，可能 AI 响应为空或格式不正确')
        documentData.value = []
      }
    } catch (e) {
      console.warn('❌ [Outline] AI 大纲生成请求失败或解析严重异常:', e)
      message.error('大纲生成过程中发生错误，请稍后重试。')
    }

    planningMsg.isPlanning = false
    chatMessages.value.push({
      role: 'assistant',
      content: `大纲已就绪（目标 ${deliverable.value.targetWords || 5000} 字），已为您优化了结构。请审阅确认，点击章节即可开始精细化协作。`
    })

    // 默认选中第一个章节
    if (documentData.value.length > 0 && !activeSectionId.value) {
      console.log('[Outline] 自动选中第一个章节:', documentData.value[0].id)
      activeSectionId.value = documentData.value[0].id
      scrollToSection(documentData.value[0].id)
    }

    // 自动保存大纲到数据库
    console.log('[Outline] 准备执行自动保存...')
    await saveDocument()

    // 刷新交付物信息（更新状态为"已撰写"）
    console.log('[Outline] 准备刷新交付物基本信息...')
    await fetchDeliverableInfo()
    console.log('[Outline] 大纲生成全流程处理完毕')
  } catch (err) {
    planningMsg.isPlanning = false
    message.error('大纲生成失败')
  } finally {
    isGenerating.value = false
  }
}

const uploadStatus = ref('ready') // 'ready', 'uploading', 'done', 'failed'
const uploadProgress = ref(0)
const selectedFile = ref(null)
const showToast = ref(false)
const toastMsg = ref('')
const toastType = ref('success') // 'success', 'error'

// 功能按钮配置
const actions = [
  { id: 'outline', label: '生成大纲', color: '#00A3E0', icon: Sparkles },
  { id: 'draft', label: '生成初稿', color: '#86BC25', icon: FileText }, // Deloitte Blue
  { id: 'polish', label: '全文润色', color: '#6A3D9A', icon: Sparkles }  // Deloitte Purple
]

// 顺序控制：判断按钮是否应该被禁用
const isActionDisabled = (actionId) => {
  if (isGenerating.value) return true

  switch (actionId) {
    case 'draft':
      // 生成初稿：需要有大纲
      return documentData.value.length === 0
    case 'polish':
      // 全文润色：需要有初稿内容（至少有一个章节有内容）
      return !documentData.value.some(s => s.content && s.content.trim().length > 0)
    default:
      return false
  }
}

// 获取按钮禁用提示信息
const getActionDisabledTooltip = (actionId) => {
  switch (actionId) {
    case 'draft':
      return '请先生成大纲'
    case 'polish':
      return '请先生成初稿内容'
    default:
      return ''
  }
}

const handleAction = async (actionId) => {
  if (isGenerating.value) return

  // 生成新的 Trace ID 用于本次生成流程
  const prefix = actionId === 'polish' ? 'polish' : 'draft';
  const newTraceId = `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  setTraceId(newTraceId);

  const userStore = useUserStore();
  const userId = userStore.user?.user_id || 'unknown';

  // 添加模式验证日志
  logger.info(`模式验证 [Action: ${actionId}]`, {
    type: 'mode_validation',
    isLocalMode: isLocalMode.value,
    activeSectionId: activeSectionId.value,
    activeSectionTitle: activeSectionId.value ? documentData.value.find(s => s.id === activeSectionId.value)?.title : 'N/A'
  });

  logger.info(`用户点击按钮: ${actionId}`, {
    type: 'user_action',
    actionId,
    userId,
    projectId,
    deliverableId
  });

  isGenerating.value = true

  try {
    if (actionId === 'draft') {
      logger.info('开始生成完整初稿流程', {
        sectionCount: documentData.value.length
      });

      // 初始化进度消息
      const targetSections = isLocalMode.value 
        ? documentData.value.filter(s => s.id === activeSectionId.value)
        : documentData.value
      
      const totalSections = targetSections.length
      const progressMsg = {
        role: 'assistant',
        type: 'progress',
        label: isLocalMode.value ? '正在生成选中章节进度' : '正在生成完整初稿进度',
        current: 0,
        total: totalSections,
        content: isLocalMode.value ? `正在为您生成章节 "${targetSections[0]?.title}"，请稍候...` : '正在为您生成完整初稿，请稍候...'
      }
      chatMessages.value.push(progressMsg)

      const agentId = 'DeliverableAgent'
      let completedSections = 0

      // 遍历目标章节进行生成
      for (const section of targetSections) {
        writingSectionId.value = section.id
        section.status = 'writing'

        logger.info(`[Draft Generation] 开始生成章节 [Mode: ${isLocalMode.value ? 'Local' : 'Global'}]: ${section.title}`, {
          type: 'mode_validation',
          sectionId: section.id,
          targetWords: section.targetWords,
          projectId,
          deliverableId
        });

        try {
          const params = {
            query: `请为章节"${section.title}"生成初稿内容，目标字数：${section.targetWords}字。`,
            config: {
              thread_id: currentThreadId.value,
              configurable: {
                deliverableId: deliverableId
              }
            },
            meta: {
              context: {
                projectId,
                deliverableId,
                mode: 'local', // 使用局部模式，针对单个章节生成
                activeSectionId: section.id,
                documentStructure: documentData.value.map(s => ({
                  id: s.id,
                  title: s.title,
                  targetWords: s.targetWords
                })),
                scenario: 'draft' // 明确指定场景为初稿生成
              }
            }
          }

          logger.debug(`[Draft Generation] 发送 API 请求 [章节: ${section.title}]`, { params });
          const response = await agentApi.sendAgentMessage(agentId, params)
          logger.info(`[Draft Generation] 收到 API 响应状态 [章节: ${section.title}]: ${response.status}`);

          if (response.ok) {
            const reader = response.body.getReader()
            const decoder = new TextDecoder()
            let fullText = ''
            let buffer = ''
            let chunkCount = 0

            while (true) {
              const { done, value } = await reader.read()
              if (done) {
                logger.info(`[Draft Generation] SSE 流结束 [章节: ${section.title}], 共接收 ${chunkCount} 个数据块`);
                break
              }

              chunkCount++
              buffer += decoder.decode(value, { stream: true })
              const lines = buffer.split('\n')
              buffer = lines.pop() || ''

              for (const line of lines) {
                const trimmedLine = line.trim()
                if (!trimmedLine) continue

                // 兼容 SSE 格式 (data: {...}) 和 纯 JSON 行格式 ({...})
                const rawJson = trimmedLine.startsWith('data: ') 
                  ? trimmedLine.slice(6) 
                  : trimmedLine

                try {
                  const data = JSON.parse(rawJson)

                  // 处理 agent_state 更新 (重要：如果是通过工具生成的正文，内容会在这里)
                  if (data.status === 'agent_state' && data.agent_state && data.agent_state.documentStructure) {
                    logger.debug(`[Draft Generation] 收到智能体状态更新 (章节生成):`, {
                      sectionId: section.id,
                      structureLength: data.agent_state.documentStructure.length
                    })
                    
                    // 优化：不要直接覆盖整个 documentData.value，这会导致当前 loop 的 section 引用失效
                    // 采用合并策略更新
                    const newStructure = data.agent_state.documentStructure
                    let hasChanged = false
                    newStructure.forEach(newSec => {
                      const existingSec = documentData.value.find(s => s.id === newSec.id)
                      if (existingSec) {
                        // 如果后端传回了内容，则更新（移除长度判断，信任后端数据）
                        if (newSec.content !== undefined && newSec.content !== existingSec.content) {
                          existingSec.content = newSec.content
                          hasChanged = true
                        }
                        // 更新状态和标题
                        if (newSec.status && existingSec.status !== newSec.status) {
                          existingSec.status = newSec.status
                          hasChanged = true
                        }
                        if (newSec.title && existingSec.title !== newSec.title) {
                          existingSec.title = newSec.title
                          hasChanged = true
                        }
                      }
                    })
                    
                    // 显式触发响应式更新，确保 UI 刷新
                    if (hasChanged) {
                      documentData.value = [...documentData.value]
                    }
                    
                    // 同步当前正在生成的章节引用，确保后续赋值不会覆盖掉工具生成的内容
                    const updatedSection = documentData.value.find(s => s.id === section.id)
                    if (updatedSection && updatedSection.content) {
                      section.content = updatedSection.content
                    }
                  }

                  // 兼容多种内容字段格式
                  const content = data.response || (data.msg && data.msg.content) || data.content || data.answer || ''

                  if (content) {
                    fullText += content

                    // 保存 thread_id
                    if (data.thread_id) {
                      currentThreadId.value = data.thread_id
                    } else if (data.meta && data.meta.thread_id) {
                      currentThreadId.value = data.meta.thread_id
                    }
                  }

                  if (data.type === 'error') {
                    logger.error(`[Draft Generation] SSE 错误消息:`, data.error);
                  }
                } catch (e) {
                  logger.warn(`[Draft Generation] 解析数据行失败:`, { error: e.message, line: trimmedLine });
                }
              }
            }

            // 处理最后剩余的 buffer
            if (buffer.trim()) {
              const trimmedLine = buffer.trim()
              const rawJson = trimmedLine.startsWith('data: ') ? trimmedLine.slice(6) : trimmedLine
              try {
                const data = JSON.parse(rawJson)
                const content = data.response || (data.msg && data.msg.content) || data.content || data.answer || ''
                if (content) fullText += content
              } catch (e) {
                // 忽略最后可能的截断
              }
            }

            logger.info(`[Draft Generation] 章节生成原始文本完成 [章节: ${section.title}]`, {
              contentLength: fullText.length,
              preview: fullText.substring(0, 100) + '...'
            });

            // 提取 <content> 标签中的内容
            let cleanContent = fullText
            const contentMatch = fullText.match(/<content>([\s\S]*?)(?:<\/content>|$)/)
            if (contentMatch) {
              cleanContent = contentMatch[1].trim()
              logger.info(`[Draft Generation] 成功提取 <content> 标签内容 [章节: ${section.title}], 长度: ${cleanContent.length}`);
            } else {
              logger.warn(`[Draft Generation] 未找到 <content> 标签 [章节: ${section.title}], 将尝试清理其他标签`);
              // 如果没有 content 标签，移除其他标签
              cleanContent = fullText
                .replace(/<\/?think[^>]*>/gi, '')
                .replace(/<summary>[\s\S]*?(?:<\/summary>|$)/g, '')
                .replace(/<check>[\s\S]*?(?:<\/check>|$)/g, '')
                .trim()
            }

            // 剥离可能冗余的章节标题
            const sectionTitle = section.title
            const escapedTitle = sectionTitle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
            const redundantPattern = new RegExp(`^\\s*(?:##+|#|\\*\\*|)\\s*(?:\\d+\\.?\\s*)?${escapedTitle}\\s*(?:\\*\\*)?\\n*`, 'i')
            cleanContent = cleanContent.replace(redundantPattern, '').trim()

            // 关键修复：确保更新的是最新的 documentData 引用中的对象
            const latestSection = documentData.value.find(s => s.id === section.id)
            if (latestSection) {
              if (cleanContent) {
                latestSection.content = cleanContent
                logger.info(`[Draft Generation] 章节内容已更新: ${latestSection.title}, 长度: ${cleanContent.length}`);
              }
              latestSection.status = 'completed'
            } else {
              // 兜底：如果找不到最新的，尝试更新当前引用（虽然可能已脱离 array）
              if (cleanContent) section.content = cleanContent
              section.status = 'completed'
            }
            
            // 显式触发响应式更新
            documentData.value = [...documentData.value]
            logger.info(`[Draft Generation] 章节状态更新为 completed: ${section.title}`);
          } else {
            const errorText = await response.text();
            logger.error(`[Draft Generation] 章节 "${section.title}" API 响应失败`, {
              status: response.status,
              error: errorText
            });
            throw new Error(`网络响应失败: ${response.status}`)
          }
        } catch (err) {
          logger.error(`[Draft Generation] 章节 "${section.title}" 生成过程发生异常`, {
            error: err.message,
            stack: err.stack
          });
          section.status = 'pending'
        }

        completedSections++
        // 更新现有进度消息
        progressMsg.current = completedSections
        if (completedSections === totalSections) {
          progressMsg.content = isLocalMode.value 
            ? `✅ 章节 "${targetSections[0]?.title}" 初稿已生成完毕！`
            : '✅ 完整初稿已生成完毕！您可以点击左侧目录进行查看和精调。'
        }
      }

      // 自动保存
      console.log('[Draft Generation] 章节生成任务全部完成，触发自动保存');
      await saveDocument()

      // 刷新交付物信息（更新状态）
      console.log('[Draft Generation] 正在刷新交付物详情...');
      await fetchDeliverableInfo()
      console.log('[Draft Generation] 刷新完成');
    } else if (actionId === 'polish') {
      // 润色逻辑
      const targetSections = isLocalMode.value 
        ? documentData.value.filter(s => s.id === activeSectionId.value)
        : documentData.value

      const totalSections = targetSections.length
      const progressMsg = {
        role: 'assistant',
        type: 'progress',
        label: isLocalMode.value ? '正在润色选中章节进度' : '正在润色进度',
        current: 0,
        total: totalSections,
        content: isLocalMode.value ? `正在对章节 "${targetSections[0]?.title}" 进行专业润色...` : '正在对全文进行专业润色，统一语体风格...'
      }
      chatMessages.value.push(progressMsg)

      const agentId = 'DeliverableAgent'
      let polishedSections = 0

      // 遍历目标章节进行润色
      for (const section of targetSections) {
        // 跳过没有内容的章节
        if (!section.content || section.content.trim().length === 0) {
          polishedSections++
          progressMsg.current = polishedSections
          continue
        }

        writingSectionId.value = section.id
        section.status = 'writing'

        logger.info(`开始润色章节 [Mode: ${isLocalMode.value ? 'Local' : 'Global'}]: ${section.title}`, {
          type: 'mode_validation',
          sectionId: section.id
        });

        try {
          const params = {
            query: `请对章节"${section.title}"进行全文润色，目标字数：${section.content.length}字。

原文内容：
${section.content}`,
            config: {
              thread_id: currentThreadId.value
            },
            meta: {
              context: {
                projectId,
                deliverableId,
                mode: 'local',
                activeSectionId: section.id,
                documentStructure: documentData.value.map(s => ({
                  id: s.id,
                  title: s.title,
                  targetWords: s.targetWords
                })),
                scenario: 'polish' // 明确指定场景为润色
              }
            }
          }

          const response = await agentApi.sendAgentMessage(agentId, params)

          if (response.ok) {
            const reader = response.body.getReader()
            const decoder = new TextDecoder()
            let fullText = ''
            let buffer = ''

            while (true) {
              const { done, value } = await reader.read()
              if (done) break

              buffer += decoder.decode(value, { stream: true })
              const lines = buffer.split('\n')
              buffer = lines.pop() || ''

              for (const line of lines) {
                const trimmedLine = line.trim()
                if (!trimmedLine) continue

                // 兼容 SSE 格式 (data: {...}) 和 纯 JSON 行格式 ({...})
                const rawJson = trimmedLine.startsWith('data: ') 
                  ? trimmedLine.slice(6) 
                  : trimmedLine

                try {
                  const data = JSON.parse(rawJson)

                  // 处理 agent_state 更新
                  if (data.status === 'agent_state' && data.agent_state && data.agent_state.documentStructure) {
                    console.log('收到智能体状态更新 (润色):', data.agent_state.documentStructure)
                    
                    // 优化：采用合并策略更新，防止当前 loop 的 section 引用失效
                    const newStructure = data.agent_state.documentStructure
                    let hasChanged = false
                    newStructure.forEach(newSec => {
                      const existingSec = documentData.value.find(s => s.id === newSec.id)
                      if (existingSec) {
                        // 如果后端传回了内容，且比当前内容新（更长），则更新
                        if (newSec.content && (!existingSec.content || newSec.content.length > existingSec.content.length)) {
                          existingSec.content = newSec.content
                          hasChanged = true
                        }
                        // 更新状态和标题
                        if (newSec.status && existingSec.status !== newSec.status) {
                          existingSec.status = newSec.status
                          hasChanged = true
                        }
                        if (newSec.title && existingSec.title !== newSec.title) {
                          existingSec.title = newSec.title
                          hasChanged = true
                        }
                      }
                    })
                    
                    // 显式触发响应式更新，确保 UI 刷新
                    if (hasChanged) {
                      documentData.value = [...documentData.value]
                    }
                    
                    const updatedSection = documentData.value.find(s => s.id === section.id)
                    if (updatedSection && updatedSection.content) {
                      section.content = updatedSection.content
                    }
                  }

                  const content = data.response || (data.msg && data.msg.content) || data.content || data.answer || ''

                  if (content) {
                    fullText += content

                    if (data.thread_id) {
                      currentThreadId.value = data.thread_id
                    } else if (data.meta && data.meta.thread_id) {
                      currentThreadId.value = data.meta.thread_id
                    }
                  }
                } catch (e) {
                  console.warn('解析数据失败:', e)
                }
              }
            }

            // 处理最后剩余的 buffer
            if (buffer.trim()) {
              const trimmedLine = buffer.trim()
              const rawJson = trimmedLine.startsWith('data: ') ? trimmedLine.slice(6) : trimmedLine
              try {
                const data = JSON.parse(rawJson)
                const content = data.response || (data.msg && data.msg.content) || data.content || data.answer || ''
                if (content) fullText += content
              } catch (e) {
                // 忽略
              }
            }

            // 提取 <content> 标签中的内容
            let polishedContent = fullText
            const contentMatch = fullText.match(/<content>([\s\S]*?)(?:<\/content>|$)/)
            if (contentMatch) {
              polishedContent = contentMatch[1].trim()
            } else {
              // 如果没有 content 标签，移除其他标签
              polishedContent = fullText
                .replace(/<\/?think[^>]*>/gi, '')
                .replace(/<summary>[\s\S]*?(?:<\/summary>|$)/g, '')
                .replace(/<check>[\s\S]*?(?:<\/check>|$)/g, '')
                .trim()
            }

            // 剥离可能冗余的章节标题
            const sectionTitle = section.title
            const escapedTitle = sectionTitle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
            const redundantPattern = new RegExp(`^\\s*(?:##+|#|\\*\\*|)\\s*(?:\\d+\\.?\\s*)?${escapedTitle}\\s*(?:\\*\\*)?\\n*`, 'i')
            polishedContent = polishedContent.replace(redundantPattern, '').trim()

            // 如果润色后内容为空，保持原内容 (可能已由 agent_state 更新或保留原样)
            if (polishedContent && polishedContent.length > 0) {
              section.content = polishedContent
            }
            section.status = 'completed'
          } else {
            throw new Error(`网络响应失败: ${response.status}`)
          }
        } catch (err) {
          console.error(`章节 "${section.title}" 润色失败:`, err)
          section.status = 'completed' // 即使失败也保持已完成的标记
        }

        polishedSections++
        // 更新现有进度消息
        progressMsg.current = polishedSections
        if (polishedSections === totalSections) {
          progressMsg.content = isLocalMode.value
            ? `✅ 章节 "${targetSections[0]?.title}" 润色完成！`
            : '✅ 全文润色完成！已将整体语体调整为更加商务、严谨的风格。'
        }
      }

      // 自动保存
      await saveDocument()

      // 刷新交付物信息（更新状态）
      console.log('[Polish] 正在刷新交付物详情...');
      await fetchDeliverableInfo()
      console.log('[Polish] 刷新完成');
    }
  } catch (err) {
    console.error('操作失败:', err)
    message.error('操作失败: ' + (err.message || '未知错误'))
  } finally {
    isGenerating.value = false
    writingSectionId.value = null
  }
}
// 文件上传处理
const MAX_SIZE = 50 // MB
const handleFileUpload = (event) => {
  const file = event.target.files[0]
  if (!file) return
  
  // 格式校验
  const allowedTypes = ['.txt', '.doc', '.docx']
  const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()
  if (!allowedTypes.includes(ext)) {
    showToastMsg('只支持 .txt, .doc, .docx 格式', 'error')
    return
  }
  
  // 大小校验
  if (file.size > MAX_SIZE * 1024 * 1024) {
    showToastMsg(`文件大小不能超过 ${MAX_SIZE}MB`, 'error')
    return
  }
  
  selectedFile.value = file
  simulateUpload()
}

const simulateUpload = () => {
  uploadStatus.value = 'uploading'
  uploadProgress.value = 0
  
  const interval = setInterval(() => {
    uploadProgress.value += Math.floor(Math.random() * 15) + 5
    if (uploadProgress.value >= 100) {
      uploadProgress.value = 100
      uploadStatus.value = 'done'
      clearInterval(interval)
      showToastMsg('文件上传成功', 'success')
    }
  }, 300)
}

const showToastMsg = (msg, type = 'success') => {
  toastMsg.value = msg
  toastType.value = type
  showToast.value = true
  setTimeout(() => {
    showToast.value = false
  }, 3000)
}

const sendMessage = async () => {
  if (!userInput.value.trim() || isGenerating.value) return
  
  const userMsg = userInput.value.trim()
  chatMessages.value.push({ role: 'user', content: userMsg })
  userInput.value = ''
  isGenerating.value = true
  
  if (activeSectionId.value) {
    writingSectionId.value = activeSectionId.value
  }

  try {
    const params = {
      query: userMsg,
      config: {
        thread_id: currentThreadId.value,
        system_prompt: `你是一个专业的交付物生成助手。

## 当前工作模式
- 模式：${isLocalMode.value ? '局部模式（Local Mode）- 针对选中章节' : '全局模式（Global Mode）- 针对整个文档'}
${isLocalMode.value && activeSectionId.value ? `- 选中章节：${documentData.value.find(s => s.id === activeSectionId.value)?.title} (${activeSectionId.value})` : ''}

## 可用工具
你有以下 4 个文档操作工具：
1. **generate_section_content** - 生成章节文字内容（不修改结构）
2. **add_subsection** - 添加子章节（修改结构）
3. **delete_section** - 删除章节
4. **update_section_content** - 更新/重写章节内容

## 工作要求
- 目标字数：${deliverable.value.targetWords} 字
- 风格要求：商务、严谨、专业
${isLocalMode.value && activeSectionId.value ? `- 编号规则：必须继承父章节编号。如父章节是 "2. 项目背景"，子章节从 "2.1" 开始` : ''}

## 操作约束
- 根据用户意图选择合适的工具
- 理解用户指令后再执行操作
- 如果用户意图不明确，可询问用户

## ⚠️ 字数控制（CRITICAL）
- **必须**从 documentStructure 中获取章节的实际 targetWords
- **字数确定优先级**：
  1. **用户明确指定字数**（如"生成500字内容"、"润色为2000字"）→ 尊重用户意图，使用用户指定的字数
  2. **用户没有指定字数** → **必须**使用 documentStructure 中的实际 targetWords
- **绝对禁止**：AI 自己随意猜测或生成不合理的字数（如 270、100、50 等明显不合理的数字）
- **正确示例**：
  - 用户说"生成500字内容" → 使用 500 字 ✅
  - 用户说"全文润色"（未提字数）→ 从 documentStructure 获取实际 targetWords（如 15000 字）✅
- **错误示例**：
  - 用户说"全文润色"（未提字数）→ AI 自己决定使用 270 字 ❌
  - 用户说"润色内容" → AI 使用 100 字 ❌
- AI应该在工具调用时明确说明字数来源："使用用户指定的 500 字" 或 "使用章节实际目标字数 15000 字"

请确保内容符合项目上下文，并进行自我反思以剔除幻觉内容。`
      },
      meta: {
        context: {
          projectId,
          deliverableId,
          mode: isLocalMode.value ? 'local' : 'global',
          activeSectionId: activeSectionId.value,
          documentStructure: documentData.value.map(s => ({ id: s.id, title: s.title, targetWords: s.targetWords }))
        }
      }
    }

    const agentId = 'DeliverableAgent'
    let response
    try {
      response = await agentApi.sendAgentMessage(agentId, params)
    } catch (e) {
      console.warn('API 调用失败，使用模拟流式输出:', e)
      // 模拟逻辑略过，直接抛出异常以便进入 catch
      throw e
    }
    
    if (!response.ok) throw new Error(`网络响应失败: ${response.status}`)

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    
    // 初始化消息对象
    let assistantMsg = { 
      role: 'assistant', 
      content: '', 
      thinking: '', 
      summary: '', 
      qualityCheck: '',
      isThinking: false,
      isFinished: false,
      confirmed: false
    }
    chatMessages.value.push(assistantMsg)

    let fullText = ''
    let buffer = ''
    let currentSectionIndex = -1
    
    if (isLocalMode.value) {
      currentSectionIndex = documentData.value.findIndex(s => s.id === activeSectionId.value)
      if (currentSectionIndex !== -1) {
        documentData.value[currentSectionIndex].status = 'writing'
        documentData.value[currentSectionIndex].content = '' 
      }
    }

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmedLine = line.trim()
        if (!trimmedLine) continue

        // 兼容 SSE 格式 (data: {...}) 和 纯 JSON 行格式 ({...})
        const rawJson = trimmedLine.startsWith('data: ') 
          ? trimmedLine.slice(6) 
          : trimmedLine

        try {
          const data = JSON.parse(rawJson)
          
          // 处理 agent_state 更新
          if (data.status === 'agent_state' && data.agent_state && data.agent_state.documentStructure) {
            console.log('收到智能体状态更新:', data.agent_state.documentStructure)
            // 优化：不要直接覆盖整个 documentData.value，这会导致当前 loop 的 section 引用失效
            // 采用合并策略更新
            const newStructure = data.agent_state.documentStructure
            newStructure.forEach(newSec => {
              const existingSec = documentData.value.find(s => s.id === newSec.id)
              if (existingSec) {
                // 如果后端传回了内容，且比当前内容新（更长），则更新
                if (newSec.content && (!existingSec.content || newSec.content.length >= existingSec.content.length)) {
                  existingSec.content = newSec.content
                }
                // 更新状态和标题
                if (newSec.status) existingSec.status = newSec.status
                if (newSec.title) existingSec.title = newSec.title
              }
            })
            
            // 如果是在局部模式下生成内容，尝试从更新后的结构中提取当前章节的内容
            if (isLocalMode.value && activeSectionId.value) {
              const currentSection = documentData.value.find(s => s.id === activeSectionId.value)
              if (currentSection && currentSection.content && !assistantMsg.content) {
                // 如果 assistantMsg 还没有内容，同步一下，避免用户看到空白
                assistantMsg.content = currentSection.content
              }
            }
          }

          const content = data.response || (data.msg && data.msg.content) || data.content || data.answer || ''
          
          if (data.thread_id) {
            currentThreadId.value = data.thread_id
          } else if (data.meta && data.meta.thread_id) {
            currentThreadId.value = data.meta.thread_id
          }
          
          if (content) {
            fullText += content
              
              // 1. 提取深度思考 <think>
              const thinkMatch = fullText.match(/<think>([\s\S]*?)(?:<\/think>|$)/)
              if (thinkMatch) {
                assistantMsg.thinking = thinkMatch[1]
                assistantMsg.isThinking = !fullText.includes('</think>')
              }

              // 2. 提取内容概述 <summary>
              const summaryMatch = fullText.match(/<summary>([\s\S]*?)(?:<\/summary>|$)/)
              if (summaryMatch) {
                assistantMsg.summary = summaryMatch[1].trim()
              }

              // 3. 提取正式生成内容 <content>
              const contentMatch = fullText.match(/<content>([\s\S]*?)(?:<\/content>|$)/)
              if (contentMatch) {
                const rawContent = contentMatch[1].trim()
                
                // 检查是否是 JSON 格式的大纲数据
                let isJsonOutline = false
                let parsedJson = null
                try {
                  const cleaned = rawContent.replace(/```json|```/g, '').trim()
                  if (cleaned.startsWith('[') && cleaned.endsWith(']')) {
                    const parsed = JSON.parse(cleaned)
                    if (Array.isArray(parsed) && parsed.length > 0 && parsed[0].title) {
                      isJsonOutline = true
                      parsedJson = parsed
                    }
                  }
                } catch (e) {
                  // 不是完整 JSON 或解析失败
                }

                if (isJsonOutline) {
                  if (isLocalMode.value) {
                    // 如果是局部模式下的 JSON 大纲，解析并同步到目录树
                    syncSubSectionsFromJson(activeSectionId.value, parsedJson)
                    
                    // 将 JSON 转换为标准的 Word 版式（Markdown 标题列表）展示在右侧
                    const markdownVersion = parsedJson.map(item => `## ${item.title}\n\n${item.content || '(请在此输入内容)'}`).join('\n\n')
                    assistantMsg.content = `已为您规划了子章节结构。`
                    
                    const targetSection = documentData.value.find(s => s.id === activeSectionId.value)
                    if (targetSection && targetSection.content !== markdownVersion) {
                      targetSection.content = markdownVersion
                    }
                  } else {
                    // 全局模式下的大纲生成
                    const rawOutline = parsedJson.map(item => ({
                      ...item,
                      content: '',
                      status: 'pending'
                    }))
                    documentData.value = reconstructHierarchy(rawOutline)
                    
                    // 自动展开新生成的大纲
                    expandedKeys.value = documentData.value
                      .filter(s => documentData.value.some(child => child.parentId === s.id))
                      .map(s => s.id)
                    
                    assistantMsg.content = '已为您规划全文大纲，请在右侧查看。'
                  }
                } else {
                  // 只有当内容看起来不是正在构建中的 JSON 时，才更新到右侧
                  const isPartialJson = rawContent.trim().startsWith('[') || rawContent.trim().startsWith('```json')
                  
                  assistantMsg.content = rawContent
                  
                  if (isLocalMode.value && !isPartialJson) {
                    const targetSection = documentData.value.find(s => s.id === activeSectionId.value)
                    if (targetSection && targetSection.content !== rawContent) {
                      // 关键修复：存入前剥离 AI 可能返回的当前章节冗余标题，以及可能出现的下一个主章节标题
                      const sectionTitle = targetSection.title
                      const escapedTitle = sectionTitle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
                      // 优化匹配：只在内容【最开头】匹配冗余标题，且增加非贪婪匹配，避免误伤
                      const redundantPattern = new RegExp(`^\\s*(?:##+|###+|#|\\*\\*|)\\s*(?:\\d+\\.?\\s*)?${escapedTitle}\\s*(?:\\*\\*)?\\n*`, 'i')
                      let cleanContent = rawContent.replace(redundantPattern, '').trim()
                      
                      // 进一步剥离：如果内容中出现了下一个同级或高级章节的标题（通常是 AI 自动续写导致的），也将其及之后的内容剥离
                      // 匹配模式：行首跟着一个看起来像章节标题的行，且该标题不是当前章节的子章节
                      // 1. 获取当前章节的编号前缀，如 "1."
                      const sectionMatch = sectionTitle.match(/^(\d+)\./)
                      if (sectionMatch) {
                        const currentNum = parseInt(sectionMatch[1])
                        const nextNum = currentNum + 1
                        // 匹配如 "2. ", "## 2. ", "### 2. " 等开头的行，支持在行首或换行后
                        const nextSectionRegex = new RegExp(`(?:\\n|^)(?:##+|###+|#|\\*\\*|)\\s*${nextNum}\\.\\s+.*`, 'm')
                        if (nextSectionRegex.test(cleanContent)) {
                          // 使用 split 并取第一部分，确保彻底截断
                          cleanContent = cleanContent.split(nextSectionRegex)[0].trim()
                        }
                      }
                      
                      // 通用兜底剥离：如果内容中出现了明显的 Markdown 标题结构，且包含新的主编号
                      const generalNextPattern = /(?:\n|^)(?:##+|#)\s+\d+\..*/g
                      if (generalNextPattern.test(cleanContent)) {
                        cleanContent = cleanContent.split(generalNextPattern)[0].trim()
                      }
                      
                      targetSection.content = cleanContent
                      syncSubSectionsToOutlineRealtime(activeSectionId.value, cleanContent)
                    }
                  }
                }
              }

              // 4. 提取质量自检 <check>
              const checkMatch = fullText.match(/<check>([\s\S]*?)(?:<\/check>|$)/)
              if (checkMatch) {
                assistantMsg.qualityCheck = checkMatch[1].trim()
              }
              
              // 5. 兜底逻辑：如果 AI 返回的内容中不包含 <content> 标签，则尝试提取标签外的内容
              if (!fullText.includes('<content>')) {
                // 移除所有已知标签及其内容，剩下的就是正式内容
                let cleanContent = fullText
                  .replace(/<think>[\s\S]*?(?:<\/think>|$)/g, '')
                  .replace(/<summary>[\s\S]*?(?:<\/summary>|$)/g, '')
                  .replace(/<check>[\s\S]*?(?:<\/check>|$)/g, '')
                  .trim()
                
                if (cleanContent) {
                  assistantMsg.content = cleanContent
                  if (isLocalMode.value && currentSectionIndex !== -1) {
                    documentData.value[currentSectionIndex].content = cleanContent
                  }
                }
              }
            }
          } catch (e) {
            console.warn('解析数据失败:', e)
          }
        }
      }

      // 处理最后剩余的 buffer
    if (buffer.trim()) {
      const trimmedLine = buffer.trim()
      const rawJson = trimmedLine.startsWith('data: ') ? trimmedLine.slice(6) : trimmedLine
      try {
        const data = JSON.parse(rawJson)
        const content = data.response || (data.msg && data.msg.content) || data.content || data.answer || ''
        if (content) fullText += content
      } catch (e) {
        // 忽略
      }
    }

    assistantMsg.isFinished = true
    assistantMsg.confirmed = true // 自动确认
    
    if (isLocalMode.value && currentSectionIndex !== -1) {
      documentData.value[currentSectionIndex].status = 'completed'
      
      // 强制触发响应式更新
      documentData.value = [...documentData.value]
      
      // 如果有结构化内容，自动同步到大纲并分割内容
      if (assistantMsg.content) {
        syncSubSectionsToOutline(activeSectionId.value, assistantMsg.content)
      }
    }

    await saveDocument()

  } catch (err) {
    console.error('生成失败:', err)
    message.error('生成失败，请重试')
    chatMessages.value.push({ role: 'assistant', content: '抱歉，生成过程中遇到了错误。' })
  } finally {
    isGenerating.value = false
    writingSectionId.value = null
  }
}

// 从 JSON 数据同步子章节
const syncSubSectionsFromJson = (parentId, sections) => {
  const parentIndex = documentData.value.findIndex(s => s.id === parentId)
  if (parentIndex === -1) return

  let hasChanges = false
  const normalizedParentTitle = getNormalizedTitle(documentData.value[parentIndex].title)
  
  const existingTitles = new Set(
    documentData.value.map(s => getNormalizedTitle(s.title))
  )

  // 过滤掉已经在目录中的章节（全局去重）
  const newSections = sections.filter(newSec => {
    const normalizedTitle = getNormalizedTitle(newSec.title)
    if (normalizedTitle === normalizedParentTitle) return false
    if (existingTitles.has(normalizedTitle)) return false
    existingTitles.add(normalizedTitle) // 防止传入的 sections 中有重复
    return true
  }).map((newSec, i) => {
    // 强制修正子章节编号
    const parentSection = documentData.value.find(s => s.id === parentId)
    const fixedTitle = parentSection 
      ? fixSectionTitleNumbering(parentSection.title, newSec.title, i)
      : newSec.title

    return {
      id: newSec.id || `${parentId}-sub-${Date.now()}-${i}`,
      parentId: parentId,
      title: fixedTitle,
      content: newSec.content || '',
      targetWords: newSec.targetWords || 500,
      status: 'pending'
    }
  })

  if (newSections.length > 0) {
    // 找到父章节及其所有现有子章节的最后位置
    const lastIndex = documentData.value.findLastIndex(s => s.parentId === parentId || s.id === parentId)
    documentData.value.splice(lastIndex + 1, 0, ...newSections)
    hasChanges = true
  }

  if (hasChanges) {
    documentData.value = [...documentData.value]
    saveDocument()
  }
}

// 实时同步子章节结构到大纲（不分割内容，仅更新目录树结构）
const syncSubSectionsToOutlineRealtime = (parentId, fullContent) => {
  // 匹配 Markdown 标题 ##, ### 等
  const headingRegex = /^(##+|###+)\s+(.+)$/gm
  const matches = [...fullContent.matchAll(headingRegex)]
  
  if (matches.length === 0) return

  const parentIndex = documentData.value.findIndex(s => s.id === parentId)
  if (parentIndex === -1) return

  const parent = documentData.value[parentIndex]
  const parentTitle = parent.title
  const parentLevel = parent.parentId ? 3 : 2
  let hasChanges = false
  
  // 获取全局已存在的标题（标准化后）
  const normalizedParentTitle = getNormalizedTitle(parentTitle)
  
  const existingTitles = new Set(
    documentData.value.map(s => getNormalizedTitle(s.title))
  )
  
  matches.forEach((match, i) => {
    const levelStr = match[1]
    const title = match[2].trim()
    const normalizedTitle = getNormalizedTitle(title)
    
    // 1. 检查标题级别：只有比父章节更深级别的标题才被视为子章节
    // 例如：父章节是 h2 (##)，则只有 ### 及以上才被视为子章节
    const matchLevel = levelStr.length
    if (matchLevel <= parentLevel) return
    
    // 2. 检查是否与父章节标题相同（忽略编号差异）
    const isSameAsParent = normalizedTitle === normalizedParentTitle
    
    // 3. 检查是否已存在同名章节（全局去重）
    const existsGlobally = existingTitles.has(normalizedTitle)
    
    if (!existsGlobally && !isSameAsParent) {
      // 找到父章节的所有子章节数量，用于生成正确索引
      const currentChildrenCount = documentData.value.filter(s => s.parentId === parentId).length
      const fixedTitle = fixSectionTitleNumbering(parentTitle, title, currentChildrenCount)

      const newSection = {
        id: `${parentId}-sub-tmp-${Date.now()}-${i}`,
        parentId: parentId,
        title: fixedTitle,
        content: '',
        targetWords: 500,
        status: 'pending'
      }
      
      // 找到插入位置：父章节之后，或者是最后一个已有的子章节之后
      const lastIndex = documentData.value.findLastIndex(s => s.parentId === parentId || s.id === parentId)
      documentData.value.splice(lastIndex + 1, 0, newSection)
      
      // 添加到已存在集合，防止同一次解析中产生重复
      existingTitles.add(normalizedTitle)
      hasChanges = true
    }
  })
  
  if (hasChanges) {
    documentData.value = [...documentData.value]
    saveDocument()
  }
}

const syncSubSectionsToOutline = (parentId, fullContent) => {
  const headingRegex = /^(##+|###+)\s+(.+)$/gm
  const matches = [...fullContent.matchAll(headingRegex)]
  
  if (matches.length === 0) return

  const parentIndex = documentData.value.findIndex(s => s.id === parentId)
  if (parentIndex === -1) return

  const parent = documentData.value[parentIndex]
  const parentTitle = parent.title
  const parentLevel = parent.parentId ? 3 : 2
  const normalizedParentTitle = getNormalizedTitle(parentTitle)
  
  // 1. 过滤掉级别不对或者重复的标题
  let actualMatches = matches.filter(match => {
    const levelStr = match[1]
    const title = match[2].trim()
    const normalizedTitle = getNormalizedTitle(title)
    
    // 只有比当前章节级别更深的才视为子章节
    const matchLevel = levelStr.length
    if (matchLevel <= parentLevel) return false
    
    // 不能与父章节标题相同
    if (normalizedTitle === normalizedParentTitle) return false
    
    return true
  })
  
  if (actualMatches.length === 0) {
    documentData.value[parentIndex].content = fullContent.trim()
    documentData.value = [...documentData.value]
    saveDocument()
    return
  }

  // 第一个有效子章节的位置，之前的都归属于父章节正文
  const firstHeadingIdx = actualMatches[0].index
  const parentContent = fullContent.substring(0, firstHeadingIdx).trim()
  documentData.value[parentIndex].content = parentContent

  // 2. 提取并准备新的子章节数据
  // 排除当前父章节及其子章节，获取全局其他标题
  const existingOtherTitles = new Set(
    documentData.value
      .filter(s => s.parentId !== parentId && s.id !== parentId)
      .map(s => getNormalizedTitle(s.title))
  )

  const newSubSections = []
  actualMatches.forEach((match, i) => {
    const title = match[2].trim()
    const normalizedTitle = getNormalizedTitle(title)
    
    // 如果该标题已经作为其他主章节或子章节存在，则跳过，避免重复
    if (existingOtherTitles.has(normalizedTitle)) {
      return
    }

    const startIdx = match.index + match[0].length
    const nextMatch = actualMatches[i + 1]
    const endIdx = nextMatch ? nextMatch.index : fullContent.length
    const content = fullContent.substring(startIdx, endIdx).trim()
    
    // 强制修正编号
    const fixedTitle = fixSectionTitleNumbering(parentTitle, title, newSubSections.length)

    newSubSections.push({
      id: `${parentId}-sub-${Date.now()}-${i}`,
      parentId: parentId,
      title: fixedTitle,
      content: content,
      targetWords: 500,
      status: 'completed'
    })
    
    // 同时也加入到已存在集合，防止本次循环中产生重复
    existingOtherTitles.add(normalizedTitle)
  })

  // 3. 插入到 documentData 中
  const filteredData = documentData.value.filter(s => s.parentId !== parentId)
  const newParentIdx = filteredData.findIndex(s => s.id === parentId)
  filteredData.splice(newParentIdx + 1, 0, ...newSubSections)
  
  documentData.value = filteredData
}

// === 集成测试脚本 ===
const isTesting = ref(false)
const runIntegrationTest = async () => {
  if (isTesting.value) return
  isTesting.value = true
  message.loading({ content: '正在启动集成测试...', key: 'test-msg' })

  try {
    // Step 1: 模拟大纲生成
    await generateOutline()
    await new Promise(r => setTimeout(r, 1000))
    
    // Step 2: 全局模式对话
    userInput.value = "请确保整体风格专业严谨"
    await sendMessage()
    await new Promise(r => setTimeout(r, 1000))

    // Step 3: 进入局部模式
    if (documentData.value.length > 0) {
      const firstId = documentData.value[0].id
      onTreeSelect([firstId])
      await new Promise(r => setTimeout(r, 1000))
      
      // Step 4: 局部模式扩写
      userInput.value = "请根据背景详细扩写本章节内容"
      await sendMessage()
      await new Promise(r => setTimeout(r, 1000))
      
      // Step 5: 退出局部模式
      activeSectionId.value = null
    }

    // Step 6: 模拟生成初稿
    await handleAction('draft')
    
    message.success({ content: '集成测试通过！交付物智能体核心流程验证成功。', key: 'test-msg', duration: 5 })
  } catch (err) {
    console.error('测试失败:', err)
    message.error({ content: '集成测试失败，请查看控制台。', key: 'test-msg' })
  } finally {
    isTesting.value = false
  }
}

const backToDetail = () => {
  const pathPrefix = isDemoMode.value ? '/project/demo' : '/project'
  router.push(`${pathPrefix}/${projectId}`)
}

// 分页逻辑
const contentViewRef = ref(null)
const pageCount = ref(1)
const PAGE_HEIGHT_PX = 1122.5 // A4 height
const PAGE_GAP_PX = 40 // 增加页面之间的间距，确保 ≥8px 的安全空间

// 自动调整跨页章节，防止标题被分割
const optimizePageBreaks = () => {
  if (!contentViewRef.value) return
  
  const sections = contentViewRef.value.querySelectorAll('.document-section')
  const styles = getComputedStyle(contentViewRef.value)
  const paddingTop = parseFloat(styles.paddingTop) || 25
  const paddingBottom = parseFloat(styles.paddingBottom) || 25
  
  let currentY = paddingTop 

  // 如果有主标题，计算其高度和边距
  const mainTitle = contentViewRef.value.querySelector('.document-main-title')
  if (mainTitle) {
    const titleStyles = getComputedStyle(mainTitle)
    const titleHeight = mainTitle.offsetHeight
    const titleMarginTop = parseFloat(titleStyles.marginTop) || 0
    const titleMarginBottom = parseFloat(titleStyles.marginBottom) || 0
    currentY += titleHeight + titleMarginTop + titleMarginBottom
  }
  
  sections.forEach((section) => {
    // 重置章节调整
    section.style.marginTop = '0px'
    
    // 重置章节内所有标题的调整
    const internalHeadings = section.querySelectorAll('.markdown-body h1, .markdown-body h2, .markdown-body h3, .section-title')
    internalHeadings.forEach(h => h.style.marginTop = '')

    // 获取当前章节的高度
    const sectionHeight = section.offsetHeight
    const sectionStyles = getComputedStyle(section)
    const sectionMarginBottom = parseFloat(sectionStyles.marginBottom) || 0
    
    // 计算当前章节在当前页可用空间中是否放得下
    const totalPageHeight = PAGE_HEIGHT_PX + PAGE_GAP_PX
    const positionInPageUnit = currentY % totalPageHeight
    const currentPageRemaining = PAGE_HEIGHT_PX - positionInPageUnit
    
    // 增加一个安全阈值，确保内容不紧贴分页线
    const SAFE_THRESHOLD = paddingBottom + 20 
    
    // 情况1：整个章节（包括其标题）在当前页放不下，或者标题本身就在页尾
    // 检查 section 的标题是否处于敏感区域
    const sectionTitle = section.querySelector('.section-title')
    let shouldMoveEntireSection = false
    
    if (sectionTitle) {
      const titleRect = sectionTitle.getBoundingClientRect()
      const containerRect = contentViewRef.value.getBoundingClientRect()
      const titleTopRelative = titleRect.top - containerRect.top
      const tPositionInPageUnit = titleTopRelative % totalPageHeight
      const tPageRemaining = PAGE_HEIGHT_PX - tPositionInPageUnit
      
      if (tPageRemaining < 60 || tPositionInPageUnit > PAGE_HEIGHT_PX) {
        shouldMoveEntireSection = true
      }
    }

    if (shouldMoveEntireSection || (sectionHeight > (currentPageRemaining - SAFE_THRESHOLD) && sectionHeight <= (PAGE_HEIGHT_PX - paddingTop - paddingBottom))) {
      const adjustment = currentPageRemaining + PAGE_GAP_PX + paddingTop
      section.style.marginTop = `${adjustment}px`
      currentY += adjustment
    } 
    // 情况2：章节跨页，检查内部 Markdown 标题
    else {
      internalHeadings.forEach((heading) => {
        // 如果是主标题且我们已经移动了整个 section，则跳过
        if (heading.classList.contains('section-title')) return

        const headingStyles = getComputedStyle(heading)
        const oldMarginTop = parseFloat(headingStyles.marginTop) || 0
        
        const headingRect = heading.getBoundingClientRect()
        const containerRect = contentViewRef.value.getBoundingClientRect()
        const headingTopRelative = headingRect.top - containerRect.top
        
        const hPositionInPageUnit = headingTopRelative % totalPageHeight
        const hPageRemaining = PAGE_HEIGHT_PX - hPositionInPageUnit
        
        if (hPageRemaining < 60 || hPositionInPageUnit > PAGE_HEIGHT_PX) { 
          const targetY = (Math.floor(headingTopRelative / totalPageHeight) + 1) * totalPageHeight + paddingTop
          const shift = targetY - headingTopRelative
          
          if (shift > 0) {
            heading.style.marginTop = `${oldMarginTop + shift}px`
          }
        }
      })
    }
    
    const finalSectionHeight = section.offsetHeight
    currentY += finalSectionHeight + sectionMarginBottom
  })
}

const updatePageCount = () => {
  if (!contentViewRef.value) return
  
  // 先进行分页优化，这会改变内容的实际高度
  optimizePageBreaks()
  
  // 使用 nextTick 确保 DOM 更新后测量高度
  nextTick(() => {
    const height = contentViewRef.value.offsetHeight
    // 总页数计算需要包含页面间的间距
    pageCount.value = Math.max(1, Math.ceil(height / (PAGE_HEIGHT_PX + PAGE_GAP_PX)))
  })
}

// 监听内容变化，更新页数
watch(() => documentData.value, () => {
  nextTick(updatePageCount)
}, { deep: true })

const resizeObserver = new ResizeObserver(() => {
  updatePageCount()
})

// 监听图片加载，图片加载会改变内容高度
const handleImageLoad = (e) => {
  if (contentViewRef.value && contentViewRef.value.contains(e.target)) {
    updatePageCount()
  }
}

onUnmounted(() => {
  if (contentViewRef.value) {
    resizeObserver.unobserve(contentViewRef.value)
    contentViewRef.value.removeEventListener('load', handleImageLoad, true)
  }
})

// 导出文档
const handleExport = async () => {
  if (isDemoMode.value) {
    message.info('演示模式暂不支持导出')
    return
  }

  // 内容完整性检查
  const hasContent = documentData.value.some(s => s.content && s.content.trim().length > 0)
  const hasOutline = documentData.value.length > 0
  
  if (!hasOutline) {
    message.warning('交付物尚未生成大纲，无法导出。')
    return
  }

  if (!hasContent) {
    Modal.confirm({
      title: '导出确认',
      content: '当前各章节正文内容均为空。是否仅导出目录标题？',
      okText: '确定导出',
      cancelText: '取消',
      onOk: () => executeExport()
    })
    return
  }

  executeExport()
}

const executeExport = async () => {
  try {
    message.loading({ content: '正在准备导出...', key: 'exporting' })
    
    // 先保存一次当前内容
    await saveDocument()
    
    const response = await projectApi.exportDeliverable(projectId, deliverableId)
    
    // 检查是否返回了错误 JSON (比如 400/404)
    if (response.headers.get('content-type')?.includes('application/json')) {
      const errorData = await response.json()
      throw new Error(errorData.detail || '导出失败')
    }

    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    
    const disposition = response.headers.get('Content-Disposition')
    let filename = `${deliverable.value.name}.docx`
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
    
    message.success({ content: `交付物 ${deliverable.value.name} 导出成功`, key: 'exporting' })
  } catch (err) {
    console.error('导出失败:', err)
    message.error({ content: '导出失败: ' + (err.message || '未知错误'), key: 'exporting' })
  }
}

onMounted(() => {
  fetchDeliverableInfo()
  if (contentViewRef.value) {
    resizeObserver.observe(contentViewRef.value)
    // 监听图片加载，图片加载会改变内容高度
    contentViewRef.value.addEventListener('load', handleImageLoad, true)
  }
  
  if (isDemoMode.value && deliverable.value.status === '已撰写') {
    const mockData = [
      { id: '1', title: '1. 项目背景与目标', content: '随着数字化浪潮的深入，本项目旨在通过引入AI驱动的决策支持系统，重塑现有的业务流程。目标是在六个月内实现核心环节的自动化率提升30%，并显著改善客户满意度。', targetWords: 1000, status: 'completed' },
      { id: '2', title: '2. 核心诊断发现', content: '经过对过去三年的历史数据分析，我们发现现有流程中存在以下瓶颈：\n- 数据孤岛现象严重，跨部门协作成本高昂。\n- 缺乏实时预测能力，导致资源分配滞后。\n- 传统人工审核环节平均耗时超过48小时。', targetWords: 1500, status: 'completed' },
      { id: '3', title: '3. 建议解决方案', content: '我们提议构建一个统一的数据湖平台，并部署以下模块：\n- 智能预测引擎：基于LSTM模型实现对市场需求的精准预判。\n- 自动化审批流：利用RPA技术处理80%的标准化业务。\n- 可视化仪表盘：为管理层提供实时、全方位的业务洞察。', targetWords: 2000, status: 'completed' }
    ]
    documentData.value = mockData
    chatMessages.value.push({ role: 'assistant', content: `我已经为您加载了“${deliverable.value.name}”的已撰写初稿。您可以点击右侧目录进行预览，或者在左侧输入要求让我进行微调。` })
  }
})

// 左右拖拽布局逻辑
const leftPanelWidth = ref(30) // 初始 30%
const isDragging = ref(false)
const containerRef = ref(null)
const isSidebarCollapsed = ref(false)

const validateFormat = (content) => {
  const issues = []
  if (!content.includes('# ')) issues.push('建议使用一级标题')
  if (content.length < 50) issues.push('内容可能过短')
  return issues
}

// 保存文档数据到数据库
const saveDocument = async () => {
  if (isDemoMode.value) return
  
  console.log('[Save] >>> 开始执行保存文档流程...')
  console.log('[Save] 当前章节数量:', documentData.value.length)
  
  try {
    // 执行格式校验
    documentData.value.forEach(section => {
      if (section.content) {
        const issues = validateFormat(section.content)
        if (issues.length > 0) {
          console.log(`[Save] 章节 "${section.title}" 格式提醒:`, issues.join(', '))
        }
      }
    })

    const updateData = {
      metadata: {
        outline: documentData.value.map(s => ({
          id: s.id,
          title: s.title,
          targetWords: s.targetWords,
          status: s.status,
          parentId: s.parentId || null,
          contentLength: s.content ? s.content.length : 0 // 添加长度便于调试
        })),
        thread_id: currentThreadId.value
      },
      // 将所有章节内容合并存储到 content 字段中，以便后续导出或预览
      content: documentData.value.map(s => {
        const sectionContent = s.content || ''
        console.log(`[Save] 章节: ${s.title}, 状态: ${s.status}, 完整内容:\n${sectionContent}\n[End of Section]`)
        // 根据章节层级动态生成 Markdown 标题级别 (一级章节 ##, 二级章节 ###, 依此类推)
        const level = getSectionLevel(s)
        const prefix = '#'.repeat(level)
        return `${prefix} ${s.title}\n\n${sectionContent}`
      }).join('\n\n'),
      status: documentData.value.every(s => s.status === 'completed') ? '已撰写' : '撰写中'
    }
    
    console.log('[Save] 准备发送更新请求到后端 API...')
    console.log('[Save] 更新数据概览:', JSON.stringify({
      outlineLength: updateData.metadata.outline.length,
      totalContentLength: updateData.content.length,
      status: updateData.status,
      thread_id: updateData.metadata.thread_id,
      sectionsWithContent: updateData.metadata.outline.filter(s => s.contentLength > 0).map(s => s.title)
    }, null, 2))

    const res = await projectApi.updateDeliverable(projectId, deliverableId, updateData)
    if (res && (res.success || res.code === 200 || res.code === 0)) {
      console.log('✅ [Save] 文档数据已成功保存到数据库')
    } else {
      console.warn('⚠️ [Save] 文档数据保存可能未完全成功:', res)
    }
  } catch (err) {
    console.error('❌ [Save] 自动保存失败，发生错误:', err)
  }
}

const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

const startResizing = () => {
  isDragging.value = true
  document.addEventListener('mousemove', handleMouseMove)
  document.addEventListener('mouseup', stopResizing)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

const handleMouseMove = (event) => {
  if (!isDragging.value || !containerRef.value) return
  
  const containerWidth = containerRef.value.offsetWidth
  const newLeftWidth = (event.clientX / containerWidth) * 100
  
  // 限制拖拽范围在 15% 到 60% 之间
  if (newLeftWidth >= 15 && newLeftWidth <= 60) {
    leftPanelWidth.value = newLeftWidth
  }
}

const stopResizing = () => {
  isDragging.value = false
  document.removeEventListener('mousemove', handleMouseMove)
  document.removeEventListener('mouseup', stopResizing)
  document.body.style.cursor = 'default'
  document.body.style.userSelect = 'auto'
}
</script>

<template>
  <div class="generator-page">
    <!-- 顶部导航栏 -->
    <header class="top-bar">
      <div class="bar-left">
        <button class="back-btn" @click="backToDetail">
          <ChevronLeft :size="20" />
          <span>返回项目</span>
        </button>
        <div class="divider"></div>
        <div class="doc-info">
          <FileText :size="18" class="doc-icon" />
          <span class="doc-name">{{ deliverable.name }}</span>
          <span class="status-badge">{{ deliverable.status }}</span>
        </div>
      </div>
      
      <div class="bar-right">
        <button class="action-btn test-btn" @click="runIntegrationTest" :disabled="isTesting" v-if="isDemoMode">
          <PlayCircle :size="18" />
          <span>集成测试</span>
        </button>
        <button
          class="action-btn primary"
          @click="handleExport"
          :disabled="!deliverable.can_download || isGenerating"
          :title="isGenerating ? 'AI 生成中，无法导出' : (deliverable.can_download ? '导出 Word' : '未撰写，无法导出')"
        >
          <Download :size="18" />
          <span>导出 Word</span>
        </button>
      </div>
    </header>

    <main class="content-layout" ref="containerRef" :class="{ 'is-resizing': isDragging }">
      <!-- 左侧：AI 对话区 (30%) -->
      <section class="chat-section" :style="{ width: leftPanelWidth + '%' }">
        <div class="chat-history">
          <div class="chat-welcome">
            <Sparkles :size="32" class="welcome-icon" />
            <h3>AI 助手</h3>
            <p>我是您的专业撰写助手，可以帮您撰写、修改和优化项目交付物。</p>
          </div>
          
          <div v-for="(msg, index) in chatMessages" :key="index" class="message-item" :class="msg.role">
            <div class="message-content" :class="{ 'has-thinking': msg.thinking }">
              <!-- 深度思考过程 -->
              <div v-if="msg.thinking" class="thinking-container">
                <div class="thinking-header" @click="msg.showThinking = !msg.showThinking">
                  <div class="thinking-title">
                    <Loader2 v-if="msg.isThinking" class="animate-spin" :size="14" />
                    <CheckCircle2 v-else :size="14" class="text-success" />
                    <span>深度思考过程</span>
                  </div>
                  <ChevronLeft :size="14" :class="{ 'rotate-down': msg.showThinking }" class="collapse-icon" />
                </div>
                <Transition name="fade">
                  <div v-if="msg.showThinking || msg.isThinking" class="thinking-body markdown-body" v-html="renderMarkdown(msg.thinking)"></div>
                </Transition>
              </div>

              <!-- 摘要内容 -->
              <div v-if="msg.summary" class="summary-box">
                <div class="summary-tag">摘要</div>
                <div class="summary-text">{{ msg.summary }}</div>
              </div>

              <!-- 进度展示 -->
              <div v-if="msg.type === 'progress'" class="progress-container">
                <div class="progress-info">
                  <span class="progress-label">{{ msg.label }}</span>
                  <span class="progress-percentage">{{ Math.round(msg.current / msg.total * 100) }}%</span>
                </div>
                <div class="progress-bar-wrapper">
                  <div class="progress-bar-bg">
                    <div 
                      class="progress-bar-fill" 
                      :style="{ width: (msg.current / msg.total * 100) + '%' }"
                    ></div>
                  </div>
                </div>
                <div class="progress-status">
                  <span>已完成: {{ msg.current }}/{{ msg.total }}</span>
                  <span v-if="msg.current === msg.total" class="completion-tag">{{ msg.content }}</span>
                  <span v-else class="status-text">{{ msg.content }}</span>
                </div>
              </div>

              <!-- 正文内容 (左侧仅展示提示或预览) -->
              <div v-if="msg.content && msg.type !== 'progress'" class="main-content-preview">
                <div v-if="!msg.thinking && !msg.summary" v-html="renderMarkdown(msg.content)"></div>
                <div v-else class="content-synced-tip">
                  <FileText :size="14" />
                  <span>正文内容已同步至右侧文档区域</span>
                </div>
              </div>

              <!-- 质量自检提示 -->
              <div v-if="msg.qualityCheck" class="quality-check-area">
                <div class="check-tip">
                  <AlertCircle :size="14" />
                  <span>{{ msg.qualityCheck }}</span>
                </div>
              </div>

              <div v-if="msg.isPlanning" class="planning-status">
                <div class="breathing-light"></div>
                <span>正在规划大纲...</span>
              </div>
            </div>
          </div>

          <div v-if="isGenerating && !writingSectionId" class="message-item assistant loading">
            <div class="message-content">
              <Loader2 class="animate-spin" :size="16" />
              <span>正在生成中...</span>
            </div>
          </div>
        </div>

        <div class="chat-input-container">
          <div class="mode-indicator" v-if="isLocalMode">
            <Target :size="14" />
            <span>当前聚焦: {{ documentData.find(s => s.id === activeSectionId)?.title }}</span>
            <button class="exit-mode" @click="activeSectionId = null">退出局部模式</button>
          </div>
          <div class="quick-actions">
            <button
              v-for="action in actions"
              :key="action.id"
              class="quick-action-btn"
              @click="action.id === 'outline' ? handleGenerateOutline() : handleAction(action.id)"
              :disabled="isActionDisabled(action.id)"
              :style="{ '--action-color': action.color }"
              :title="isActionDisabled(action.id) ? getActionDisabledTooltip(action.id) : action.label"
            >
              <component :is="action.icon" :size="14" :style="{ color: action.color }" />
              <span>{{ action.label }}</span>
            </button>
          </div>
          
          <div class="input-wrapper">
            <textarea 
              v-model="userInput" 
              placeholder="在此输入您的需求..." 
              @keydown.enter.prevent="sendMessage"
            ></textarea>
            <div class="input-actions">
              <label class="upload-icon-btn" title="上传文档">
                <input type="file" hidden @change="handleFileUpload" accept=".txt,.doc,.docx" />
                <FileUp :size="18" />
              </label>
              <button class="send-button" @click="sendMessage" :disabled="!userInput.trim() || isGenerating">
                <Send :size="18" />
              </button>
            </div>
          </div>
        </div>
      </section>

      <!-- 可拖动分隔条 -->
      <div class="resize-handle" @mousedown="startResizing">
        <div class="handle-line"></div>
      </div>

      <!-- 右侧：文档预览区 (70%) -->
      <section class="preview-section" :style="{ width: (100 - leftPanelWidth) + '%' }">
        <div class="preview-layout" :class="{ 'sidebar-collapsed': isSidebarCollapsed }">
          <!-- 左侧目录树 -->
          <div class="document-sidebar">
            <div class="sidebar-header">
              <div class="header-main" v-if="!isSidebarCollapsed">
                <ListTree :size="16" />
                <span>章节导航</span>
              </div>
              <button class="collapse-toggle" @click="toggleSidebar" :title="isSidebarCollapsed ? '展开目录' : '收起目录'">
                <ChevronLeftSquare :size="18" v-if="!isSidebarCollapsed" />
                <ChevronRightSquare :size="18" v-else />
              </button>
            </div>
            <div class="tree-container" v-show="!isSidebarCollapsed">
              <ATree
                :tree-data="treeData"
                :selected-keys="activeSectionId ? [activeSectionId] : []"
                :expanded-keys="expandedKeys"
                @select="onTreeSelect"
                @expand="onTreeExpand"
                block-node
                class="outline-tree"
              >
                <!-- 自定义展开/收起图标 -->
                <template #switcherIcon="{ expanded, isLeaf }">
                  <span v-if="!isLeaf" class="switcher-icon">
                    {{ expanded ? '▼' : '▶' }}
                  </span>
                </template>

                <template #title="{ title, key, targetWords }">
                  <div 
                    class="tree-node-title" 
                    :class="{ 'is-active': activeSectionId === key }"
                    @click="handleNodeClick($event, key, title)"
                  >
                    <ATooltip placement="right">
                      <template #title>{{ title }}</template>
                      <span class="node-text">{{ title }}</span>
                    </ATooltip>
                    <div class="node-meta">
                      <span class="node-words" :title="`${targetWords}字`">{{ targetWords }}字</span>
                      <div v-if="writingSectionId === key" class="writing-indicator">
                        <div class="breathing-dot"></div>
                      </div>
                    </div>
                  </div>
                </template>
              </ATree>
            </div>
          </div>

          <!-- 右侧文档内容 -->
          <div class="document-editor-container">
            <div class="document-scroller">
              <!-- 文档外层 -->
              <div class="document-pages-container" :style="{ height: (pageCount * (PAGE_HEIGHT_PX + PAGE_GAP_PX)) + 'px' }">
                <!-- 动态分页辅助线和页码 -->
                <div class="document-pagination-layer">
                  <!-- 物理页面背景，模拟页面之间的隔开效果 -->
                  <div 
                    v-for="page in pageCount" 
                    :key="`bg-${page}`" 
                    class="physical-page-bg"
                    :style="{ 
                      top: ((page - 1) * (PAGE_HEIGHT_PX + PAGE_GAP_PX)) + 'px',
                      height: PAGE_HEIGHT_PX + 'px'
                    }"
                  ></div>

                  <div 
                    v-for="page in pageCount" 
                    :key="page" 
                    class="page-break-line"
                    :style="{ top: (page * PAGE_HEIGHT_PX + (page - 1) * PAGE_GAP_PX) + 'px' }"
                    v-show="page < pageCount"
                  >
                    <div class="page-number">第 {{ page }} 页 / 共 {{ pageCount }} 页</div>
                  </div>
                  <!-- 最后一页的页码显示在页面底部，与页面保持一定距离 -->
                  <div 
                    class="page-break-line last-page-info"
                    :style="{ top: (pageCount * PAGE_HEIGHT_PX + (pageCount - 1) * PAGE_GAP_PX) + 'px' }"
                  >
                    <div class="page-number">第 {{ pageCount }} 页 / 共 {{ pageCount }} 页</div>
                  </div>
                </div>

                <!-- 内容层 -->
                <div class="document-content-layer">
                  <!-- 模拟 Word 文档内容展示 -->
                  <div class="document-content-view" ref="contentViewRef">
                    <!-- 交付物主标题 -->
                    <h1 class="document-main-title">{{ deliverable.name }}</h1>
                    
                    <div v-if="documentData.length === 0" class="empty-state">
                      <Sparkles :size="48" class="empty-icon" />
                      <p>智能体已就绪，正在等待您的指令</p>
                      <span class="empty-tip">点击左侧“生成大纲”开启智写之旅</span>
                    </div>
                    <div v-else class="sections-list">
                      <div 
                        v-for="section in documentData" 
                        :key="section.id" 
                        :id="`section-${section.id}`"
                        class="document-section"
                        :class="{ 
                          'is-active': activeSectionId === section.id,
                          'is-writing': writingSectionId === section.id,
                          'is-sub-section': section.parentId
                        }"
                        @click="activeSectionId = section.id"
                      >
                        <component 
                          :is="`h${getSectionLevel(section)}`" 
                          class="section-title"
                        >
                          {{ section.title }}
                        </component>
                        <div class="section-content-wrapper">
                          <div v-if="writingSectionId === section.id && !section.content" class="writing-placeholder">
                            <div class="writing-text">正在为您撰写中...</div>
                            <div class="breathing-bar"></div>
                          </div>
                          <div 
                            v-else-if="section.content" 
                            class="content-text markdown-body" 
                            v-html="renderMarkdown(section.content, section.title)"
                          ></div>
                          <div v-else class="empty-section-placeholder">&nbsp;</div>
                          
                          <!-- 写作中的闪烁效果 -->
                          <div v-if="writingSectionId === section.id" class="writing-overlay"></div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>

    <!-- Toast 提示 -->
    <Transition name="toast">
      <div v-if="showToast" class="toast-overlay" :class="toastType">
        <CheckCircle2 v-if="toastType === 'success'" :size="18" />
        <AlertCircle v-else :size="18" />
        <span>{{ toastMsg }}</span>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* 打印样式，隐藏页码 */
@media print {
  @page {
    margin: 20mm; /* 标准打印边距 */
    size: A4;
  }
  
  body {
    background: white !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }

  .generator-page {
    position: static !important;
    height: auto !important;
    overflow: visible !important;
  }

  /* 隐藏所有 UI 元素，只保留文档内容 */
  .top-bar,
  .action-sidebar,
  .left-panel,
  .drag-handle,
  .document-pagination-layer,
  .page-number,
  .toast-overlay,
  .exit-mode,
  .writing-overlay,
  .writing-indicator {
    display: none !important;
  }

  .document-editor-container,
  .document-scroller,
  .document-pages-container,
  .document-content-layer {
    display: block !important;
    width: 100% !important;
    height: auto !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: visible !important;
    box-shadow: none !important;
    background: transparent !important;
    border: none !important;
  }

  .document-content-view {
    padding: 0 !important; /* 由 @page margin 控制 */
    width: 100% !important;
  }

  .document-section {
    page-break-inside: avoid; /* 尽量避免在章节内部分页 */
    break-inside: avoid;
    margin-top: 0 !important; /* 移除预览时的 marginTop 调整 */
  }

  .markdown-body {
    font-size: 12pt !important; /* 打印时稍微调大字号 */
    color: black !important;
  }
}

.generator-page {
  height: 100vh;
  width: 100vw;
  background-color: #f0f2f5;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: fixed;
  top: 0;
  left: 0;
}

/* Header */
.top-bar {
  height: 60px;
  background: white;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
  z-index: 10;
}

.bar-left {
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

.doc-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.doc-icon {
  color: #86BC25;
}

.doc-name {
  font-size: 15px;
  font-weight: 600;
  color: #1a1a1a;
}

.status-badge {
  font-size: 11px;
  background: #f0f9eb;
  color: #86BC25;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}

.bar-right {
  display: flex;
  gap: 12px;
}

.action-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s;
  border: 1px solid transparent;
}

.action-btn.primary {
  background: #86BC25;
  color: white;
}

.action-btn.primary:hover {
  background: #75a620;
}

.action-btn.test-btn {
  background: #f0f5ff;
  border-color: #adc6ff;
  color: #2f54eb;
}

.action-btn.test-btn:hover {
  background: #d6e4ff;
  border-color: #85a5ff;
}

.action-btn.test-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* Layout */
.content-layout {
  flex: 1;
  display: flex;
  overflow: hidden;
  background: #f8f9fa;
  position: relative;
}

/* Chat Section */
.chat-section {
  border-right: none;
  display: flex;
  flex-direction: column;
  background: white;
  min-width: 250px;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.is-resizing .chat-section,
.is-resizing .preview-section {
  transition: none !important;
}

.chat-history {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.chat-welcome {
  text-align: center;
  padding: 40px 20px;
  color: #8c8c8c;
}

.welcome-icon {
  color: #86BC25;
  margin-bottom: 16px;
}

.chat-welcome h3 {
  color: #262626;
  margin-bottom: 8px;
}

/* 消息项基础样式 */
.message-item {
  display: flex;
  flex-direction: column;
  max-width: 88%;
  position: relative;
  transition: all 0.3s ease;
}

.message-item.user {
  align-self: flex-end;
}

.message-item.assistant {
  align-self: flex-start;
}

/* 气泡内容基础样式 */
.message-content {
  font-size: 14px;
  line-height: 1.6;
  position: relative;
}

/* 用户消息气泡 */
.user .message-content {
  background: #86BC25;
  color: white;
  padding: 10px 16px;
  border-radius: 16px 16px 2px 16px;
  box-shadow: 0 2px 6px rgba(134, 188, 37, 0.15);
}

/* 助手消息气泡 - 结构化容器 */
.assistant .message-content {
  background: white;
  color: #262626;
  border: 1px solid #f0f0f0;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
  border-radius: 16px 16px 16px 2px;
  width: 100%;
  padding: 0;
  overflow: hidden;
}

/* 助手消息内部内容边距控制 */
.assistant .message-content > div:not(.thinking-container):not(.summary-box):not(.quality-check-area) {
  padding-left: 16px;
  padding-right: 16px;
}

.assistant .message-content > div:first-child:not(.thinking-container):not(.summary-box) {
  padding-top: 14px;
}

.assistant .message-content > div:last-child {
  padding-bottom: 14px;
}

/* 特殊区域背景与边距 */
.thinking-container {
  background: #f8f9fa;
  border-bottom: 1px solid #f0f0f0;
}

.thinking-header {
  padding: 10px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s;
}

.thinking-header:hover {
  background: #f0f2f5;
}

.thinking-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: #8c8c8c;
}

.collapse-icon {
  transition: transform 0.3s;
  color: #bfbfbf;
}

.rotate-down {
  transform: rotate(-90deg);
}

.thinking-body {
  padding: 12px 16px;
  font-size: 13px;
  color: #595959;
  border-top: 1px solid #f0f0f0;
  background: #ffffff;
  max-height: 240px;
  overflow-y: auto;
}

/* 自定义滚动条 */
.thinking-body::-webkit-scrollbar {
  width: 4px;
}

.thinking-body::-webkit-scrollbar-track {
  background: transparent;
}

.thinking-body::-webkit-scrollbar-thumb {
  background: #e8e8e8;
  border-radius: 2px;
}

.thinking-body::-webkit-scrollbar-thumb:hover {
  background: #d9d9d9;
}

/* 摘要区域样式优化 */
.summary-box {
  padding: 14px 16px;
  background: rgba(134, 188, 37, 0.04);
  border-left: 3px solid #86BC25;
  margin: 12px 16px;
  border-radius: 4px;
}

.summary-tag {
  font-size: 11px;
  font-weight: 600;
  color: #86BC25;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.summary-text {
  font-size: 13px;
  line-height: 1.6;
  color: #434343;
}

/* 进度展示样式 */
.progress-container {
  padding: 16px;
  background: #fff;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.progress-label {
  font-size: 14px;
  font-weight: 500;
  color: #262626;
}

.progress-percentage {
  font-size: 14px;
  font-weight: 600;
  color: #86BC25;
}

.progress-bar-wrapper {
  margin-bottom: 12px;
}

.progress-bar-bg {
  height: 8px;
  background: #f0f0f0;
  border-radius: 4px;
  overflow: hidden;
}

.progress-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #86BC25, #a3d945);
  border-radius: 4px;
  transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 1px 3px rgba(134, 188, 37, 0.2);
}

.progress-status {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #8c8c8c;
}

.completion-tag {
  color: #52c41a;
  font-weight: 500;
  animation: fadeIn 0.5s ease;
}

.status-text {
  color: #8c8c8c;
  font-style: italic;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 正文实时同步状态提示 */
.main-content-preview {
  margin: 4px 0;
}

.content-synced-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: #f9f9f9;
  border: 1px dashed #e8e8e8;
  border-radius: 8px;
  color: #8c8c8c;
  font-size: 13px;
}

/* 质量检查区域 */
.quality-check-area {
  padding: 14px 16px;
  background: #fffdf0;
  border-top: 1px solid #fff1b8;
}

.check-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #d48806;
  margin-bottom: 12px;
}

.action-footer {
  display: flex;
  gap: 8px;
}

.msg-action-btn {
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  border: 1px solid transparent;
  font-weight: 500;
}

.msg-action-btn.confirm {
  background: #86BC25;
  color: white;
}

.msg-action-btn.confirm:hover {
  background: #75a620;
  box-shadow: 0 2px 4px rgba(134, 188, 37, 0.2);
}

.msg-action-btn.retry {
  background: white;
  border-color: #d9d9d9;
  color: #595959;
}

.msg-action-btn.retry:hover {
  border-color: #86BC25;
  color: #86BC25;
  background: #fafff0;
}

.confirmed-status {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #52c41a;
  font-size: 13px;
  font-weight: 500;
}

/* Word 样式标准 */
.markdown-body :deep(h1) {
  font-size: 24pt;
  font-weight: bold;
  color: #000000;
  margin-top: 24pt;
  margin-bottom: 12pt;
  line-height: 1.2;
}

.markdown-body :deep(h2) {
  font-size: 18pt;
  font-weight: bold;
  color: #000000;
  margin-top: 18pt;
  margin-bottom: 10pt;
  line-height: 1.2;
}

.markdown-body :deep(h3) {
  font-size: 14pt;
  font-weight: bold;
  color: #000000;
  margin-top: 14pt;
  margin-bottom: 8pt;
  line-height: 1.2;
}

.markdown-body :deep(p) {
  font-size: 12pt;
  line-height: 1.5;
  margin-bottom: 12pt;
  color: #333333;
  text-align: justify;
  /* 防止行被分割 */
  orphans: 3;
  widows: 3;
}

.markdown-body :deep(ul), .markdown-body :deep(ol) {
  padding-left: 24pt;
  margin-bottom: 12pt;
}

.markdown-body :deep(li) {
  font-size: 12pt;
  line-height: 1.5;
  margin-bottom: 6pt;
}

/* 规划大纲状态 */
.planning-status {
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  color: #86BC25;
  font-size: 13px;
  font-weight: 500;
  background: rgba(134, 188, 37, 0.05);
  border-radius: 8px;
  margin-top: 8px;
}

.breathing-light {
  width: 8px;
  height: 8px;
  background: #86BC25;
  border-radius: 50%;
  box-shadow: 0 0 8px rgba(134, 188, 37, 0.4);
  animation: breathe 2s infinite;
}

@keyframes breathe {
  0% { transform: scale(0.8); opacity: 0.5; }
  50% { transform: scale(1.2); opacity: 1; box-shadow: 0 0 12px rgba(134, 188, 37, 0.6); }
  100% { transform: scale(0.8); opacity: 0.5; }
}

.mode-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #fff7e6;
  border: 1px solid #ffd591;
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 12px;
  color: #d48806;
}

.exit-mode {
  margin-left: auto;
  background: none;
  border: none;
  color: #1890ff;
  cursor: pointer;
  font-weight: 500;
}

.exit-mode:hover {
  text-decoration: underline;
}

.text-success {
  color: #52c41a;
}

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.3s;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}

/* Loading 状态特殊处理 */
.message-item.loading .message-content {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: #86BC25;
  background: white;
  padding: 10px 20px;
  border-radius: 16px 16px 16px 2px;
  border: 1px solid #f0f0f0;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
  font-weight: 500;
}

/* 打字机光标动画 */
@keyframes blink {
  50% { opacity: 0; }
}

.writing-dot {
  width: 6px;
  height: 6px;
  background-color: #86BC25;
  border-radius: 50%;
  animation: blink 1s infinite;
}

/* Chat Input */
.chat-input-container {
  padding: 20px;
  border-top: 1px solid #f0f0f0;
  background: white;
}

.quick-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.quick-action-btn {
  white-space: nowrap;
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid #e8e8e8;
  background: white;
  font-size: 12px;
  color: #595959;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  transition: all 0.2s;
}

.quick-action-btn:hover {
  border-color: var(--action-color, #86BC25);
  color: var(--action-color, #86BC25);
  background: rgba(134, 188, 37, 0.05);
}

.input-wrapper {
  position: relative;
  background: #f5f5f5;
  border-radius: 12px;
  padding: 12px;
  border: 1px solid transparent;
  transition: all 0.2s;
}

.input-wrapper:focus-within {
  background: white;
  border-color: #86BC25;
  box-shadow: 0 0 0 2px rgba(134, 188, 37, 0.1);
}

.input-wrapper textarea {
  width: 100%;
  height: 80px;
  border: none;
  background: transparent;
  resize: none;
  outline: none;
  font-size: 14px;
  color: #262626;
  padding-bottom: 40px;
}

.input-actions {
  position: absolute;
  right: 12px;
  bottom: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.upload-icon-btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #8c8c8c;
  cursor: pointer;
  transition: all 0.2s;
}

.upload-icon-btn:hover {
  color: #86BC25;
  background: rgba(134, 188, 37, 0.05);
}

.send-button {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: none;
  background: #86BC25;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
}

.send-button:disabled {
  background: #d9d9d9;
  cursor: not-allowed;
}

/* Resize Handle */
.resize-handle {
  width: 12px; /* 稍微加宽触达区域 */
  cursor: col-resize;
  background: #f0f0f0; /* 默认给一个极浅的背景色 */
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  position: relative;
  z-index: 10;
  margin-left: -6px;
  margin-right: -6px;
  border-left: 1px solid #e8e8e8;
  border-right: 1px solid #e8e8e8;
}

.resize-handle:hover,
.is-resizing .resize-handle {
  background: rgba(134, 188, 37, 0.08);
  border-left-color: rgba(134, 188, 37, 0.2);
  border-right-color: rgba(134, 188, 37, 0.2);
}

.handle-line {
  width: 2px;
  height: 32px; /* 默认显示一个小滑块 */
  background: #d9d9d9;
  border-radius: 1px;
  transition: all 0.2s;
}

.resize-handle:hover .handle-line,
.is-resizing .handle-line {
  background: #86BC25;
  height: 100%; /* 悬浮或拖拽时变为全高，增强反馈 */
}

/* Preview Section */
.preview-section {
  display: flex;
  flex-direction: column;
  padding: 0;
  background: #f0f2f5;
  overflow: hidden;
  min-width: 600px;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.preview-layout {
  display: flex;
  height: 100%;
  width: 100%;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.preview-layout.sidebar-collapsed .document-sidebar {
  width: 40px;
  min-width: 40px;
  padding: 8px 0;
}

.preview-layout.sidebar-collapsed .document-sidebar .sidebar-header {
  padding: 0;
  justify-content: center;
}

/* Sidebar */
.document-sidebar {
  width: 240px;
  background: white;
  border-right: 1px solid #e8e8e8;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}

.sidebar-header {
  height: 48px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  border-bottom: 1px solid #f0f0f0;
  font-weight: 600;
  color: #262626;
  font-size: 14px;
  justify-content: space-between;
}

.header-main {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.collapse-toggle {
  background: none;
  border: none;
  padding: 4px;
  cursor: pointer;
  color: #9ca3af;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: all 0.2s;
}

.collapse-toggle:hover {
  background: #f3f4f6;
  color: #86BC25;
}

.tree-container {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden; /* 严禁左右滑动 */
  padding: 8px 0;
}

.tree-container :deep(.ant-tree) {
  width: 100%;
}

.tree-container :deep(.ant-tree-list-holder-inner) {
  width: 100% !important;
}

.tree-container :deep(.ant-tree-treenode) {
  width: 100%;
  display: flex;
  align-items: center;
}

.tree-container :deep(.ant-tree-node-content-wrapper) {
  flex: 1;
  min-width: 0;
  display: flex;
  padding: 0;
}

.tree-container :deep(.ant-tree-title) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.tree-node-title {
  display: flex !important;
  align-items: center;
  width: 100%;
  overflow: hidden;
  position: relative;
  padding-right: 60px;
  height: 32px;
  cursor: pointer;
}

.switcher-icon {
  font-size: 10px;
  color: #8c8c8c;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  margin-right: 4px;
  transition: transform 0.2s;
}

.outline-tree :deep(.ant-tree-switcher) {
  width: 14px !important;
  display: flex;
  align-items: center;
  justify-content: center;
}

.outline-tree :deep(.ant-tree-switcher_open .switcher-icon),
.outline-tree :deep(.ant-tree-switcher_close .switcher-icon) {
  transform: none; /* 我们自己控制图标，不需要 ant-design 的旋转 */
}

.outline-tree :deep(.ant-tree-indent-unit) {
  width: 12px !important;
}

.node-text {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
  color: #262626;
  line-height: 32px;
}

.node-meta {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  gap: 4px;
  width: 54px;
  justify-content: flex-end;
  background: transparent;
  pointer-events: none; /* 防止遮挡下方点击 */
}

/* 激活状态和悬浮状态的样式优化 */
.tree-node-title.is-active {
  color: #86BC25;
  font-weight: 500;
}

.tree-node-title.is-active .node-text {
  color: #86BC25;
}

.tree-container :deep(.ant-tree-node-content-wrapper:hover) {
  background-color: #f6ffed !important;
}

.tree-container :deep(.ant-tree-node-selected) {
  background-color: #f6ffed !important;
}

.node-words {
  font-size: 11px;
  color: #8c8c8c;
  white-space: nowrap;
  display: inline-block;
  text-align: right;
}

/* Editor Container */
.document-editor-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #f3f4f6;
  overflow: hidden;
  position: relative;
}

  .document-scroller {
    flex: 1;
    overflow-y: auto;
    padding: 60px 0;
    display: flex;
    justify-content: center;
    background: #f0f2f5;
    scroll-behavior: smooth;
  }

  .document-pages-container {
    position: relative;
    width: 210mm;
    flex-shrink: 0;
    margin: 0 auto;
    background: transparent;
    box-shadow: none;
    transition: height 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  }

  /* 分页层样式 */
  .document-pagination-layer {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    z-index: 1;
  }

  /* 模拟物理页面，产生页面之间的缝隙感 */
  .physical-page-bg {
    position: absolute;
    left: 0;
    width: 100%;
    height: 297mm;
    background: #ffffff;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.05), 0 1px 8px rgba(0, 0, 0, 0.03);
    border: 1px solid #e2e8f0;
    z-index: -1;
    border-radius: 2px;
  }

  .page-break-line {
    position: absolute;
    left: -60px;
    right: -60px;
    width: calc(100% + 120px);
    height: 40px; /* 必须与 PAGE_GAP_PX 一致 */
    margin-top: 0;
    display: flex;
    justify-content: center;
    align-items: center;
    z-index: 3;
    background: #f0f2f5;
  }

  .page-break-line::before {
    content: "";
    position: absolute;
    left: 0;
    right: 0;
    height: 1px;
    border-top: 1px dashed #cbd5e1;
    top: 50%;
    transform: translateY(-50%);
  }

  .page-break-line.last-page-info {
    /* 最后一页的页码样式与普通分页线保持一致，确保有背景色形成的距离感 */
  }

  .page-number {
    background: #ffffff;
    padding: 6px 16px;
    font-size: 12px;
    font-weight: 500;
    color: #64748b;
    border-radius: 20px;
    border: 1px solid #e2e8f0;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    position: relative;
    z-index: 4;
  }

  /* 内容层 */
  .document-content-layer {
    position: relative;
    width: 100%;
    z-index: 2;
  }

  .document-content-view {
    padding: 25px 25mm; /* 上下内边距改为 25px (对应页眉页脚间距要求)，左右保持 25mm */
    min-height: 297mm;
    box-sizing: border-box;
  }

  .document-main-title {
    font-size: 24pt;
    font-weight: 800;
    text-align: center;
    margin-top: 50pt;
    margin-bottom: 60pt; /* 缩小主标题下间距，从 60pt 改为 40pt */
    color: #1a1a1a;
    line-height: 1.2;
    word-break: break-word;
  }

/* Sections */
.sections-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.document-section {
  padding: 0; /* 移除 padding，改用 margin 控制间距 */
  margin-bottom: 24px; /* 章节间距，保持紧凑 */
  border-radius: 0;
  border-bottom: 1px solid transparent;
  transition: background 0.2s;
  cursor: pointer;
  position: relative;
}

.document-section:last-child {
  margin-bottom: 0;
}

.document-section:hover {
  background: rgba(134, 188, 37, 0.01);
}

.document-section.is-active {
  background: rgba(134, 188, 37, 0.03);
  border-left: 3px solid #86BC25;
  padding-left: 20px;
  margin-left: -23px;
  padding-top: 8px; /* 激活状态稍微增加内边距 */
  padding-bottom: 8px;
}

.document-section.is-sub-section {
  padding-left: 32px;
  border-left: 1px dashed #e8e8e8;
  margin-left: 16px;
}

.document-section.is-sub-section .section-title {
  font-size: 18px;
  color: #434343;
}

.section-title {
  font-size: 22px;
  font-weight: 700;
  margin-top: 0; /* 确保顶部对齐 */
  margin-bottom: 18px; /* 标题与正文间距，建议 15-20px */
  color: #1a1a1a;
  line-height: 1.4;
}

.section-content-wrapper {
  position: relative;
  min-height: 40px;
  font-size: 11pt;
  line-height: 1.8;
  color: #333;
}

.empty-section-placeholder {
  min-height: 1.5em;
  width: 100%;
}

.empty-section-tip {
  color: #bfbfbf;
  font-style: italic;
  font-size: 13px;
}

/* Writing Animations */
.writing-indicator {
  display: flex;
  align-items: center;
}

.breathing-dot {
  width: 6px;
  height: 6px;
  background: #86BC25;
  border-radius: 50%;
  animation: breathing 1.5s ease-in-out infinite;
}

.writing-placeholder {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 0;
}

.writing-text {
  font-size: 13px;
  color: #86BC25;
}

.breathing-bar {
  height: 4px;
  width: 100%;
  background: linear-gradient(90deg, #f0f2f5 0%, #86BC25 50%, #f0f2f5 100%);
  background-size: 200% 100%;
  animation: bar-slide 2s linear infinite;
  border-radius: 2px;
}

.writing-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(134, 188, 37, 0.03);
  animation: blink 2s ease-in-out infinite;
  pointer-events: none;
  border-radius: 4px;
}

@keyframes breathing {
  0% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.1); }
  100% { opacity: 0.3; transform: scale(0.8); }
}

@keyframes bar-slide {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

@keyframes blink {
  0% { opacity: 0.2; }
  50% { opacity: 0.6; }
  100% { opacity: 0.2; }
}

/* Mode Indicator */
.mode-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 12px;
  color: #856404;
}

.exit-mode {
  margin-left: auto;
  border: none;
  background: transparent;
  color: #1890ff;
  cursor: pointer;
}

.exit-mode:hover {
  text-decoration: underline;
}

/* Empty State */
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #9ca3af;
  padding-top: 120px;
}

.empty-state p {
  font-size: 16px;
  color: #666;
  margin: 16px 0 8px;
}

.empty-state .empty-tip {
  font-size: 14px;
  color: #999;
}

.empty-state .empty-icon {
  color: #86BC25;
  opacity: 0.5;
}

.primary-outline-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-radius: 8px;
  border: 1.5px solid #86BC25;
  background: white;
  color: #86BC25;
  font-weight: 600;
  cursor: pointer;
  margin-top: 20px;
  transition: all 0.3s;
}

.primary-outline-btn:hover {
  background: #f6ffed;
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(134, 188, 37, 0.15);
}

.planning-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: #86BC25;
}

.breathing-light {
  width: 8px;
  height: 8px;
  background: #86BC25;
  border-radius: 50%;
  box-shadow: 0 0 8px #86BC25;
  animation: breathing-light 2s infinite;
}

@keyframes breathing-light {
  0% { opacity: 0.4; box-shadow: 0 0 2px #86BC25; }
  50% { opacity: 1; box-shadow: 0 0 12px #86BC25; }
  100% { opacity: 0.4; box-shadow: 0 0 2px #86BC25; }
}

/* Rest of styles... */

/* Office Style Cleanups */
.document-footer {
  position: absolute;
  bottom: 40px;
  left: 90px;
  right: 90px;
  display: flex;
  justify-content: center;
  border-top: 0.5px solid #d9d9d9;
  padding-top: 4px;
  font-size: 11px;
  color: #8c8c8c;
  font-family: "SimSun", serif;
}

.markdown-body {
  font-family: "PingFang SC", "Microsoft YaHei", "SimSun", serif;
  color: #262626;
  line-height: 1.8;
  font-size: 15px;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  color: #1a1a1a;
  font-weight: 600;
  margin-top: 28px;
  margin-bottom: 16px;
  line-height: 1.4;
  break-inside: avoid;
  break-after: avoid;
}

.markdown-body :deep(h1) { 
  font-size: 26px; 
  text-align: center;
  margin-bottom: 32px;
  padding-bottom: 12px;
  border-bottom: 2px solid #86BC25; 
}

.markdown-body :deep(h2) { 
  font-size: 20px; 
  border-left: 4px solid #86BC25;
  padding-left: 12px;
  margin-top: 32px;
}

.markdown-body :deep(h3) { 
  font-size: 18px;
  color: #333;
}

.markdown-body :deep(p) {
  margin-bottom: 16px;
  text-indent: 0; /* Word docs often don't use indent if there's spacing, or vice versa. Let's stick to spacing for modern look */
  text-align: justify;
}

.markdown-body :deep(ul), 
.markdown-body :deep(ol) {
  padding-left: 28px;
  margin-bottom: 16px;
}

.markdown-body :deep(li) {
  margin-bottom: 10px;
}

.markdown-body :deep(strong) {
  font-weight: 600;
  color: #000;
}



.document-nav-bar {
  padding: 8px 16px;
  background: white;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  align-items: center;
  justify-content: space-between;
  z-index: 10;
  height: 40px;
}

.status-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-icon {
  color: #86BC25;
}

.status-text {
  color: #86BC25;
  font-weight: 600;
  font-size: 14px;
  letter-spacing: 0.5px;
}

.status-metrics {
  display: flex;
  align-items: center;
  gap: 20px;
  color: #8c8c8c;
  font-size: 14px;
}

.metric-item {
  white-space: nowrap;
}

.status-bar {
  display: none;
}

.word-count {
  font-size: 12px;
  color: #8c8c8c;
}

.animate-spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Input Area */
.input-area {
  flex: 1;
  min-height: 400px;
  display: flex;
}

.editor-container {
  flex: 1;
  background: white;
  border-radius: 16px;
  border: 1px solid #e8e8e8;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
  position: relative;
}

.editor-toolbar {
  padding: 12px 20px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fafafa;
}

.toolbar-left {
  display: flex;
  gap: 8px;
}

.tool-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: #595959;
  cursor: pointer;
  transition: all 0.2s;
}

.tool-btn:hover {
  background: #e8e8e8;
  color: #1a1a1a;
}

.upload-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #86BC25;
  color: white;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s;
}

.upload-btn:hover {
  background: #75a620;
}

.main-textarea {
  flex: 1;
  width: 100%;
  padding: 24px;
  border: none;
  resize: none;
  font-size: 16px;
  line-height: 1.8;
  color: #1a1a1a;
  outline: none;
  background: white;
}

.upload-progress-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 12px 20px;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(4px);
  border-top: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  gap: 12px;
}

.progress-track {
  flex: 1;
  height: 6px;
  background: #f0f0f0;
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: #86BC25;
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 12px;
  color: #86BC25;
  font-weight: 500;
  min-width: 80px;
}

/* Status Area */
.status-area {
  height: 40px;
  flex-shrink: 0;
}

.status-content {
  display: flex;
  gap: 24px;
  align-items: center;
  justify-content: center;
}

.status-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #8c8c8c;
}

.status-item.success { color: #52c41a; }
.status-item.error { color: #ff4d4f; }
.status-item.uploading, .status-item.processing { color: #86BC25; font-weight: 500; }

.icon-ready { color: #bfbfbf; }

/* Toast */
.toast-overlay {
  position: fixed;
  top: 80px;
  left: 50%;
  transform: translateX(-50%);
  padding: 12px 24px;
  border-radius: 12px;
  background: white;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.15);
  display: flex;
  align-items: center;
  gap: 10px;
  z-index: 1000;
  font-weight: 500;
}

.toast-overlay.success { color: #52c41a; border-left: 4px solid #52c41a; }
.toast-overlay.error { color: #ff4d4f; border-left: 4px solid #ff4d4f; }

.toast-enter-active, .toast-leave-active {
  transition: all 0.3s cubic-bezier(0.18, 0.89, 0.32, 1.28);
}

.toast-enter-from, .toast-leave-to {
  opacity: 0;
  transform: translate(-50%, -20px);
}

@media (max-width: 768px) {
  .action-buttons {
    flex-wrap: wrap;
  }
  .action-item {
    flex: 1;
    min-width: 140px;
  }
}
</style>
