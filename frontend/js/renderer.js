import { state, metricLabels, statusLabels } from "./state.js";
import { $ } from "./utils.js";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    switch (char) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      case "'":
        return "&#39;";
      default:
        return char;
    }
  });
}

function formatTimestamp(value) {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function getOverview() {
  return state.overview || {};
}

function getRuntime() {
  return state.runtime || getOverview().runtime || {};
}

function getRuntimes() {
  return state.runtimes || getOverview().runtimes || {};
}

function getSessions() {
  const payload = state.sessions || getOverview().sessions || {};
  return payload.items || [];
}

function getActiveRuns() {
  const payload = state.activeRuns || getOverview().active_runs || {};
  return payload.items || [];
}

function getEvaluations() {
  const payload = state.evaluations || getOverview().evaluations || {};
  return payload.items || [];
}

function getCandidates() {
  const payload = state.candidates || getOverview().candidates || {};
  return payload.items || [];
}

function getPromotions() {
  const payload = state.promotions || getOverview().promotions || {};
  return payload.items || [];
}

function getLifecycle() {
  return state.evolution || getOverview().evolution || {};
}

function getStyleDistillations() {
  return state.styleDistillations || getOverview().style_distillations || {};
}

function getStyleDistillationItems() {
  const payload = getStyleDistillations();
  return payload.items || [];
}

function getLatestStyleDistillation() {
  return state.latestStyleDistillation || getStyleDistillations().latest || getStyleDistillationItems()[0] || null;
}

export function activateSection(target) {
  document.querySelectorAll(".nav-item[data-target]").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.target === target);
  });
  document.querySelectorAll(".section").forEach((section) => {
    section.classList.toggle("is-active", section.id === target);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function simplifyPath(path) {
  if (!path) return "未指定";
  const parts = path.split(/[/\\]/).filter((part) => part.length > 0);
  return parts.pop() || path;
}

function renderCardList(containerId, items, formatter, emptyText) {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = items.length
    ? items.map(formatter).join("")
    : `<div class="glass-card empty-state">${escapeHtml(emptyText)}</div>`;
}

function renderSummaryBox(containerId, rows) {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = rows
    .map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
}

export function renderDashboard() {
  const overview = getOverview();
  const runtime = getRuntime();
  const runtimes = getRuntimes();
  const sessions = getSessions();
  const activeRuns = getActiveRuns();
  const evaluations = getEvaluations();
  const candidates = getCandidates();
  const promotions = getPromotions();
  const lifecycle = getLifecycle();
  const styleDistillations = getStyleDistillationItems();
  const latestStyleDistillation = getLatestStyleDistillation();

  const title = $("#dashboard-title");
  if (title) title.textContent = overview.dashboard?.title || "运行时总览";
  const summary = $("#dashboard-summary");
  if (summary) summary.textContent = overview.dashboard?.summary || "只读控制面，监控运行、评估、候选与晋升摘要。";

  const runtimeContext = overview.runtime_context || {};
  renderSummaryBox("#runtime-context", [
    ["当前工作区", simplifyPath(runtimeContext.workspace_dir || runtime.workspace_root || state.workspaceDir)],
    ["系统模式", runtimeContext.frontend_mode || runtime.mode || "runtime-dashboard"],
    ["已注册 Runtime", String(runtimes.count ?? runtime.runtime_count ?? 0)],
    ["活跃运行", String(runtimeContext.active_run_count ?? runtime.active_run_count ?? 0)],
    ["会话总数", String(runtimeContext.session_count ?? runtime.session_count ?? 0)],
    ["候选数", String(runtimeContext.candidate_count ?? runtime.candidate_count ?? 0)],
    ["晋升数", String(runtimeContext.promotion_count ?? runtime.promotion_count ?? 0)],
    ["蒸馏任务", String(runtimeContext.style_distillation_count ?? styleDistillations.length ?? 0)],
  ]);

  const metricsBox = $("#runtime-metrics");
  if (metricsBox) {
    const metrics = {
      runtime_count: runtimes.count || runtime.runtime_count || 0,
      run_count: runtime.run_count || 0,
      active_run_count: runtime.active_run_count || 0,
      session_count: runtime.session_count || 0,
      trace_count: runtime.trace_count || 0,
      artifact_count: runtime.artifact_count || 0,
      evaluation_count: runtime.evaluation_count || 0,
      style_distillation_count: runtimeContext.style_distillation_count || styleDistillations.length || 0,
    };
    metricsBox.innerHTML = Object.entries(metrics)
      .map(([key, value], idx) => `
        <div class="glass-card runtime-metric" style="animation: slideDown 0.35s ease forwards ${idx * 0.04}s; opacity:0;">
          <span>${metricLabels[key] || key}</span>
          <strong>${value}</strong>
        </div>
      `)
      .join("");
  }

  const statusRow = $("#runtime-status-row");
  if (statusRow) {
    const statusCounts = runtime.status_counts || {};
    statusRow.innerHTML = Object.entries(statusCounts)
      .map(([key, value]) => `<span class="pill status-${key.toLowerCase()}">${escapeHtml(statusLabels[key] || key)} · ${value}</span>`)
      .join("");
  }

  const latestRun = runtime.latest_active_run || runtime.latest_run;
  const latestRunBox = $("#latest-run-card");
  if (latestRunBox) {
    latestRunBox.innerHTML = latestRun
      ? `
        <div class="card-heading">最新运行</div>
        <div class="card-title">${escapeHtml(latestRun.run_id || "unknown")}</div>
        <div class="card-meta">Agent: ${escapeHtml(latestRun.agent_id || "unknown")}</div>
        <div class="card-meta">流程: ${escapeHtml(latestRun.flow_id || "unknown")}</div>
        <div class="card-meta">状态: <span class="pill status-${(latestRun.status || "unknown").toLowerCase()}">${escapeHtml(statusLabels[latestRun.status] || latestRun.status || "unknown")}</span></div>
        <div class="card-meta">时间: ${escapeHtml(formatTimestamp(latestRun.started_at))}</div>
      `
      : `<div class="card-heading">最新运行</div><div class="card-meta">暂无运行记录</div>`;
  }

  const latestEvaluation = runtime.latest_evaluation;
  const latestEvaluationBox = $("#latest-evaluation-card");
  if (latestEvaluationBox) {
    latestEvaluationBox.innerHTML = latestEvaluation
      ? `
        <div class="card-heading">最新评估</div>
        <div class="card-title">${escapeHtml(latestEvaluation.evaluation_id || "unknown")}</div>
        <div class="card-meta">等级: <span class="pill">${escapeHtml(latestEvaluation.overall_grade || latestEvaluation.grade || "unknown")}</span></div>
        <div class="card-meta">Runtime 通过: ${latestEvaluation.runtime_pass ? "是" : "否"}</div>
        <div class="card-meta">Contract 通过: ${latestEvaluation.contract_pass ? "是" : "否"}</div>
        <div class="card-meta">匹配度: ${escapeHtml(String(latestEvaluation.task_match_score ?? "-"))}</div>
      `
      : `<div class="card-heading">最新评估</div><div class="card-meta">暂无评估记录</div>`;
  }

  const latestCandidate = lifecycle.latest_candidate || candidates[0];
  const latestCandidateBox = $("#latest-candidate-card");
  if (latestCandidateBox) {
    latestCandidateBox.innerHTML = latestCandidate
      ? `
        <div class="card-heading">最新候选</div>
        <div class="card-title">${escapeHtml(latestCandidate.candidate_id || "unknown")}</div>
        <div class="card-meta">目标 Skill: ${escapeHtml(latestCandidate.target_skill_name || "unknown")}</div>
        <div class="card-meta">状态: <span class="pill">${escapeHtml(statusLabels[latestCandidate.status] || latestCandidate.status || "unknown")}</span></div>
        <div class="card-meta">验证状态: ${escapeHtml(statusLabels[latestCandidate.validation_status] || latestCandidate.validation_status || "pending")}</div>
      `
      : `<div class="card-heading">最新候选</div><div class="card-meta">暂无候选记录</div>`;
  }

  const latestPromotion = lifecycle.latest_promotion || promotions[0];
  const latestPromotionBox = $("#latest-promotion-card");
  if (latestPromotionBox) {
    latestPromotionBox.innerHTML = latestPromotion
      ? `
        <div class="card-heading">最新晋升</div>
        <div class="card-title">${escapeHtml(latestPromotion.promotion_id || "unknown")}</div>
        <div class="card-meta">目标 Skill: ${escapeHtml(latestPromotion.target_skill_name || "unknown")}</div>
        <div class="card-meta">注册状态: <span class="pill">${escapeHtml(statusLabels[latestPromotion.registry_status] || latestPromotion.registry_status || "unknown")}</span></div>
        <div class="card-meta">验证状态: ${escapeHtml(statusLabels[latestPromotion.validation_status] || latestPromotion.validation_status || "unknown")}</div>
      `
      : `<div class="card-heading">最新晋升</div><div class="card-meta">暂无晋升记录</div>`;
  }

  renderSummaryBox("#overview-summary", [
    ["累计运行", String(runtime.run_count || 0)],
    ["Runtime 类型", String(runtimes.count || runtime.runtime_count || 0)],
    ["活跃会话", String(runtime.session_count || 0)],
    ["评估数", String(runtime.evaluation_count || 0)],
    ["候选数", String(runtime.candidate_count || 0)],
    ["晋升数", String(runtime.promotion_count || 0)],
    ["蒸馏数", String(runtimeContext.style_distillation_count || styleDistillations.length || 0)],
  ]);

  renderSummaryBox("#evolution-summary", [
    ["评估记录", String(lifecycle.counts?.evaluations || evaluations.length || 0)],
    ["候选 Skill", String(lifecycle.counts?.candidates || candidates.length || 0)],
    ["晋升记录", String(lifecycle.counts?.promotions || promotions.length || 0)],
  ]);

  const runtimeCards = (runtimes.items || []).map((item) => `
    <article class="glass-card list-card">
      <div class="list-card-header">
        <div>
          <div class="card-heading">运行时</div>
          <div class="card-title">${escapeHtml(item.runtime_id || item.descriptor?.runtime_id || "unknown")}</div>
        </div>
        <span class="pill status-${item.enabled === false ? "failed" : "success"}">${item.enabled === false ? "已禁用" : "可用"}</span>
      </div>
      <div class="list-card-body">
        <div><span>名称</span><strong>${escapeHtml(item.descriptor?.name || "unknown")}</strong></div>
        <div><span>类型</span><strong>${escapeHtml(item.descriptor?.runtime_type || "unknown")}</strong></div>
        <div><span>模式</span><strong>${escapeHtml(item.descriptor?.execution_mode || "unknown")}</strong></div>
        <div><span>工具集</span><strong>${(item.descriptor?.tool_surface || []).length}</strong></div>
      </div>
    </article>
  `);
  renderCardList("#registered-runtimes", runtimes.items || [], () => runtimeCards.shift(), "当前没有注册 runtime。");
  renderCardList("#runtimes-list", runtimes.items || [], (item) => `
    <article class="glass-card list-card">
      <div class="list-card-header">
        <div>
          <div class="card-heading">运行时</div>
          <div class="card-title">${escapeHtml(item.runtime_id || item.descriptor?.runtime_id || "unknown")}</div>
        </div>
        <span class="pill status-${item.enabled === false ? "failed" : "success"}">${item.enabled === false ? "已禁用" : "可用"}</span>
      </div>
      <div class="list-card-body">
        <div><span>名称</span><strong>${escapeHtml(item.descriptor?.name || "unknown")}</strong></div>
        <div><span>类型</span><strong>${escapeHtml(item.descriptor?.runtime_type || "unknown")}</strong></div>
        <div><span>模式</span><strong>${escapeHtml(item.descriptor?.execution_mode || "unknown")}</strong></div>
        <div><span>工具集</span><strong>${(item.descriptor?.tool_surface || []).length}</strong></div>
      </div>
    </article>
  `, "当前没有注册 runtime。");

  const notesBox = $("#runtime-notes");
  if (notesBox) {
    notesBox.innerHTML = (runtime.runtime_notes || [])
      .map((note) => `<li>${escapeHtml(note)}</li>`)
      .join("");
  }

  renderCardList("#sessions-list", sessions, (session) => `
    <article class="glass-card list-card">
      <div class="list-card-header">
        <div>
          <div class="card-heading">会话</div>
          <div class="card-title">${escapeHtml(session.session_id || "unknown")}</div>
        </div>
        <span class="pill">${session.active_run_count || 0} 活跃中</span>
      </div>
      <div class="list-card-body">
        <div><span>执行 Agent</span><strong>${escapeHtml(session.agent_id || "unknown")}</strong></div>
        <div><span>流程</span><strong>${escapeHtml(session.flow_id || "unknown")}</strong></div>
        <div><span>运行次数</span><strong>${session.run_count || 0}</strong></div>
        <div><span>最后活跃</span><strong>${escapeHtml(formatTimestamp(session.last_seen_at))}</strong></div>
      </div>
      <div class="list-card-footer">
        <span>最近 Run ID: ${escapeHtml((session.recent_run_ids || []).join(" · ") || "无")}</span>
      </div>
    </article>
  `, "当前工作区没有可显示的会话。");

  renderCardList("#active-runs-list", activeRuns, (run) => `
    <article class="glass-card list-card">
      <div class="list-card-header">
        <div>
          <div class="card-heading">活跃执行</div>
          <div class="card-title">${escapeHtml(run.run_id || "unknown")}</div>
        </div>
        <span class="pill status-${(run.status || "unknown").toLowerCase()}">${escapeHtml(statusLabels[run.status] || run.status || "unknown")}</span>
      </div>
      <div class="list-card-body">
        <div><span>所属会话</span><strong>${escapeHtml(run.runtime_session_id || "unknown")}</strong></div>
        <div><span>执行 Agent</span><strong>${escapeHtml(run.agent_id || "unknown")}</strong></div>
        <div><span>流程</span><strong>${escapeHtml(run.flow_id || "unknown")}</strong></div>
        <div><span>交付物数</span><strong>${run.artifact_count || 0}</strong></div>
      </div>
      <div class="list-card-footer">
        <span>摘要: ${escapeHtml(run.summary || "暂无摘要")}</span>
        <span>开始于: ${escapeHtml(formatTimestamp(run.started_at))}</span>
      </div>
    </article>
  `, "当前没有正在运行的任务。");

  renderCardList("#evaluations-list", evaluations, (item) => `
    <article class="glass-card list-card">
      <div class="list-card-header">
        <div>
          <div class="card-heading">Evaluation</div>
          <div class="card-title">${escapeHtml(item.evaluation_id || "unknown")}</div>
        </div>
        <span class="pill">${escapeHtml(item.overall_grade || "unknown")}</span>
      </div>
      <div class="list-card-body">
        <div><span>关联运行</span><strong>${escapeHtml(item.run_id || "unknown")}</strong></div>
        <div><span>Runtime</span><strong>${item.runtime_pass ? "通过" : "未通过"}</strong></div>
        <div><span>Contract</span><strong>${item.contract_pass ? "通过" : "未通过"}</strong></div>
        <div><span>匹配度</span><strong>${escapeHtml(String(item.task_match_score ?? "-"))}</strong></div>
      </div>
      <div class="list-card-footer">
        <span>${escapeHtml(item.summary || "暂无摘要")}</span>
      </div>
    </article>
  `, "当前没有评估记录。");

  renderCardList("#candidates-list", candidates, (item) => `
    <article class="glass-card list-card">
      <div class="list-card-header">
        <div>
          <div class="card-heading">Candidate</div>
          <div class="card-title">${escapeHtml(item.candidate_id || "unknown")}</div>
        </div>
        <span class="pill">${escapeHtml(item.validation_status || item.status || "pending")}</span>
      </div>
      <div class="list-card-body">
        <div><span>目标 Skill</span><strong>${escapeHtml(item.target_skill_name || "unknown")}</strong></div>
        <div><span>类型</span><strong>${escapeHtml(item.target_skill_kind || "unknown")}</strong></div>
        <div><span>候选类别</span><strong>${escapeHtml(item.candidate_type || "general")}</strong></div>
        <div><span>会话 ID</span><strong>${escapeHtml(item.runtime_session_id || "unknown")}</strong></div>
        <div><span>状态</span><strong>${escapeHtml(item.status || "pending")}</strong></div>
      </div>
      <div class="list-card-footer">
        <span>${escapeHtml(item.change_summary || "暂无变更摘要")}</span>
      </div>
    </article>
  `, "当前没有候选 Skill。");

  renderCardList("#promotions-list", promotions, (item) => `
    <article class="glass-card list-card">
      <div class="list-card-header">
        <div>
          <div class="card-heading">Promotion</div>
          <div class="card-title">${escapeHtml(item.promotion_id || "unknown")}</div>
        </div>
        <span class="pill">${escapeHtml(item.registry_status || item.validation_status || "pending")}</span>
      </div>
      <div class="list-card-body">
        <div><span>目标 Skill</span><strong>${escapeHtml(item.target_skill_name || "unknown")}</strong></div>
        <div><span>候选 ID</span><strong>${escapeHtml(item.candidate_id || "unknown")}</strong></div>
        <div><span>验证状态</span><strong>${escapeHtml(item.validation_status || "pending")}</strong></div>
        <div><span>注册状态</span><strong>${escapeHtml(item.registry_status || "pending")}</strong></div>
        <div><span>会话 ID</span><strong>${escapeHtml(item.runtime_session_id || "unknown")}</strong></div>
      </div>
      <div class="list-card-footer">
        <span>${escapeHtml(item.package_path || "尚未生成正式 skill 根目录")}</span>
      </div>
    </article>
  `, "当前没有晋升记录。");

  const latestStyleCard = $("#style-distill-latest-card");
  if (latestStyleCard) {
    latestStyleCard.innerHTML = latestStyleDistillation
      ? `
        <div class="card-heading">最新蒸馏</div>
        <div class="card-title">${escapeHtml(latestStyleDistillation.target_skill_name || latestStyleDistillation.summary?.target_skill_name || "wallet-style")}</div>
        <div class="card-meta">地址: ${escapeHtml(latestStyleDistillation.wallet || latestStyleDistillation.summary?.wallet || "unknown")}</div>
        <div class="card-meta">Review Backend: ${escapeHtml(latestStyleDistillation.review_backend || latestStyleDistillation.summary?.review_backend || latestStyleDistillation.reflection?.review_backend || "unknown")}</div>
        <div class="card-meta">Reflection Run: ${escapeHtml(latestStyleDistillation.reflection_run_id || latestStyleDistillation.summary?.reflection_run_id || latestStyleDistillation.reflection?.reflection_run_id || "unknown")}</div>
        <div class="card-meta">候选: ${escapeHtml(latestStyleDistillation.candidate?.candidate_id || latestStyleDistillation.summary?.candidate_id || "unknown")}</div>
        <div class="card-meta">晋升: ${escapeHtml(latestStyleDistillation.promotion?.promotion_id || latestStyleDistillation.summary?.promotion_id || "unknown")}</div>
        <div class="card-meta">置信度: ${escapeHtml(String(latestStyleDistillation.profile?.confidence ?? latestStyleDistillation.summary?.confidence ?? "-"))}</div>
        <div class="card-meta">执行就绪: ${escapeHtml(latestStyleDistillation.execution_readiness || latestStyleDistillation.summary?.execution_readiness || "unknown")}</div>
        <div class="card-meta">示例就绪: ${escapeHtml(latestStyleDistillation.example_readiness || latestStyleDistillation.summary?.example_readiness || "unknown")}</div>
      `
      : `<div class="card-heading">最新蒸馏</div><div class="card-meta">暂无蒸馏记录</div>`;
  }

  const latestStyleQa = $("#style-distill-qa-card");
  if (latestStyleQa) {
    const checks = latestStyleDistillation?.qa?.checks || [];
    latestStyleQa.innerHTML = latestStyleDistillation
      ? `
        <div class="card-heading">QA 闭环</div>
        <div class="card-title">${escapeHtml(latestStyleDistillation.qa?.status || latestStyleDistillation.summary?.qa_status || "unknown")}</div>
        <div class="card-meta">Reflection 状态: ${escapeHtml(latestStyleDistillation.reflection_status || latestStyleDistillation.summary?.reflection_status || latestStyleDistillation.reflection?.status || "unknown")}</div>
        <div class="card-meta">Fallback: ${latestStyleDistillation.fallback_used || latestStyleDistillation.summary?.fallback_used ? "是" : "否"}</div>
        <div class="card-meta">Stage: ${escapeHtml(Object.entries(latestStyleDistillation.stage_statuses || latestStyleDistillation.summary?.stage_statuses || {}).map(([stage, meta]) => `${stage}:${meta?.status || "unknown"}`).join(" / ") || "unknown")}</div>
        ${checks.map((item) => `<div class="card-meta">${escapeHtml(item.check)}: ${item.passed ? "通过" : "失败"}</div>`).join("")}
      `
      : `<div class="card-heading">QA 闭环</div><div class="card-meta">等待任务执行</div>`;
  }

  const styleProfile = $("#style-distill-profile");
  if (styleProfile) {
    const profile = latestStyleDistillation?.profile;
    styleProfile.innerHTML = profile
      ? [
          ["Backend", latestStyleDistillation?.review_backend || latestStyleDistillation?.summary?.review_backend || "unknown"],
          ["摘要", profile.summary || "暂无摘要"],
          ["节奏", profile.execution_tempo || "unknown"],
          ["风险", profile.risk_appetite || "unknown"],
          ["仓位偏好", profile.conviction_profile || "unknown"],
          ["稳定币姿态", profile.stablecoin_bias || "unknown"],
          ["主动作", (profile.dominant_actions || []).join(" / ") || "unknown"],
          ["偏好代币", (profile.preferred_tokens || []).join(", ") || "unknown"],
          ["活跃窗口", (profile.active_windows || []).join(", ") || "unknown"],
        ]
          .map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
          .join("")
      : `<div><span>状态</span><strong>暂无风格画像</strong></div>`;
  }

  renderCardList("#style-distill-list", styleDistillations, (item) => `
    <article class="glass-card list-card">
      <div class="list-card-header">
        <div>
          <div class="card-heading">蒸馏任务</div>
          <div class="card-title">${escapeHtml(item.target_skill_name || item.summary?.target_skill_name || item.job_id || "unknown")}</div>
        </div>
        <span class="pill">${escapeHtml(item.qa?.status || item.summary?.qa_status || item.status || "unknown")}</span>
      </div>
      <div class="list-card-body">
        <div><span>地址</span><strong>${escapeHtml(item.wallet || item.summary?.wallet || "unknown")}</strong></div>
        <div><span>链</span><strong>${escapeHtml(item.chain || item.summary?.chain || "unknown")}</strong></div>
        <div><span>Backend</span><strong>${escapeHtml(item.review_backend || item.summary?.review_backend || item.reflection?.review_backend || "unknown")}</strong></div>
        <div><span>Reflection</span><strong>${escapeHtml(item.reflection_status || item.summary?.reflection_status || item.reflection?.reflection_status || "unknown")}${item.fallback_used || item.summary?.fallback_used ? " / fallback" : ""}</strong></div>
        <div><span>执行就绪</span><strong>${escapeHtml(item.execution_readiness || item.summary?.execution_readiness || "unknown")}</strong></div>
        <div><span>示例就绪</span><strong>${escapeHtml(item.example_readiness || item.summary?.example_readiness || "unknown")}</strong></div>
        <div><span>候选</span><strong>${escapeHtml(item.candidate?.candidate_id || item.summary?.candidate_id || "unknown")}</strong></div>
        <div><span>晋升</span><strong>${escapeHtml(item.promotion?.promotion_id || item.summary?.promotion_id || "unknown")}</strong></div>
        <div><span>时间</span><strong>${escapeHtml(formatTimestamp(item.created_at || item.summary?.created_at))}</strong></div>
      </div>
      <div class="list-card-footer">
        <span>${escapeHtml(item.profile?.summary || item.summary?.summary || item.summary || "暂无摘要")}</span>
      </div>
    </article>
  `, "当前还没有地址蒸馏任务。");

  renderThoughtProcess(latestStyleDistillation);
}

function renderThoughtProcess(distill) {
  const thoughtBox = $("#style-distill-thought-process");
  const traceBox = $("#style-distill-trace");
  if (!thoughtBox || !traceBox) return;

  if (!distill) {
    thoughtBox.innerHTML = '<div class="empty-state">等待蒸馏任务启动...</div>';
    traceBox.innerHTML = '<div class="empty-state">暂无执行轨迹</div>';
    return;
  }

  // 1. 渲染执行轨迹 (Trace)
  const trace = distill.trace || distill.summary?.trace || [];
  if (trace.length > 0) {
    traceBox.innerHTML = trace.map((event, idx) => {
      const type = event.event_type || event.type || "event";
      const message = event.message || event.text || JSON.stringify(event.payload || event);
      return `
        <div class="thought-step" style="animation-delay: ${idx * 0.05}s">
          <div class="thought-header">
            <span>${escapeHtml(type)}</span>
            <span>${escapeHtml(formatTimestamp(event.timestamp || event.created_at))}</span>
          </div>
          <div class="thought-content">${escapeHtml(message)}</div>
        </div>
      `;
    }).join("");
  } else {
    traceBox.innerHTML = '<div class="empty-state">当前任务尚无轨迹记录</div>';
  }

  // 2. 渲染深度思考过程 (Reflection Events / Invocations)
  // 结合 reflection 数据中的 events
  const events = distill.reflection?.events || distill.summary?.reflection?.events || distill.events || [];
  
  if (events.length === 0) {
    thoughtBox.innerHTML = '<div class="empty-state">尚未捕获到 Agent 思考细节</div>';
    return;
  }

  thoughtBox.innerHTML = events.map((event, idx) => {
    let typeClass = "is-thought";
    let title = "Agent 思考";
    let content = "";
    let meta = "";

    const type = (event.event_type || event.type || "").toLowerCase();
    
    if (type.includes("thought") || type.includes("reflection")) {
      typeClass = "is-thought";
      title = "THOUGHT";
      content = event.message || event.payload?.thought || "";
    } else if (type.includes("call") || type.includes("invoke")) {
      typeClass = "is-call";
      title = "TOOL CALL";
      content = `${event.payload?.tool_name || "unknown_tool"}(${JSON.stringify(event.payload?.args || {})})`;
      meta = `Call ID: ${event.payload?.tool_call_id || "n/a"}`;
    } else if (type.includes("result") || type.includes("response")) {
      typeClass = "is-result";
      title = "TOOL RESULT";
      content = JSON.stringify(event.payload?.result || event.payload || {}, null, 2);
    } else {
      title = type.toUpperCase();
      content = event.message || JSON.stringify(event.payload || event);
    }

    if (!content && event.message) content = event.message;

    return `
      <div class="thought-step ${typeClass}" style="animation-delay: ${idx * 0.1}s">
        <div class="thought-header">
          <span>${escapeHtml(title)}</span>
          <span>${escapeHtml(formatTimestamp(event.timestamp || event.created_at))}</span>
        </div>
        <div class="thought-content ${content.length > 200 ? "is-collapsed" : ""}" 
             onclick="this.classList.toggle('is-collapsed')">
          ${escapeHtml(content)}
        </div>
        ${meta ? `<div class="thought-meta">${escapeHtml(meta)}</div>` : ""}
      </div>
    `;
  }).join("");
}
