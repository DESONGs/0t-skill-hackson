export function $(selector) {
  return document.querySelector(selector);
}

export function escapeHtml(value) {
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

export function deepClone(value) {
  return JSON.parse(JSON.stringify(value ?? {}));
}

export function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

export function normalizeText(value) {
  return String(value ?? "").trim().toLowerCase().replace(/\s+/g, " ");
}

export function containsAny(text, keywords) {
  return keywords.some((keyword) => text.includes(keyword));
}
