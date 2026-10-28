"use strict";
const form = document.querySelector("#command-form");
const input = document.querySelector("#command");
const send = document.querySelector("#send");
const refresh = document.querySelector("#refresh");
const retry = document.querySelector("#retry");
const newGame = document.querySelector("#new-game");
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
  retry.textContent = pending && pending.operation === "new-game" ? "Retry new game" : "Retry pending turn";
  newGame.disabled = input.disabled;
  document.querySelectorAll("[data-command]").forEach(button => { button.disabled = input.disabled; });
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
  const guidance = view.guidance;
  if (guidance) {
    document.querySelector("#objective").textContent = `Objective: ${guidance.objective}`;
    document.querySelector("#phase").textContent = guidance.message;
    const recap = document.querySelector("#recap");
    recap.hidden = guidance.recap.length === 0;
    recap.textContent = guidance.recap.join("\n");
    const suggestions = document.querySelector("#suggestions");
    suggestions.replaceChildren();
    guidance.suggestions.forEach(suggestion => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = suggestion.label;
      button.dataset.command = suggestion.command;
      button.dataset.consequential = String(suggestion.consequential);
      suggestions.append(button);
    });
  }
  transcript.replaceChildren();
  // Title and premise belong in the story; current objective, timing and
  // surroundings already have persistent panels above it.
  view.opening.slice(0, 2).forEach(text => message(text));
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
  const startingNewGame = pending.operation === "new-game";
  const { operation, ...payload } = pending;
  try {
    await request(startingNewGame ? "/api/new-game" : "/api/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-BadBadger-Request": "turn" },
      body: JSON.stringify(payload),
    });
    clearPending();
    input.value = "";
    await load();
    notice.textContent = startingNewGame ? "New game started. Your previous playthrough is archived." : "Saved.";
  } catch (error) {
    if ([400, 409, 413, 422].includes(error.status)) {
      input.value = pending ? (pending.command || "") : input.value;
      clearPending();
      notice.textContent = startingNewGame
        ? "The game changed. Review the saved game before starting over."
        : "Your turn was not submitted. Refresh the saved game, review it, then send again.";
      try { await load(); } catch (_) { /* The manual refresh remains available. */ }
    } else {
      notice.textContent = pending
        ? "Connection interrupted. Retry the pending request; it will not run twice."
        : "Your turn was saved. Refresh to load the latest story.";
    }
  } finally {
    busy = false;
    controls();
    if (!input.disabled) input.focus();
  }
}
function persistAndSubmit(payload) {
  try { localStorage.setItem(pendingKey, JSON.stringify(payload)); }
  catch (_) {
    storageAvailable = false;
    notice.textContent = "Allow browser storage for this site to submit turns safely.";
    controls();
    return;
  }
  pending = payload;
  submitPending();
}
form.addEventListener("submit", event => {
  event.preventDefault();
  if (busy || pending || revision === null || !input.value.trim()) return;
  persistAndSubmit({ request_id: crypto.randomUUID(), command: input.value, expected_revision: revision });
});
newGame.addEventListener("click", () => {
  if (busy || pending || revision === null || !storageAvailable) return;
  if (!window.confirm("Start a new Relay Crisis game? Your current playthrough will be archived on the server. The new game begins at the start of the crisis.")) return;
  persistAndSubmit({ operation: "new-game", request_id: crypto.randomUUID(), expected_revision: revision, confirmed: true });
});
document.addEventListener("click", event => {
  const button = event.target.closest("button[data-command]");
  if (!button || input.disabled) return;
  input.value = button.dataset.command;
  input.focus();
  notice.textContent = button.dataset.consequential === "true"
    ? "This request is irreversible if accepted. Review it, then Send if you want to commit."
    : "Command selected. Review it, then Send.";
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
