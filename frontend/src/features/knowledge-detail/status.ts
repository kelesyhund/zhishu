import type {
  AgentRunStatus,
  DocumentItem,
  DocumentTaskStage,
  DocumentTaskStatus,
  ToolExecutionStatus,
} from '../../api'

export type StatusTagType = 'info' | 'success' | 'warning' | 'danger'

export function documentStatusText(status: DocumentItem['status']) {
  return { PROCESSING: '处理中', SUCCESS: '处理成功', FAILURE: '处理失败' }[status]
}

export function documentStatusTag(status: DocumentItem['status']): StatusTagType {
  return { PROCESSING: 'info', SUCCESS: 'success', FAILURE: 'danger' }[status] as StatusTagType
}

export function taskStatusText(status: DocumentTaskStatus) {
  return {
    PENDING: '等待处理', PROCESSING: '正在处理', RETRYING: '等待自动重试', SUCCESS: '处理成功',
    FAILURE: '处理失败', ENQUEUE_FAILED: '任务投递失败', CANCEL_REQUESTED: '正在取消', CANCELLED: '已取消',
  }[status]
}

export function taskStageText(stage: DocumentTaskStage) {
  return {
    WAITING: '等待 Worker', READING: '读取并解析文件', SPLITTING: '切分文本', EMBEDDING: '生成向量',
    SAVING: '保存切片', DONE: '处理完成', FAILED: '处理失败', CANCELLING: '等待安全取消', CANCELLED: '任务已取消',
  }[stage]
}

export function taskStatusTag(status: DocumentTaskStatus): StatusTagType {
  if (status === 'SUCCESS') return 'success'
  if (status === 'FAILURE' || status === 'ENQUEUE_FAILED') return 'danger'
  if (status === 'RETRYING' || status === 'CANCEL_REQUESTED') return 'warning'
  return 'info'
}

export function agentStatusText(status: AgentRunStatus) {
  return { RUNNING: '执行中', SUCCESS: '执行成功', FAILURE: '执行失败', LIMIT_REACHED: '达到最大执行步骤', CANCELLED: '执行已取消' }[status]
}

export function agentStatusTag(status: AgentRunStatus): StatusTagType {
  return { RUNNING: 'info', SUCCESS: 'success', FAILURE: 'danger', LIMIT_REACHED: 'warning', CANCELLED: 'warning' }[status] as StatusTagType
}

export function toolStatusText(status: ToolExecutionStatus) {
  return { RUNNING: '执行中', SUCCESS: '成功', FAILURE: '失败', REJECTED: '已拒绝' }[status]
}

export function toolStatusTag(status: ToolExecutionStatus): StatusTagType {
  return { RUNNING: 'info', SUCCESS: 'success', FAILURE: 'danger', REJECTED: 'warning' }[status] as StatusTagType
}
