"use strict";
const form = document.querySelector("#command-form");
const input = document.querySelector("#command");
const send = document.querySelector("#send");
const refresh = document.querySelector("#refresh");
const retry = document.querySelector("#retry");
const notice = document.querySelector("#notice");
const transcript = document.querySelector("#transcript");
const pendingKey = "badbadger.pending-turn.v1";
let revision = null;
let pending = null;
let busy = false;
let storageAvailable = true;
try { pending = JSON.parse(localStorage.getItem(pendingKey) || "null"); }
catch (_) { storageAvailable = false; }

function controls() {
  input.disabled = busy || pending !== null || revision === null || !storageAvailable;
  send.disabled = input.disabled;
  refresh.disabled = busy;
  retry.hidden = pending === null;
  retry.disabled = busy;
}
function message(text, player = false) {
  const p = document.createElement("p");
  p.className = player ? "message player" : "message";
  p.textContent = player ? `You: ${text}` : text;
  transcript.append(p);
}
async function request(path, options = {}) {
  const response = await fetch(path, { ...options, credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(90000) });
  if (!response.ok) {
    const error = new Error(response.status === 401 ? "Sign in again, then refresh this page." : "The request could not be completed.");
    error.status = response.status;
    throw error;
  }
  return response.json();
}
async function load() {
  const view = await request("/api/game");
  revision = view.revision;
  document.querySelector("#dialogue-mode").textContent = view.dialogue_mode === "ai"
    ? "AI dialogue is configured." : "Offline dialogue: try the commands in help.";
  document.querySelector("#status").textContent = view.status.join("\n");
  transcript.replaceChildren();
  view.opening.forEach(text => message(text));
  view.transcript.forEach(turn => {
    message(turn.command, true);
    turn.messages.forEach(text => message(text));
  });
  transcript.scrollTop = transcript.scrollHeight;
}
function clearPending() {
  localStorage.removeItem(pendingKey);
  pending = null;
}
async function submitPending() {
  if (busy || !pending) return;
  busy = true;
  controls();
  notice.textContent = "Working…";
  try {
    await request("/api/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-BadBadger-Request": "turn" },
      body: JSON.stringify(pending),
    });
    clearPending();
    input.value = "";
    await load();
    notice.textContent = "Saved.";
  } catch (error) {
    if ([400, 409, 413, 422].includes(error.status)) {
      input.value = pending ? pending.command : input.value;
      clearPending();
      notice.textContent = "Your turn was not submitted. Refresh the saved game, review it, then send again.";
      try { await load(); } catch (_) { /* The manual refresh remains available. */ }
    } else {
      notice.textContent = pending
        ? "Connection interrupted. Retry the pending turn; it will not run twice."
        : "Your turn was saved. Refresh to load the latest story.";
    }
  } finally {
    busy = false;
    controls();
    if (!input.disabled) input.focus();
  }
}
form.addEventListener("submit", event => {
  event.preventDefault();
  if (busy || pending || revision === null || !input.value.trim()) return;
  const turn = { request_id: crypto.randomUUID(), command: input.value, expected_revision: revision };
  try { localStorage.setItem(pendingKey, JSON.stringify(turn)); }
  catch (_) {
    storageAvailable = false;
    notice.textContent = "Allow browser storage for this site to submit turns safely.";
    controls();
    return;
  }
  pending = turn;
  submitPending();
});
retry.addEventListener("click", submitPending);
refresh.addEventListener("click", async () => {
  busy = true;
  controls();
  try {
    await load();
    notice.textContent = pending ? "A turn is pending. Retry it to confirm the result." : "Saved game loaded.";
  } catch (error) { notice.textContent = error.message; }
  finally { busy = false; controls(); }
});
(async () => {
  try {
    await load();
    notice.textContent = !storageAvailable ? "Allow browser storage for this site to submit turns safely."
      : pending ? "A turn is pending. Retry it to confirm the result." : "Your saved game is ready. Type help for ideas.";
  } catch (error) { notice.textContent = error.message; }
  controls();
})();
