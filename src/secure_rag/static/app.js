const form = document.querySelector("#question-form");
const input = document.querySelector("#question");
const sendButton = document.querySelector("#send-button");
const messages = document.querySelector("#messages");
const welcome = document.querySelector("#welcome");
const conversation = document.querySelector("#conversation");
let mode = "answer";
let busy = false;

function scrollToLatest() {
  conversation.scrollTop = conversation.scrollHeight;
}

function makeElement(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = value;
  return element;
}

function addMessage(role, label) {
  const row = makeElement("article", `message ${role}`);
  const body = makeElement("div", "message-body");
  if (role === "assistant") {
    row.append(makeElement("div", "avatar", "✳"));
    body.append(makeElement("div", "message-label", label));
  }
  row.append(body);
  messages.append(row);
  welcome.hidden = true;
  scrollToLatest();
  return body;
}

function addAnswerText(container, answer, sourceCards) {
  const text = makeElement("div", "message-text");
  const citations = /\[(\d+)\]/g;
  let cursor = 0;
  for (const match of answer.matchAll(citations)) {
    text.append(document.createTextNode(answer.slice(cursor, match.index)));
    const number = Number(match[1]);
    if (number >= 1 && number <= sourceCards.length) {
      const citation = makeElement("button", "citation", `[${number}]`);
      citation.type = "button";
      citation.setAttribute("aria-label", `Open source ${number}`);
      citation.addEventListener("click", () => {
        const card = sourceCards[number - 1];
        card.open = true;
        card.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      text.append(citation);
    } else {
      text.append(document.createTextNode(match[0]));
    }
    cursor = match.index + match[0].length;
  }
  text.append(document.createTextNode(answer.slice(cursor)));
  container.append(text);
}

function addSources(container, sources) {
  if (!sources.length) return [];
  container.append(makeElement("div", "sources-heading", `${sources.length} VERIFIED ${sources.length === 1 ? "SOURCE" : "SOURCES"}`));
  return sources.map((source, index) => {
    const card = makeElement("details", "source-card");
    const summary = makeElement("summary");
    summary.append(makeElement("span", "source-number", String(index + 1)));
    summary.append(makeElement("span", "source-title", source.title));
    summary.append(makeElement("span", "source-meta", `${source.author} · ${source.post_id}`));
    summary.append(makeElement("span", "source-chevron", "›"));
    card.append(summary, makeElement("div", "source-excerpt", source.text));
    container.append(card);
    return card;
  });
}

async function ask(question) {
  if (busy || !question.trim()) return;
  const prompt = question.trim();
  busy = true;
  sendButton.disabled = true;
  input.value = "";
  addMessage("user").append(makeElement("div", "message-text", prompt));
  const responseMode = mode;
  const responseBody = addMessage("assistant", responseMode === "insights" ? "INSIGHTS" : "ANSWER");
  const thinking = makeElement("div", "thinking");
  thinking.setAttribute("aria-label", "Searching verified sources and preparing an answer");
  for (let index = 0; index < 3; index += 1) thinking.append(makeElement("span"));
  responseBody.append(thinking);
  try {
    const response = await fetch(responseMode === "insights" ? "/insights" : "/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: prompt, limit: 5 }),
    });
    if (!response.ok) {
      const requestId = response.headers.get("X-Request-ID");
      throw new Error(`Request failed (${response.status}). Check that Qdrant and Ollama are running.${requestId ? ` Request ID: ${requestId}` : ""}`);
    }
    const result = await response.json();
    thinking.remove();
    const cardsContainer = makeElement("div", "sources");
    const cards = addSources(cardsContainer, result.sources || []);
    addAnswerText(responseBody, result.answer || "No answer was returned.", cards);
    responseBody.append(cardsContainer);
  } catch (error) {
    thinking.remove();
    responseBody.append(makeElement("div", "message-text error-text", error.message || "Something went wrong."));
  } finally {
    busy = false;
    sendButton.disabled = false;
    scrollToLatest();
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ask(input.value);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => ask(button.dataset.prompt));
});

document.querySelectorAll("[data-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    mode = button.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach((option) => {
      const active = option === button;
      option.classList.toggle("active", active);
      option.setAttribute("aria-pressed", String(active));
    });
    input.focus();
  });
});

function clearConversation() {
  if (busy) return;
  messages.replaceChildren();
  welcome.hidden = false;
  input.focus();
}

document.querySelector("#new-chat").addEventListener("click", clearConversation);
document.querySelector("#mobile-new-chat").addEventListener("click", clearConversation);
