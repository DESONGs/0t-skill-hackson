export const state = {
  overview: null,
  runtimes: null,
  runtime: null,
  sessions: null,
  activeRuns: null,
  evaluations: null,
  candidates: null,
  promotions: null,
  candidateSurface: null,
  evolution: null,
  styleDistillations: null,
  latestStyleDistillation: null,
  workspaceDir: ".ot-workspace",
  loading: false,
  error: null,
};

export const metricLabels = {
  runtime_count: "已注册 Runtime",
  run_count: "累计运行数",
  active_run_count: "当前活跃任务",
  session_count: "会话总数",
  trace_count: "追踪轨迹 (Traces)",
  artifact_count: "交付物 (Artifacts)",
  evaluation_count: "评估报告数",
  candidate_count: "候选包数",
  promotion_count: "晋升记录数",
  style_distillation_count: "蒸馏任务数",
};

export const statusLabels = {
  ready: "就绪",
  running: "运行中",
  active: "活跃",
  pending: "待执行",
  succeeded: "成功",
  failed: "失败",
  passed: "通过",
  queued: "排队中",
  blocked: "阻塞",
  compiled: "已编译",
  validated: "已验证",
  promoted: "已晋升",
  validation_failed: "验证失败",
  draft: "草稿",
  unknown: "未知",
};

export function updateWorkspaceDir(value) {
  state.workspaceDir = String(value || ".ot-workspace").trim() || ".ot-workspace";
}
