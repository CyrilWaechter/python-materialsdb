// materialsdb settings page.
// Placeholder shell shipped with Task 6; Task 7 replaces this file with the
// general/colour-scheme/palette logic. It only defines the shared token and
// API helper so the page loads and the static route is testable.
const TOKEN = window.MATERIALSDB_TOKEN;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-MaterialsDB-Token": TOKEN },
  });
  const type = response.headers.get("Content-Type") || "";
  if (!response.ok) {
    const message = type.includes("json") ? (await response.json()).error : response.statusText;
    throw new Error(message);
  }
  return type.includes("json") ? response.json() : response.blob();
}
