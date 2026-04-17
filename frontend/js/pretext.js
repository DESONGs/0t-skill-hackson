/**
 * Pretext Core Engine (AI-Optimized)
 * 高性能文字测量与非 DOM 布局驱动器
 */
export const Pretext = {
  canvas: document.createElement('canvas'),
  ctx: null,
  
  init() {
    this.ctx = this.canvas.getContext('2d');
    console.log("✦ Pretext Engine: High-Frequency DOM Strategy Active");
  },

  measure(text, font = "14px Inter") {
    if (!this.ctx) this.init();
    this.ctx.font = font;
    return this.ctx.measureText(text).width;
  },

  /**
   * 碎片化调度渲染器：针对高频更新的 UI 组件（如 Intent Chips）
   * 采用 DocumentFragment 批量写入，并利用 CSS 变量实现阶梯动效
   */
  revealFragments(container, fragments, className = "intent-chip") {
    const fragment = document.createDocumentFragment();
    
    // 1. 批量创建节点 (离线)
    fragments.forEach((content, index) => {
      const el = document.createElement('div');
      el.className = className;
      el.style.setProperty('--delay', `${index * 0.05}s`);
      el.innerHTML = content;
      fragment.appendChild(el);
    });

    // 2. 调度写入 (单次 Reflow)
    requestAnimationFrame(() => {
      container.innerHTML = "";
      container.appendChild(fragment);
    });
  },

  /**
   * 针对 Markdown 报告的阶梯式流渲染
   */
  async streamRender(container, md, options = {}) {
    // ... 原有逻辑优化
    const lines = md.split('\n').filter(l => l.trim());
    container.innerHTML = "";
    
    lines.forEach((line, index) => {
      const lineEl = document.createElement('div');
      lineEl.className = "stagger-row";
      lineEl.style.setProperty('--delay', `${index * 0.04}s`);
      lineEl.innerHTML = line;
      container.appendChild(lineEl);
    });
  }
};
