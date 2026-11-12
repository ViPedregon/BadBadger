"use strict";
const playerScope = null;
const form = document.querySelector("#command-form");
const input = document.querySelector("#command");
const send = document.querySelector("#send");
const refresh = document.querySelector("#refresh");
const retry = document.querySelector("#retry");
const newGame = document.querySelector("#new-game");
const chapterSelection = document.querySelector("#chapter-selection");
let loadedScenario = null;
const notice = document.querySelector("#notice");
const transcript = document.querySelector("#transcript");
const pendingKey = "badbadger.pending-turn.v1" + (playerScope ? `.${playerScope}` : "");
let revision = null;
let pending = null;
let busy = false;
let storageAvailable = true;
let selectedCharacter = null;
let characterProfiles = [];
try { pending = JSON.parse(localStorage.getItem(pendingKey) || "null"); }
catch (_) { storageAvailable = false; }

function controls() {
  input.disabled = busy || pending !== null || revision === null || !storageAvailable;
  send.disabled = input.disabled;
  refresh.disabled = busy;
  retry.hidden = pending === null;
  retry.disabled = busy;
  retry.textContent = pending && pending.operation === "resume" ? "Retry resume" : pending && pending.operation === "new-game" ? "Retry new game" : "Retry pending turn";
  document.querySelectorAll("[data-archive]").forEach(button => { button.disabled = input.disabled; });
  newGame.disabled = input.disabled;
  chapterSelection.disabled = input.disabled;
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
  if (view.scenario) {
    document.title = `BadBadger · ${view.scenario.title}`;
    document.querySelector("h1").textContent = view.scenario.title;
    document.querySelector("header p:last-child").textContent = view.scenario.tagline;
    if (loadedScenario !== view.scenario.key) {
      chapterSelection.replaceChildren();
      view.chapters.forEach(chapter => {
        const option = document.createElement("option");
        option.value = chapter.key;
        option.textContent = chapter.title;
        chapterSelection.append(option);
      });
      chapterSelection.value = view.scenario.key;
      loadedScenario = view.scenario.key;
    }
  }
  document.querySelector("#dialogue-mode").textContent = view.dialogue_mode === "ai"
    ? "AI dialogue is configured." : "Offline dialogue: try the commands in help.";
  document.querySelector("#status").textContent = view.status.join("\n");
  characterProfiles = view.characters || [];
  if (!characterProfiles.some(character => character.id === selectedCharacter)) {
    selectedCharacter = characterProfiles[0]?.id || null;
  }
  renderCharacters();
  const guidance = view.guidance;
  if (guidance) {
    document.querySelector("#objective").textContent = `Objective: ${guidance.objective}`;
    document.querySelector("#phase").textContent = guidance.message;
    const stages = document.querySelector("#objective-stages");
    stages.replaceChildren();
    (guidance.stages || []).forEach(stage => {
      const item = document.createElement("li");
      item.textContent = `${stage.label}${stage.optional ? " (optional)" : ""}: ${stage.status}`;
      stages.appendChild(item);
    });
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
  const archives = document.querySelector("#archives");
  archives.replaceChildren();
  (view.archives || []).forEach(archive => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.archive = archive.id;
    button.textContent = `Resume ${archive.title} · ${archive.saved_at} · save ${archive.id}`;
    archives.append(button);
  });
  if (!archives.children.length) archives.textContent = "No archived playthroughs yet.";
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
function renderCharacters() {
  const tabs = document.querySelector("#character-tabs");
  tabs.replaceChildren();
  characterProfiles.forEach(character => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = character.name;
    button.setAttribute("aria-pressed", String(character.id === selectedCharacter));
    button.addEventListener("click", () => {
      selectedCharacter = character.id;
      renderCharacters();
      document.querySelector("#character-tabs button[aria-pressed='true']").focus();
    });
    tabs.append(button);
  });
  const panel = document.querySelector("#character-profile");
  panel.replaceChildren();
  const character = characterProfiles.find(item => item.id === selectedCharacter);
  if (!character) return;
  const create = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const portrait = create("div", "", "character-portrait");
  const placeholder = create("div", "", "portrait-placeholder");
  placeholder.setAttribute("role", "img");
  placeholder.setAttribute("aria-label", `Portrait pending for ${character.name}`);
  placeholder.append(create("span", character.name.split(/\s+/).map(word => word[0]).slice(0, 2).join("")), create("small", "Portrait to come"));
  portrait.append(placeholder);
  if (character.portrait) {
    const img = document.createElement("img");
    img.src = character.portrait;
    img.alt = `Portrait of ${character.name}`;
    img.addEventListener("load", () => { placeholder.hidden = true; });
    img.addEventListener("error", () => { img.remove(); placeholder.hidden = false; });
    portrait.append(img);
  }
  const body = create("div", "", "character-body");
  body.append(create("p", character.is_player ? "YOUR CHARACTER" : "COMPANION", "eyebrow"), create("h3", character.name), create("p", character.role, "character-role"));
  if (!character.is_player) {
    const availability = {
      here: "Here · ready to talk",
      away: "Elsewhere · speak when you are together",
      travelling: "Travelling · speak after they arrive",
      unavailable: "Unavailable for conversation",
      ended: "Chapter complete · profiles remain available to inspect",
    };
    body.append(create("p", availability[character.availability] || "", "character-availability"));
    if (character.conversation_options?.length) {
      const actions = create("div", "", "controls character-conversations");
      actions.setAttribute("aria-label", `Talk with ${character.name}`);
      character.conversation_options.forEach(option => {
        const button = create("button", option.label);
        button.type = "button";
        button.dataset.command = option.command;
        button.dataset.consequential = "false";
        button.disabled = input.disabled;
        actions.append(button);
      });
      body.append(actions, create("p", "Choose a question, then Send to speak. Conversation spends 1 minute.", "character-conversation-hint"));
    }
  }
  const labels = key => key.replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
  const attributes = create("dl", "", "character-attributes");
  Object.entries(character.attributes).forEach(([key, value]) => attributes.append(create("dt", labels(key)), create("dd", value)));
  body.append(attributes);
  if (character.motivations.length) {
    body.append(create("h4", "Motivations"));
    const list = create("ul");
    character.motivations.forEach(text => list.append(create("li", text)));
    body.append(list);
  }
  if (character.values.length) body.append(create("h4", "Values"), create("p", character.values.join(" · ")));
  const details = create("details");
  details.append(create("summary", "Appearance & background"));
  const appearance = create("dl", "", "character-attributes");
  Object.entries(character.appearance).forEach(([key, value]) => appearance.append(create("dt", labels(key)), create("dd", value)));
  details.append(appearance);
  character.background.forEach(text => details.append(create("p", text)));
  body.append(details);
  panel.append(portrait, body);
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
  const resuming = pending.operation === "resume";
  const { operation, ...payload } = pending;
  try {
    await request(resuming ? "/api/resume" : startingNewGame ? "/api/new-game" : "/api/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-BadBadger-Request": "turn", ...(playerScope ? { "X-BadBadger-Player": playerScope } : {}) },
      body: JSON.stringify(payload),
    });
    clearPending();
    input.value = "";
    await load();
    notice.textContent = resuming ? "Saved playthrough resumed. Your previous game is archived." : startingNewGame ? "New game started. Your previous playthrough is archived." : "Saved.";
  } catch (error) {
    if ([400, 409, 413, 422].includes(error.status)) {
      input.value = pending ? (pending.command || "") : input.value;
      clearPending();
      notice.textContent = (startingNewGame || resuming)
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
  const title = chapterSelection.selectedOptions[0].textContent;
  if (!window.confirm(`Start a new ${title} game? Your current playthrough will be archived on the server. The new game begins at the start of the chapter.`)) return;
  persistAndSubmit({ operation: "new-game", request_id: crypto.randomUUID(), expected_revision: revision, confirmed: true, scenario: chapterSelection.value });
});
document.querySelector("#archives").addEventListener("click", event => {
  const button = event.target.closest("button[data-archive]");
  if (!button || busy || pending || revision === null || !storageAvailable) return;
  if (!window.confirm(`Resume this saved playthrough? Your current game will be archived first.`)) return;
  persistAndSubmit({ operation: "resume", request_id: crypto.randomUUID(), expected_revision: revision, archive_id: Number(button.dataset.archive), confirmed: true });
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
