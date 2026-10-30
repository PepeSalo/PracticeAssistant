"use strict";

// Local chat window controller. The server sends a snapshot first, then events.
// The page never holds an API key: only this per-run session token, which only
// allows talking to this loopback server.

const token = document.querySelector('meta[name="session-token"]').content;
const $ = (id) => document.getElementById(id);

const STATE_TEXT = {
  starting: "Starting…",
  listening: "Listening… speak whenever you like",
  thinking: "Thinking…",
  speaking: "Speaking… (start talking to interrupt)",
  interrupted: "Interrupted — listening",
  ended: "Session ended",
  error: "Something went wrong",
};

const SCENARIOS = [
  ["free-conversation", "Free conversation"],
  ["ordering at a cafe", "Ordering at a cafe"],
  ["job interview", "Job interview"],
  ["at the doctor", "At the doctor"],
  ["travel and hotels", "Travel and hotels"],
];

const LANGUAGES = [
  ["en", "English"],
  ["fi", "Finnish"],
  ["sv", "Swedish"],
  ["es", "Spanish"],
  ["fr", "French"],
  ["de", "German"],
  ["it", "Italian"],
];

const LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"];

let busy = false;
let pollTimer = null;
let stream = null;
const bubbles = new Map(); // turn -> {learner, tutor}

// ---- helpers ------------------------------------------------------------
function request(path, body) {
  return fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-Token": token,
    },
    body: JSON.stringify(body || {}),
  }).then(async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `Request failed (${response.status})`);
    }
    return payload;
  });
}

function toast(message, level = "info", timeout = 5000) {
  if (!message) return;
  const node = document.createElement("div");
  node.className = `toast ${level}`;
  node.setAttribute("role", level === "error" ? "alert" : "status");
  node.textContent = message;
  $("toasts").appendChild(node);
  window.setTimeout(() => node.remove(), timeout);
}

function setStatus(state, detail) {
  $("status").dataset.state = state || "starting";
  $("status-text").textContent = detail || STATE_TEXT[state] || state || "";
  const active = state === "listening";
  $("typed").disabled = !active;
  $("send").disabled = !active;
  $("type-hint").textContent = active
    ? "Typed messages are answered aloud, like spoken ones."
    : state === "thinking" || state === "speaking"
      ? "Wait for the current answer, or press Stop speaking."
      : state === "ended"
        ? "The session has ended. Close this window and stop the app."
        : "Waiting for the assistant to start.";
}

function chipValue(name, value) {
  const node = $(`chip-${name}`);
  if (node) node.textContent = value;
}

function renderSettings(settings, engine) {
  if (!settings) return;
  chipValue("practice_language", settings.practice_language || "—");
  chipValue("level", settings.level || "—");
  chipValue("scenario", settings.scenario || "—");
  chipValue("correction", settings.correction ? "on" : "off");
  if (engine) chipValue("engine", engine);
}

function renderLearning(learning) {
  const fill = (id, values, empty) => {
    const list = $(id);
    list.textContent = "";
    if (!values || !values.length) {
      const item = document.createElement("li");
      item.className = "muted";
      item.textContent = empty;
      list.appendChild(item);
      return;
    }
    values.slice(0, 20).forEach((value) => {
      const item = document.createElement("li");
      item.textContent = value;
      list.appendChild(item);
    });
  };
  fill("vocabulary", learning && learning.vocabulary, "Nothing recorded yet.");
  fill("mistakes", learning && learning.mistakes, "Nothing recorded yet.");
}

function renderNotifications(list) {
  const feed = $("notifications");
  feed.textContent = "";
  if (!list || !list.length) {
    const item = document.createElement("li");
    item.className = "muted";
    item.textContent = "No notifications yet.";
    feed.appendChild(item);
    return;
  }
  list.slice(-40).forEach((entry) => {
    const item = document.createElement("li");
    item.className = entry.level || "info";
    if (entry.at) {
      const stamp = document.createElement("time");
      stamp.textContent = new Date(entry.at * 1000).toLocaleTimeString();
      item.appendChild(stamp);
    }
    item.appendChild(document.createTextNode(entry.text || ""));
    feed.appendChild(item);
  });
  feed.scrollTop = feed.scrollHeight;
}

function note(level, text) {
  const feed = $("notifications");
  const placeholder = feed.querySelector("li.muted");
  if (placeholder) placeholder.remove();
  const item = document.createElement("li");
  item.className = level;
  const stamp = document.createElement("time");
  stamp.textContent = new Date().toLocaleTimeString();
  item.appendChild(stamp);
  item.appendChild(document.createTextNode(text));
  feed.appendChild(item);
  while (feed.children.length > 40) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

function renderDiagnostics(diagnostics) {
  const list = $("diagnostics");
  list.textContent = "";
  Object.entries(diagnostics || {}).forEach(([key, value]) => {
    const term = document.createElement("dt");
    term.textContent = key.replace(/_/g, " ");
    const def = document.createElement("dd");
    def.textContent =
      value === "" || value === null || value === undefined ? "—" : String(value);
    list.append(term, def);
  });
}

// ---- conversation -------------------------------------------------------
const WHO = { learner: "You said", tutor: "Tutor" };

function bubble(turn, role) {
  const key = String(turn);
  if (!bubbles.has(key)) bubbles.set(key, {});
  const group = bubbles.get(key);
  if (group[role]) return group[role];

  const node = document.createElement("article");
  node.className = `msg ${role}`;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = WHO[role];
  const text = document.createElement("span");
  text.className = "text";
  const meta = document.createElement("div");
  meta.className = "meta";
  node.append(who, text, meta);

  const conversation = $("conversation");
  const empty = $("empty");
  if (empty) empty.remove();
  conversation.appendChild(node);
  group[role] = { node, text, meta };
  node.scrollIntoView({ block: "end", behavior: "smooth" });
  return group[role];
}

function describeTiming(stages, totalMs) {
  if (totalMs === null || totalMs === undefined) {
    if (stages && stages.note) return { label: stages.note, level: "muted" };
    return null;
  }
  const level = totalMs < 800 ? "ok" : totalMs < 1600 ? "warn" : "bad";
  return {
    label: `Hearing the first words took ${Math.round(totalMs)} ms`,
    level,
  };
}

function attachTiming(turn, stages, totalMs) {
  const entry = bubble(turn, "tutor");
  const meta = entry.meta;
  meta.querySelectorAll(".badge.timing").forEach((node) => node.remove());
  const timing = describeTiming(stages, totalMs);
  if (!timing) return;
  const badge = document.createElement("span");
  badge.className = `badge timing ${timing.level}`;
  badge.textContent = timing.label;
  badge.title = stages
    ? Object.entries(stages)
        .map(([name, value]) =>
          `${name}: ${value === null ? "unavailable" : `${Math.round(value)} ms`}`,
        )
        .join("\n")
    : "";
  meta.appendChild(badge);
}

function markInterrupted(turn) {
  const entry = bubble(turn, "tutor");
  entry.node.classList.add("interrupted");
  const badge = document.createElement("span");
  badge.className = "badge interrupted";
  badge.textContent = "you interrupted here";
  entry.meta.appendChild(badge);
}

// ---- events -------------------------------------------------------------
function handle(event) {
  switch (event.kind) {
    case "state":
      setStatus(event.state, event.detail);
      break;
    case "transcript": {
      const entry = bubble(event.turn, "learner");
      entry.text.textContent += event.text || "";
      break;
    }
    case "reply": {
      const entry = bubble(event.turn, "tutor");
      entry.text.textContent = event.text || "";
      if (event.interrupted) markInterrupted(event.turn);
      break;
    }
    case "timing":
      attachTiming(event.turn, event.stages, event.total_ms);
      break;
    case "confirmation":
      toast(event.text, "ok", 4000);
      note("ok", event.text || "");
      break;
    case "notification":
      toast(event.text, event.level || "info");
      note(event.level || "info", event.text || "");
      break;
    case "error":
      toast(event.message, "error", 9000);
      note("error", event.message || "");
      break;
    case "learning":
      renderLearning({
        vocabulary: (event.vocabulary || []).concat(
          collect("vocabulary"),
        ),
        mistakes: (event.mistakes || []).concat(collect("mistakes")),
      });
      break;
    case "session":
      renderSettings(event, "");
      if (event.ended) {
        setStatus("ended", "Session ended");
        if (event.summary) toast(event.summary, "ok", 15000);
      }
      break;
    case "diagnostic":
      break;
    default:
      break;
  }
}

function collect(id) {
  return Array.from($(id).querySelectorAll("li"))
    .map((node) => node.textContent)
    .filter((text) => text && text !== "Nothing recorded yet.");
}

function renderSnapshot(snapshot) {
  if (!snapshot) return;
  setStatus(snapshot.state, snapshot.detail);
  renderSettings(snapshot.settings, snapshot.engine);
  renderLearning(snapshot.learning);
  renderNotifications(snapshot.notifications);
  renderDiagnostics(snapshot.diagnostics);
  $("conversation").querySelectorAll(".msg").forEach((node) => node.remove());
  bubbles.clear();
  (snapshot.turns || []).forEach((turn) => {
    if (turn.role === "learner") {
      bubble(turn.turn, "learner").text.textContent = turn.text || "";
    } else {
      bubble(turn.turn, "tutor").text.textContent = turn.text || "";
      if (turn.interrupted) markInterrupted(turn.turn);
      if (turn.timing || turn.total_ms !== undefined) {
        attachTiming(turn.turn, turn.timing, turn.total_ms);
      }
    }
  });
  if (!(snapshot.turns || []).length) {
    const note = document.createElement("p");
    note.className = "empty";
    note.id = "empty";
    note.textContent =
      "Nothing has been said yet. Say something such as “Hello, I would like to practise ordering coffee.”";
    $("conversation").appendChild(note);
  }
  $("dropped-note").hidden = !(snapshot.dropped > 0);
}

function startPolling() {
  if (pollTimer) return;
  toast("Live updates paused; reloading the window periodically.", "warn");
  pollTimer = window.setInterval(async () => {
    try {
      const response = await fetch(`/api/state?token=${encodeURIComponent(token)}`);
      if (response.ok) renderSnapshot(await response.json());
    } catch (_error) {
      /* keep trying quietly */
    }
  }, 1000);
}

function connect() {
  stream = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
  stream.addEventListener("snapshot", (message) => {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
    try {
      renderSnapshot(JSON.parse(message.data));
    } catch (_error) {
      /* ignore malformed frame */
    }
  });
  stream.onmessage = (message) => {
    if (!message.data) return;
    try {
      handle(JSON.parse(message.data));
    } catch (_error) {
      /* ignore malformed frame */
    }
  };
  stream.onerror = () => {
    if (stream.readyState === EventSource.CLOSED) startPolling();
  };
}

// ---- controls -----------------------------------------------------------
function choose(entries, name, action, current) {
  const menu = $("chip-menu");
  menu.textContent = "";
  entries.forEach(([value, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    if (String(value) === String(current)) button.disabled = true;
    button.addEventListener("click", async () => {
      menu.hidden = true;
      try {
        const snapshot = await request("/api/command", { action, value: String(value) });
        renderSnapshot(snapshot);
      } catch (error) {
        toast(error.message, "error");
      }
    });
    menu.appendChild(button);
  });
  menu.dataset.name = name;
  menu.hidden = false;
  menu.querySelector("button:not(:disabled)")?.focus();
}

document.querySelectorAll(".chip[data-chip]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const name = chip.dataset.chip;
    if ($("chip-menu").dataset.name === name && !$("chip-menu").hidden) {
      $("chip-menu").hidden = true;
      return;
    }
    if (name === "practice_language") {
      choose(LANGUAGES, name, "language", $("chip-practice_language").textContent);
    } else if (name === "level") {
      choose(LEVELS.map((l) => [l, l]), name, "level", $("chip-level").textContent);
    } else if (name === "scenario") {
      choose(SCENARIOS, name, "scenario", $("chip-scenario").textContent);
    } else if (name === "correction") {
      choose(
        [["on", "Correct my mistakes"], ["off", "Don't correct me"]],
        name,
        "correction",
        $("chip-correction").textContent,
      );
    }
  });
});

document.addEventListener("click", (event) => {
  const menu = $("chip-menu");
  if (!menu.hidden && !menu.contains(event.target) && !event.target.closest(".chip")) {
    menu.hidden = true;
  }
});

document.querySelectorAll(".controls button").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      if (button.dataset.action === "stop") {
        await request("/api/stop", {});
        return;
      }
      const snapshot = await request("/api/command", {
        action: button.dataset.action,
        value: button.dataset.value || "",
      });
      renderSnapshot(snapshot);
    } catch (error) {
      toast(error.message, "error");
    }
  });
});

$("typebox").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("typed");
  const text = input.value.trim();
  if (!text || busy) return;
  busy = true;
  input.value = "";
  try {
    await request("/api/message", { text });
  } catch (error) {
    toast(error.message, "error");
    input.value = text;
  } finally {
    busy = false;
    input.focus();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  request("/api/stop", {}).catch(() => {});
});

// ---- boot ---------------------------------------------------------------
setStatus("starting");
connect();
