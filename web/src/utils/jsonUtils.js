// 错误分类统计
const errorStats = {
  truncated: 0,
  missingComma: 0,
  missingQuotes: 0,
  multipleJson: 0,
  unrecoverable: 0
}

/**
 * 记录 JSON 解析错误及修复详情
 * @param {string} original 原始字符串
 * @param {string} repaired 修复后的字符串
 * @param {Error} error 错误对象
 * @param {string} context 上下文信息
 */
const logJsonError = (original, repaired, error, context = '') => {
  const diff = original.length !== repaired.length ? 
    `长度差异: ${original.length} -> ${repaired.length}` : '长度无变化'
  
  console.group(`[JSON Error Detail] ${context}`)
  console.error('Error Message:', error.message)
  console.log('Original JSON (first 200 chars):', original.substring(0, 200) + '...')
  console.log('Repaired JSON (last 200 chars):', repaired.substring(repaired.length - 200))
  console.log('Repair Stats:', diff)
  
  // 简单分类逻辑
  if (error.message.includes('Unexpected end of JSON input')) errorStats.truncated++
  else if (error.message.includes('Unexpected token')) errorStats.missingComma++
  else errorStats.unrecoverable++
  
  console.log('Cumulative Error Stats:', errorStats)
  console.groupEnd()
}

/**
 * 智能解析 JSON 字符串，处理 LLM 可能返回的多个 JSON 块或格式错误
 * @param {string} str 
 * @param {boolean} attemptRepair 是否尝试自动修复，默认为 true
 * @returns {any}
 */
export const smartParseJson = (str, attemptRepair = true) => {
  if (!str || typeof str !== 'string') return str

  let cleanStr = str.trim()
  
  // 提取 Markdown 代码块中的内容
  const jsonBlockMatch = cleanStr.match(/```json\s*([\s\S]*?)\s*```/)
  if (jsonBlockMatch) {
    cleanStr = jsonBlockMatch[1].trim()
  } else {
    const codeBlockMatch = cleanStr.match(/```\s*([\s\S]*?)\s*```/)
    if (codeBlockMatch) {
      cleanStr = codeBlockMatch[1].trim()
    }
  }

  try {
    return JSON.parse(cleanStr)
  } catch (e) {
    console.warn(`[JSONUtils] 首次解析失败: ${e.message}`)
    
    if (!attemptRepair) {
      logJsonError(str, cleanStr, e, 'No Repair Attempted')
      throw e
    }

    // 尝试修复 JSON
    let repaired = cleanStr
    try {
      repaired = repairTruncatedJson(cleanStr)
      if (repaired !== cleanStr) {
        console.log(`[JSONUtils] 尝试使用修复后的 JSON (长度从 ${cleanStr.length} 变为 ${repaired.length})`)
        const result = JSON.parse(repaired)
        console.log('✅ [JSONUtils] 修复后解析成功')
        return result
      }
    } catch (repairErr) {
      console.warn(`[JSONUtils] 修复后解析依然失败: ${repairErr.message}`)
    }

    // 处理 "Unexpected non-whitespace character after JSON at position X" 错误
    // 这通常发生在 LLM 输出了一个完整的 JSON 后又跟了另一个 JSON 或其它文字
    const match = e.message.match(/position (\d+)/)
    if (match && (e.message.includes('Unexpected non-whitespace character after JSON') || 
                  e.message.includes('Unexpected token'))) {
      const pos = parseInt(match[1])
      const part1 = cleanStr.substring(0, pos).trim()
      
      if (part1) {
        try {
          errorStats.multipleJson++
          return smartParseJson(part1, false)
        } catch (e3) {
          // 如果第一部分也解析失败，尝试寻找最长的有效 JSON 子串
          return tryExtractValidJson(cleanStr)
        }
      }
    }
    
    // 最后尝试提取
    try {
      return tryExtractValidJson(cleanStr)
    } catch (finalErr) {
      logJsonError(str, repaired, finalErr, 'Final Extraction Failed')
      throw finalErr
    }
  }
}

/**
 * 尝试从字符串中提取最长的有效 JSON 对象或数组
 * @param {string} str 
 * @returns {any}
 */
const tryExtractValidJson = (str) => {
  console.log('[JSONUtils] 尝试提取有效 JSON 子串...')
  
  // 寻找所有可能的开始位置 { 或 [
  const starts = []
  for (let i = 0; i < str.length; i++) {
    if (str[i] === '{' || str[i] === '[') starts.push(i)
  }
  
  // 从后往前寻找结束位置 } 或 ]
  const ends = []
  for (let i = str.length - 1; i >= 0; i--) {
    if (str[i] === '}' || str[i] === ']') ends.push(i)
  }
  
  // 尝试所有可能的组合，按长度降序排列
  const candidates = []
  for (const s of starts) {
    for (const e of ends) {
      if (e > s) {
        candidates.push({ s, e, len: e - s })
      }
    }
  }
  
  candidates.sort((a, b) => b.len - a.len)
  
  for (const cand of candidates) {
    const sub = str.substring(cand.s, cand.e + 1)
    try {
      return JSON.parse(sub)
    } catch (err) {
      // 尝试修复这个子串
      try {
        return JSON.parse(repairTruncatedJson(sub))
      } catch (err2) {
        continue
      }
    }
  }
  
  throw new Error('无法从输入中提取有效的 JSON 结构')
}

/**
 * 验证 targetWords 的合理性，过滤掉异常值
 * @param {Array} items 
 * @returns {Array}
 */
export const validateAndFixTargetWords = (items) => {
  if (!items || !Array.isArray(items)) return items
  
  return items.map(item => {
    // 根据层级设置合理的字数范围（与后端提示词保持一致）
    const maxWords = {
      1: 150000,  // 一级章节最大 15 万字
      2: 80000,   // 二级章节最大 8 万字
      3: 20000,   // 三级章节最大 2 万字
      4: 15000    // 四级章节最大 1.5 万字
    }

    const minWords = 50  // 最小字数

    // 如果 targetWords 超出对应层级的合理范围，进行修正
    const levelMax = maxWords[item.level] || maxWords[4]
    if (item.targetWords > levelMax || item.targetWords < minWords) {
      console.warn(`⚠️ 章节 "${item.title}" (层级${item.level}) 的 targetWords (${item.targetWords}) 异常，超过上限 ${levelMax}，已修正为合理值`)

      // 根据层级设置合理的默认值
      if (item.level === 1) item.targetWords = Math.max(minWords, Math.min(100000, item.targetWords))
      else if (item.level === 2) item.targetWords = Math.max(minWords, Math.min(50000, item.targetWords))
      else if (item.level === 3) item.targetWords = Math.max(minWords, Math.min(10000, item.targetWords))
      else item.targetWords = Math.max(minWords, Math.min(5000, item.targetWords))
    }

    // 递归处理子章节
    if (item.children && Array.isArray(item.children)) {
      item.children = validateAndFixTargetWords(item.children)
    }

    return item
  })
}

/**
 * 修复截断或有语法错误的 JSON 字符串
 * @param {string} jsonStr 
 * @returns {string}
 */
export const repairTruncatedJson = (jsonStr) => {
  if (!jsonStr) return ''
  
  let repaired = jsonStr.trim()
  
  // 1. 处理常见的 LLM 错误输出
  // 移除开头的 markdown 标记（如果 smartParseJson 没处理干净）
  repaired = repaired.replace(/^```json\s*/, '').replace(/```$/, '').trim()
  
  // 2. 补齐未闭合的双引号
  let inString = false
  let escaped = false
  for (let i = 0; i < repaired.length; i++) {
    if (repaired[i] === '\\' && !escaped) {
      escaped = true
    } else if (repaired[i] === '"' && !escaped) {
      inString = !inString
      escaped = false
    } else {
      escaped = false
    }
  }
  if (inString) repaired += '"'

  // 3. 处理末尾多余的逗号
  repaired = repaired.replace(/,\s*([}\]])/g, '$1').replace(/,\s*$/, '')

  // 4. 修复缺失的逗号 (在 } { 或 ] [ 之间)
  repaired = repaired.replace(/}\s*{/g, '},{').replace(/]\s*\[/g, '],[')
  
  // 5. 修复属性名缺失引号 (常见于某些不规范的 LLM)
  // 匹配类似 { title: "xxx" } -> { "title": "xxx" }
  repaired = repaired.replace(/([{,]\s*)([a-zA-Z0-9_]+)(\s*:)/g, '$1"$2"$3')

  // 6. 补齐未闭合的大括号和中括号
  const stack = []
  inString = false
  escaped = false
  let result = ''
  
  for (let i = 0; i < repaired.length; i++) {
    const char = repaired[i]
    
    if (char === '\\' && !escaped) {
      escaped = true
      result += char
      continue
    }

    if (char === '"' && !escaped) {
      inString = !inString
      result += char
      continue
    }
    
    escaped = false

    if (inString) {
      result += char
      continue
    }

    if (char === '{' || char === '[') {
      stack.push(char === '{' ? '}' : ']')
      result += char
    } else if (char === '}' || char === ']') {
      if (stack.length > 0 && stack[stack.length - 1] === char) {
        stack.pop()
        result += char
      } else {
        // 跳过不匹配的闭合括号
        continue
      }
    } else {
      result += char
    }
  }
  
  repaired = result
  
  // 逆序闭合所有未闭合的括号
  while (stack.length > 0) {
    repaired += stack.pop()
  }
  
  // 7. 特殊处理：如果最后以属性名结束，如 "content": "...
  // 我们已经补齐了引号和括号，但如果内容本身不完整，可能还需要处理
  
  return repaired
}

/**
 * 解析嵌套大纲结构并展平为扁平结构
 * @param {Array} nestedOutline 
 * @returns {Array}
 */
export const parseNestedOutline = (nestedOutline) => {
  if (!nestedOutline || !Array.isArray(nestedOutline)) return []

  const flattenOutline = (items, parentId = null) => {
    const result = []
    items.forEach(item => {
      const { children, ...rest } = item
      result.push({ ...rest, parentId })
      if (children && children.length > 0) {
        result.push(...flattenOutline(children, item.id))
      }
    })
    return result
  }

  return flattenOutline(nestedOutline)
}
