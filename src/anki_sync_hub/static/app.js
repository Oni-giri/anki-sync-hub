const views = {
  setup: document.querySelector("#setup-view"),
  login: document.querySelector("#login-view"),
  dashboard: document.querySelector("#dashboard-view"),
};
const message = document.querySelector("#message");
const syncUserForm = document.querySelector("#sync-user-form");
const syncUserMessage = document.querySelector("#sync-user-message");
const mcpTokenForm = document.querySelector("#mcp-token-form");
const mcpGuide = document.querySelector("#mcp-guide");
const mcpGuideMessage = document.querySelector("#mcp-guide-message");
let latestMcpToken = "YOUR_TOKEN";

function show(name) {
  Object.entries(views).forEach(([key, view]) => view.classList.toggle("hidden", key !== name));
}

function say(text = "") { message.textContent = text; }

function saySyncUser(text = "", tone = "error") {
  syncUserMessage.textContent = text;
  syncUserMessage.className = `form-message ${tone}`;
}

function sayMcpGuide(text = "", tone = "success") {
  mcpGuideMessage.textContent = text;
  mcpGuideMessage.className = `form-message ${tone}`;
}

function updateMcpGuide() {
  const endpoint = `${window.location.origin}/mcp`;
  const authorization = `Bearer ${latestMcpToken}`;
  document.querySelector("#mcp-endpoint").textContent = endpoint;
  document.querySelector("#mcp-auth-header").textContent = authorization;
  document.querySelector("#mcp-config").textContent = JSON.stringify({
    mcpServers: {
      "anki-sync-hub": {
        type: "streamable-http",
        url: endpoint,
        headers: { Authorization: authorization },
      },
    },
  }, null, 2);
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch (_) { /* Fall back for plain HTTP on a private network. */ }
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.append(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("Copy failed. Select the value and copy it manually.");
}

function openMcpGuide() {
  updateMcpGuide();
  sayMcpGuide();
  document.body.classList.add("drawer-open");
  if (typeof mcpGuide.showModal === "function") mcpGuide.showModal();
  else mcpGuide.setAttribute("open", "");
}

function closeMcpGuide() {
  document.body.classList.remove("drawer-open");
  if (typeof mcpGuide.close === "function") mcpGuide.close();
  else mcpGuide.removeAttribute("open");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let body = {};
  try { body = await response.json(); } catch (_) { /* no JSON body */ }
  if (!response.ok) {
    const error = new Error(body.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function bytes(value) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let number = Number(value || 0);
  let index = 0;
  while (number >= 1024 && index < units.length - 1) { number /= 1024; index += 1; }
  return `${number.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function requestTotal(metrics) {
  return metrics.reduce((total, metric) => total + Number(metric.requests || 0), 0);
}

function renderUsers(users) {
  const list = document.querySelector("#users");
  if (!users.length) {
    list.innerHTML = '<p class="muted">No sync accounts yet. Add the first one to start the sync engine.</p>';
    return;
  }
  list.replaceChildren(...users.map((user) => {
    const row = document.createElement("div");
    row.className = "user-row";
    const label = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = user.username;
    const state = document.createElement("small");
    state.textContent = user.enabled ? "Enabled" : "Disabled";
    label.append(name, state);
    const button = document.createElement("button");
    button.className = "secondary";
    button.textContent = user.enabled ? "Disable" : "Enable";
    button.addEventListener("click", async () => {
      try {
        await api(`/api/sync-users/${user.id}/enabled`, {
          method: "PUT", body: JSON.stringify({ enabled: !user.enabled }),
        });
        await loadDashboard();
      } catch (error) { say(error.message); }
    });
    row.append(label, button);
    return row;
  }));
}

function renderTokens(tokens) {
  const list = document.querySelector("#tokens");
  if (!tokens.length) {
    list.innerHTML = '<p class="muted">No MCP tokens have been created.</p>';
    return;
  }
  list.replaceChildren(...tokens.map((token) => {
    const row = document.createElement("div");
    row.className = "user-row";
    const label = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = token.name;
    const detail = document.createElement("small");
    detail.textContent = `${token.username} · ${token.token_prefix}… · ${token.scopes}`;
    label.append(name, detail);
    const button = document.createElement("button");
    button.className = "secondary";
    button.textContent = token.revoked_at ? "Revoked" : "Revoke";
    button.disabled = Boolean(token.revoked_at);
    button.addEventListener("click", async () => {
      try { await api(`/api/mcp-tokens/${token.id}`, { method: "DELETE", body: "{}" }); await loadDashboard(); }
      catch (error) { say(error.message); }
    });
    row.append(label, button);
    return row;
  }));
}

function renderTokenUsers(users) {
  const select = document.querySelector("#token-user");
  const activeUsers = users.filter((user) => user.enabled);
  select.replaceChildren(...activeUsers.map((user) => {
    const option = document.createElement("option");
    option.value = user.id;
    option.textContent = user.username;
    return option;
  }));
  select.disabled = !activeUsers.length;
  mcpTokenForm.querySelector('button[type="submit"]').disabled = !activeUsers.length;
}

async function loadDashboard() {
  try {
    const [status, tokens] = await Promise.all([api("/api/status"), api("/api/mcp-tokens")]);
    show("dashboard");
    document.querySelector("#sync-state").textContent = status.syncService;
    document.querySelector("#user-count").textContent = status.syncUsers.length;
    document.querySelector("#sync-storage").textContent = bytes(status.storage.syncBytes);
    document.querySelector("#request-count").textContent = requestTotal(status.metrics);
    document.querySelector("#server-url").textContent = `${window.location.origin}/`;
    updateMcpGuide();
    const pill = document.querySelector("#service-pill");
    pill.textContent = status.syncService;
    pill.className = `pill ${status.syncService === "running" ? "good" : "waiting"}`;
    renderUsers(status.syncUsers);
    renderTokenUsers(status.syncUsers);
    renderTokens(tokens);
    say();
  } catch (error) {
    if (error.status === 401) show("login");
    else say(error.message);
  }
}

async function boot() {
  try {
    const setup = await api("/api/setup/status");
    if (!setup.configured) show("setup");
    else await loadDashboard();
  } catch (error) { say(error.message); }
}

document.querySelector("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault(); say("Initializing…");
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try { await api("/api/setup", { method: "POST", body: JSON.stringify(data) }); await loadDashboard(); }
  catch (error) { say(error.message); }
});

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault(); say("Signing in…");
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try { await api("/api/login", { method: "POST", body: JSON.stringify(data) }); await loadDashboard(); }
  catch (error) { say(error.message); }
});

syncUserForm.addEventListener("input", () => saySyncUser());
syncUserForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (form.dataset.submitting === "true") return;
  form.dataset.submitting = "true";
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  saySyncUser("Creating sync account…", "progress");
  const data = Object.fromEntries(new FormData(form));
  try {
    const user = await api("/api/sync-users", { method: "POST", body: JSON.stringify(data) });
    form.reset();
    await loadDashboard();
    saySyncUser(`Sync account “${user.username}” created.`, "success");
  } catch (error) {
    if (error.status === 409) {
      await loadDashboard();
      saySyncUser("That sync username already exists. It is listed in Sync accounts above.");
    } else {
      saySyncUser(error.message);
    }
  } finally {
    delete form.dataset.submitting;
    submit.disabled = false;
  }
});

mcpTokenForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (mcpTokenForm.dataset.submitting === "true") return;
  mcpTokenForm.dataset.submitting = "true";
  const submit = mcpTokenForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  say("Creating MCP token…");
  const form = new FormData(event.currentTarget);
  const scopes = ["read"];
  if (form.get("write")) scopes.push("write");
  try {
    const result = await api("/api/mcp-tokens", {
      method: "POST",
      body: JSON.stringify({ sync_user_id: Number(form.get("sync_user_id")), name: form.get("name"), scopes }),
    });
    const output = document.querySelector("#token-result");
    output.classList.remove("hidden");
    output.innerHTML = "<strong>Copy this token now:</strong><br>";
    const code = document.createElement("code");
    code.textContent = result.token;
    const actions = document.createElement("div");
    actions.className = "token-result-actions";
    const copyToken = document.createElement("button");
    copyToken.className = "secondary";
    copyToken.type = "button";
    copyToken.textContent = "Copy token";
    copyToken.addEventListener("click", async () => {
      try {
        await copyText(result.token);
        copyToken.textContent = "Copied";
        window.setTimeout(() => { copyToken.textContent = "Copy token"; }, 1500);
      } catch (error) { say(error.message); }
    });
    const useToken = document.createElement("button");
    useToken.className = "secondary";
    useToken.type = "button";
    useToken.textContent = "Open connection guide";
    useToken.addEventListener("click", openMcpGuide);
    actions.append(copyToken, useToken);
    output.append(code, actions);
    latestMcpToken = result.token;
    updateMcpGuide();
    event.currentTarget.reset();
    await loadDashboard();
  } catch (error) { say(error.message); }
  finally {
    delete mcpTokenForm.dataset.submitting;
    submit.disabled = false;
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST", body: "{}" }); show("login"); }
  catch (error) { say(error.message); }
});
document.querySelector("#refresh-button").addEventListener("click", loadDashboard);
document.querySelector("#copy-url").addEventListener("click", async () => {
  try {
    await copyText(`${window.location.origin}/`);
    say("Server address copied.");
  } catch (error) { say(error.message); }
});
document.querySelector("#open-mcp-guide").addEventListener("click", openMcpGuide);
document.querySelector("#close-mcp-guide").addEventListener("click", closeMcpGuide);
document.querySelector("#copy-mcp-endpoint").addEventListener("click", async () => {
  try {
    await copyText(document.querySelector("#mcp-endpoint").textContent);
    sayMcpGuide("Endpoint copied.");
  } catch (error) { sayMcpGuide(error.message, "error"); }
});
document.querySelector("#copy-mcp-header").addEventListener("click", async () => {
  try {
    await copyText(document.querySelector("#mcp-auth-header").textContent);
    sayMcpGuide("Authorization header copied.");
  } catch (error) { sayMcpGuide(error.message, "error"); }
});
document.querySelector("#copy-mcp-config").addEventListener("click", async () => {
  try {
    await copyText(document.querySelector("#mcp-config").textContent);
    sayMcpGuide("Connection JSON copied.");
  } catch (error) { sayMcpGuide(error.message, "error"); }
});
mcpGuide.addEventListener("click", (event) => {
  if (event.target === mcpGuide) closeMcpGuide();
});
mcpGuide.addEventListener("close", () => document.body.classList.remove("drawer-open"));

updateMcpGuide();
boot();
