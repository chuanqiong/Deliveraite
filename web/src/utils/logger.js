import { VERSION, ENVIRONMENT } from '@/version';

/**
 * 前端日志工具类
 * 支持结构化日志记录，并自动包含 trace_id
 */

let currentTraceId = '';

const generateUUID = () => {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
};

export const setTraceId = (id) => {
  currentTraceId = id;
};

export const getTraceId = () => {
  if (!currentTraceId) {
    currentTraceId = generateUUID();
  }
  return currentTraceId;
};

const log = (level, message, extra = {}) => {
  const traceId = getTraceId();
  const logEntry = {
    timestamp: new Date().toISOString(),
    level,
    message,
    trace_id: traceId,
    env: ENVIRONMENT,
    version: VERSION,
    ...extra
  };

  const logString = `[${logEntry.timestamp}] [${level}] [${traceId}] ${message}`;
  
  switch (level) {
    case 'DEBUG':
      console.debug(logString, extra);
      break;
    case 'INFO':
      console.info(logString, extra);
      break;
    case 'WARN':
      console.warn(logString, extra);
      break;
    case 'ERROR':
      console.error(logString, extra);
      break;
    default:
      console.log(logString, extra);
  }
  
  // 这里可以扩展为发送到后端的日志收集接口
};

export const logger = {
  debug: (msg, extra) => log('DEBUG', msg, extra),
  info: (msg, extra) => log('INFO', msg, extra),
  warn: (msg, extra) => log('WARN', msg, extra),
  error: (msg, extra) => log('ERROR', msg, extra)
};
