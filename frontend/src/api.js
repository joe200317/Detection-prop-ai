export const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export function mediaUrl(path) {
  if (!path) {
    return "";
  }
  if (path.startsWith("http")) {
    return path;
  }
  return `${API_BASE}${path}`;
}

function detailMessage(detail) {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || "Invalid value").join(" ");
  }
  return "Request failed";
}

async function parseResponse(response) {
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(detailMessage(data?.detail));
  }
  return data;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new Error("API is not running. Start the backend.");
  }
  return parseResponse(response);
}

async function requestForm(path, formData) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { method: "POST", body: formData });
  } catch {
    throw new Error("API is not running. Start the backend.");
  }
  return parseResponse(response);
}

function queryString(params) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      search.set(key, String(value));
    }
  });
  const query = search.toString();
  return query ? `?${query}` : "";
}

export function listItems(params = {}) {
  return request(`/api/inventory${queryString(params)}`);
}

export function createItem(payload) {
  return request("/api/inventory", { method: "POST", body: JSON.stringify(payload) });
}

export function updateItem(inventoryId, payload) {
  return request(`/api/inventory/${inventoryId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function getHealth() {
  return request("/api/health");
}

export function listImages(inventoryId) {
  return request(`/api/inventory/${inventoryId}/images`);
}

export function uploadInventoryImage(inventoryId, file) {
  const body = new FormData();
  body.append("file", file);
  return requestForm(`/api/inventory/${inventoryId}/images`, body);
}

export function deleteInventoryImage(inventoryId, imageId) {
  return request(`/api/inventory/${inventoryId}/images/${imageId}`, { method: "DELETE" });
}

export function listThemes() {
  return request("/api/themes?limit=100");
}

export function createTheme(payload) {
  return request("/api/themes", { method: "POST", body: JSON.stringify(payload) });
}

export function getTheme(themeId) {
  return request(`/api/themes/${themeId}`);
}

export function uploadMainImage(themeId, file) {
  const body = new FormData();
  body.append("file", file);
  return requestForm(`/api/themes/${themeId}/main-image`, body);
}

export function uploadProp(themeId, file) {
  const body = new FormData();
  body.append("file", file);
  return requestForm(`/api/themes/${themeId}/props`, body);
}

export function scanTheme(themeId) {
  return request(`/api/themes/${themeId}/scan`, { method: "POST" });
}

export function analyzeTheme(themeId) {
  return request(`/api/themes/${themeId}/analyze`, { method: "POST" });
}

export function retryTheme(themeId, payload) {
  return request(`/api/themes/${themeId}/retry`, { method: "POST", body: JSON.stringify(payload) });
}

export function getJob(jobId) {
  return request(`/api/jobs/${jobId}`);
}

export function getResult(themeId) {
  return request(`/api/themes/${themeId}/result`);
}

export function getLogs(themeId) {
  return request(`/api/themes/${themeId}/logs`);
}

export function getReview() {
  return request("/api/review");
}

export function reviewProp(themeId, propId, payload) {
  return request(`/api/themes/${themeId}/props/${propId}/review`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
