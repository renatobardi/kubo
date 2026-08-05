// study-chat.js — JS unificado para chat SSE (B12).
// Mentor e planner passam endpoint/rótulos/callbacks para StudyChat.init().
// Substitui as 3 cópias inline (handleSSEEvent, handleMentorSSEEvent, parser do planner).
(function () {
  "use strict";

  function bindEnterToSend(inputEl, formEl) {
    inputEl.addEventListener("keydown", function (e) {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        formEl.requestSubmit();
      }
    });
  }

  function addMessage(messagesEl, role, content, assistantLabel) {
    var wrapper = document.createElement("div");
    wrapper.className = "flex " + (role === "user" ? "justify-end" : "justify-start");
    var bubble = document.createElement("div");
    bubble.className =
      "max-w-[80%] rounded-2xl px-4 py-2 text-sm " +
      (role === "user" ? "bg-primary/10" : "bg-input/30");
    var label = document.createElement("span");
    label.className = "text-xs text-muted-foreground";
    label.textContent = role === "user" ? "Você" : assistantLabel;
    var p = document.createElement("p");
    p.className = "mt-0.5 whitespace-pre-wrap";
    p.textContent = content;
    bubble.appendChild(label);
    bubble.appendChild(p);
    wrapper.appendChild(bubble);
    messagesEl.appendChild(wrapper);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return p;
  }

  function addSuggestion(suggestionButtonsEl, type, value, csrf, topicUrl) {
    var btn = document.createElement("button");
    btn.className =
      "rounded-4xl bg-primary/10 px-3 py-1 text-xs ring-1 ring-foreground/10 transition-colors hover:bg-primary/20";
    var labels = { name: "Nome: ", focus: "Foco: ", depth: "Profundidade: " };
    btn.textContent = labels[type] + value;
    btn.onclick = function () {
      applySuggestion(type, value, csrf, topicUrl);
    };
    suggestionButtonsEl.appendChild(btn);
  }

  function applySuggestion(type, value, csrf, topicUrl) {
    if (type === "name") {
      var titleInput = document.querySelector('input[name="title"]');
      if (!titleInput) return;
      var prevTitle = titleInput.value;
      titleInput.value = value;
      var fd = new FormData();
      fd.append("csrf", csrf);
      fd.append("title", value);
      fetch(topicUrl + "/rename", { method: "POST", body: fd })
        .then(function (r) {
          if (!r.ok) {
            titleInput.value = prevTitle;
            return r.text().then(function (text) {
              if (typeof window._kuboShowError === "function") {
                window._kuboShowError(text || "Não foi possível renomear o tema (" + r.status + ").");
              }
            });
          }
          document.querySelectorAll("[data-page-title]").forEach(function (el) {
            el.textContent = value;
          });
          document.title = value + " · Temas · Kubo";
        })
        .catch(function () {
          titleInput.value = prevTitle;
          if (typeof window._kuboShowError === "function") {
            window._kuboShowError("Falha de rede ao renomear o tema.");
          }
        });
    } else if (type === "focus" || type === "depth") {
      var fd2 = new FormData();
      fd2.append("csrf", csrf);
      fd2.append("field", type);
      fd2.append("value", value);
      fetch(topicUrl + "/fields", { method: "POST", body: fd2 })
        .then(function (r) {
          if (r.ok) {
            // Atualiza o valor exibido in-place — sem reload (preserva a conversa).
            var fieldEl = document.querySelector('[data-field="' + type + '"]');
            if (fieldEl) fieldEl.textContent = value;
          } else {
            return r.text().then(function (text) {
              if (typeof window._kuboShowError === "function") {
                window._kuboShowError(text || "Não foi possível atualizar " + type + " (" + r.status + ").");
              }
            });
          }
        })
        .catch(function () {
          if (typeof window._kuboShowError === "function") {
            window._kuboShowError("Falha de rede ao atualizar " + type + ".");
          }
        });
    }
  }

  function parseSSEFrame(frame) {
    var ev = "";
    var dataLines = [];
    var lines = frame.split(/\r?\n/);
    for (var line of lines) {
      if (line.startsWith("event: ")) {
        ev = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        dataLines.push(line.slice(6));
      }
    }
    return { event: ev, data: dataLines.join("\n") };
  }

  async function streamSSE(resp, assistantP, fullTextRef, messagesEl, onDone, ctx) {
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      var events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop();
      for (var i = 0; i < events.length; i++) {
        var parsed = parseSSEFrame(events[i]);
        if (parsed.event === "chunk") {
          fullTextRef.text += parsed.data;
          assistantP.textContent = fullTextRef.text;
          messagesEl.scrollTop = messagesEl.scrollHeight;
        } else if (parsed.event === "error") {
          assistantP.textContent = parsed.data || "Erro ao gerar resposta.";
        } else if (parsed.event === "done") {
          try {
            var done = JSON.parse(parsed.data);
            if (done.text !== undefined) fullTextRef.text = done.text;
            assistantP.textContent = fullTextRef.text || "";
            if (onDone) onDone(done, ctx);
          } catch (e) {
            console.error("SSE done parse failed:", e);
          }
        }
      }
    }
  }

  var StudyChat = {
    init: function (opts) {
      var form = document.getElementById(opts.formId);
      if (!form) return;
      var input = document.getElementById(opts.inputId);
      var submit = document.getElementById(opts.submitId);
      var messages = document.getElementById(opts.messagesId);
      var suggestionButtons = opts.suggestionButtonsId
        ? document.getElementById(opts.suggestionButtonsId)
        : null;
      var csrf = opts.csrf;
      var topicUrl = opts.topicUrl;
      var endpoint = opts.endpoint;
      var assistantLabel = opts.assistantLabel || "Assistant";
      var onDone = opts.onDone || null;

      var ctx = {
        addSuggestion: function (type, value) {
          if (suggestionButtons) addSuggestion(suggestionButtons, type, value, csrf, topicUrl);
        },
      };

      bindEnterToSend(input, form);

      form.onsubmit = async function (e) {
        e.preventDefault();
        var msg = input.value.trim();
        if (!msg) return;
        input.value = "";
        submit.disabled = true;
        addMessage(messages, "user", msg, assistantLabel);
        var assistantP = addMessage(messages, "assistant", "", assistantLabel);
        var fullTextRef = { text: "" };

        try {
          var resp = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({ message: msg, csrf: csrf }),
          });
          if (!resp.ok) {
            assistantP.textContent = "Erro: " + resp.status;
            return;
          }
          await streamSSE(resp, assistantP, fullTextRef, messages, onDone, ctx);
        } catch (err) {
          console.error("chat fetch failed:", err);
          assistantP.textContent = "Erro de conexão.";
        } finally {
          submit.disabled = false;
        }
      };
    },
  };

  window.StudyChat = StudyChat;
})();
