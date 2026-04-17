export async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

export async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.message || `Request failed: ${response.status}`);
  }
  return body;
}

export async function fetchWorkspaces() {
  try {
    const response = await fetch("/api/workspaces");
    if (!response.ok) throw new Error("Fallback to default");
    return await response.json();
  } catch (e) {
    // 降级逻辑：如果后端还没实现，返回默认列表
    return { items: [{ id: ".ot-workspace", name: "默认工作区 (.ot-workspace)" }] };
  }
}

export async function fetchEvaluations(workspaceDir) {
  return getJson(`/api/evaluations?workspace_dir=${encodeURIComponent(workspaceDir)}`);
}

export async function fetchCandidates(workspaceDir) {
  return getJson(`/api/candidates?workspace_dir=${encodeURIComponent(workspaceDir)}`);
}

export async function fetchPromotions(workspaceDir) {
  return getJson(`/api/promotions?workspace_dir=${encodeURIComponent(workspaceDir)}`);
}

export async function fetchCandidateSurface(workspaceDir) {
  return getJson(`/api/candidate-surface?workspace_dir=${encodeURIComponent(workspaceDir)}`);
}

export async function fetchStyleDistillations(workspaceDir) {
  return getJson(`/api/style-distillations?workspace_dir=${encodeURIComponent(workspaceDir)}`);
}

export async function fetchStyleDistillationJob(workspaceDir, jobId) {
  return getJson(`/api/style-distillations?workspace_dir=${encodeURIComponent(workspaceDir)}&job_id=${encodeURIComponent(jobId)}`);
}

export async function createStyleDistillation(workspaceDir, payload) {
  return postJson(`/api/style-distillations?workspace_dir=${encodeURIComponent(workspaceDir)}`, payload);
}

export async function resumeStyleDistillation(workspaceDir, payload) {
  return postJson(`/api/style-distillations?workspace_dir=${encodeURIComponent(workspaceDir)}`, payload);
}

export function buildRequestId(presetId) {
  const suffix = Math.random().toString(36).slice(2, 8);
  return `ui-${presetId}-${suffix}`;
}
