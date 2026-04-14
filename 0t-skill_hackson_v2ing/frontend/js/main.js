import { createStyleDistillation, fetchStyleDistillationJob, getJson, fetchWorkspaces, resumeStyleDistillation } from "./api.js";
import { $, escapeHtml } from "./utils.js";
import { state, updateWorkspaceDir } from "./state.js";
import * as renderer from "./renderer.js";

async function initWorkspaces() {
  const select = $("#workspace-select");
  if (!select) return;

  const { items } = await fetchWorkspaces();
  select.innerHTML = items
    .map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || item.id)}</option>`)
    .join("");
  
  // 恢复之前的选择（如果有），否则如果只有一个则默认选中第一个
  if (state.workspaceDir && items.some(i => i.id === state.workspaceDir)) {
    select.value = state.workspaceDir;
  } else if (items.length > 0) {
    select.value = items[0].id;
    updateWorkspaceDir(items[0].id);
  }
}

async function loadDashboard() {
  const select = $("#workspace-select");
  updateWorkspaceDir(select?.value);
  
  state.loading = true;
  const status = $("#dashboard-status");
  if (status) {
    status.textContent = "正在同步工作区数据...";
  }
  
  try {
    const payload = await getJson(`/api/overview?workspace_dir=${encodeURIComponent(state.workspaceDir)}`);
    state.overview = payload;
    state.runtimes = payload.runtimes || null;
    state.runtime = payload.runtime || null;
    state.sessions = payload.sessions || null;
    state.activeRuns = payload.active_runs || null;
    state.evaluations = payload.evaluations || null;
    state.candidates = payload.candidates || null;
    state.promotions = payload.promotions || null;
    state.candidateSurface = payload.candidate_surface || null;
    state.evolution = payload.evolution || null;
    state.styleDistillations = payload.style_distillations || null;
    state.latestStyleDistillation = payload.style_distillations?.latest || null;
    state.error = null;
    
    renderer.renderDashboard();
    
    if (status) {
      status.textContent = `当前: ${state.workspaceDir}`;
    }
  } catch (error) {
    state.error = error;
    if (status) {
      status.innerHTML = `<span class="pill pill-danger">同步失败</span> <span>${escapeHtml(error.message)}</span>`;
    }
  } finally {
    state.loading = false;
  }
}

async function handleStyleDistillationSubmit(event) {
  event.preventDefault();
  const wallet = $("#distill-wallet")?.value.trim() || "";
  const chain = $("#distill-chain")?.value.trim() || "";
  const jobId = $("#distill-job-id")?.value.trim() || "";
  const skillName = $("#distill-skill-name")?.value.trim() || "";
  const extractorPrompt = $("#distill-prompt")?.value.trim() || "";
  const liveExecute = Boolean($("#distill-live-execute")?.checked);
  const approvalGranted = Boolean($("#distill-approval-granted")?.checked);
  const status = $("#distill-status");
  const submit = $("#distill-submit");

  if (!wallet && !jobId) {
    if (status) {
      status.innerHTML = `<span class="pill pill-danger">缺少参数</span> <span>新建蒸馏需要地址，恢复或查询需要 Job ID。</span>`;
    }
    return;
  }

  submit?.classList.add("is-loading");
  if (status) {
    status.innerHTML = `<span class="pill">处理中</span> <span>${jobId ? "正在查询或恢复既有 job，并按需要推进到 build / execution 阶段。" : "正在拉取钱包数据，先通过 Pi reflection agent 做风格自省，再生成 skill。"}</span>`;
  }

  try {
    let result;
    if (jobId && !wallet && !liveExecute && !approvalGranted) {
      result = await fetchStyleDistillationJob(state.workspaceDir, jobId);
    } else if (jobId) {
      result = await resumeStyleDistillation(state.workspaceDir, {
        job_id: jobId,
        live_execute: liveExecute,
        approval_granted: approvalGranted,
      });
    } else {
      result = await createStyleDistillation(state.workspaceDir, {
        wallet,
        chain: chain || null,
        skill_name: skillName || null,
        extractor_prompt: extractorPrompt || null,
        live_execute: liveExecute,
        approval_granted: approvalGranted,
      });
    }
    state.latestStyleDistillation = result;
    await loadDashboard();
    state.latestStyleDistillation = result;
    renderer.renderDashboard();
    renderer.activateSection("style-distill");
    if (status) {
      const backend = result.review_backend || result.summary?.review_backend || "wallet-style";
      const readiness = result.execution_readiness || result.summary?.execution_readiness || "n/a";
      status.innerHTML = `<span class="pill">完成</span> <span>${escapeHtml(result.profile?.summary || result.summary?.summary || "已完成地址风格蒸馏。")} · backend: ${escapeHtml(backend)} · execution: ${escapeHtml(readiness)}</span>`;
    }
  } catch (error) {
    if (status) {
      status.innerHTML = `<span class="pill pill-danger">失败</span> <span>${escapeHtml(error.message)}</span>`;
    }
  } finally {
    submit?.classList.remove("is-loading");
  }
}

window.app = {
  refresh: loadDashboard,
  setSection(section) {
    renderer.activateSection(section);
  },
};

document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".nav-item[data-target]").forEach((item) => {
    item.addEventListener("click", () => {
      renderer.activateSection(item.dataset.target);
      document.querySelectorAll(".nav-item[data-target]").forEach((nav) => nav.classList.remove("is-active"));
      item.classList.add("is-active");
    });
  });

  // 初始化工作区列表
  await initWorkspaces();

  $("#workspace-select")?.addEventListener("change", loadDashboard);
  $("#refresh-dashboard")?.addEventListener("click", loadDashboard);
  $("#style-distill-form")?.addEventListener("submit", handleStyleDistillationSubmit);

  // 初次加载
  loadDashboard();
});
