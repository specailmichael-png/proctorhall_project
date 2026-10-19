/*
 * ProctorHall — exam timer, secure screen lock-down, browser activity logging,
 * code autosave, in-browser Python runs (Pyodide), and final submission.
 *
 * The student never sees the event log: events are recorded silently and
 * reviewed by the lecturer on the monitoring log page.
 */
(function () {
  const cfg = window.PROCTOR_CONFIG;
  const form = document.getElementById("examForm");
  const timerEl = document.getElementById("timer");
  const runner = document.getElementById("codeRunner");
  const runnerEditor = document.getElementById("codeEditor");
  const runnerLanguage = document.getElementById("runnerLanguage");
  const output = document.getElementById("codeOutput");
  const gate = document.getElementById("examGate");
  const beginButton = document.getElementById("beginExamBtn");
  const gateNote = document.getElementById("gateNote");
  const lockdownOverlay = document.getElementById("lockdownOverlay");
  const lockdownTitle = document.getElementById("lockdownTitle");
  const lockdownMessage = document.getElementById("lockdownMessage");
  const returnButton = document.getElementById("returnToExamBtn");
  const storagePrefix = `proctorhall:${cfg.attemptId}:`;
  const editors = Array.from(document.querySelectorAll(".program-source"));
  const saveTimers = new Map();
  const maxWarnings = Number(cfg.lockdownMaxWarnings || 3);
  let remaining = cfg.remainingSeconds;
  let submitted = false;
  let examStarted = false;
  let lockdownActive = false;
  let warnings = 0;
  let lastStrikeAt = 0;
  let activeQuestionId = null;
  let activeExecutionAllowed = false;
  let submitPromise = null;
  let strikesSuppressedUntil = 0;

  // Native dialogs (confirm/alert) blur the window; don't let that count as a violation.
  function suppressStrikes(ms) {
    strikesSuppressedUntil = Math.max(strikesSuppressedUntil, Date.now() + ms);
  }

  function csrfFetch(url, payload, keepalive) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": cfg.csrfToken },
      body: JSON.stringify(payload),
      keepalive: Boolean(keepalive),
    });
  }

  function log(type, detail, severity, force) {
    if (window.ProctorHall) window.ProctorHall.logViolation(type, detail, severity, force);
  }

  function formatTime(seconds) {
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function tick() {
    if (submitted) return;
    timerEl.textContent = formatTime(Math.max(0, remaining));
    if (remaining <= 60) timerEl.classList.add("low");
    if (remaining <= 0) {
      submitExam(true);
      return;
    }
    remaining -= 1;
    window.setTimeout(tick, 1000);
  }

  /* ------------------------------------------------------------------
   * Secure screen lock-down
   * Full screen on start; leaving the exam screen warns the student and
   * is recorded. After `maxWarnings` the exam auto-submits, answered or not.
   * ------------------------------------------------------------------ */

  function enterFullscreen() {
    const root = document.documentElement;
    if (!root.requestFullscreen || document.fullscreenElement) {
      return Promise.resolve(Boolean(document.fullscreenElement));
    }
    return root.requestFullscreen().then(() => true, () => false);
  }

  function strike(reason, eventType) {
    if (submitted || lockdownActive || !examStarted || Date.now() < strikesSuppressedUntil) return;
    const now = Date.now();
    if (now - lastStrikeAt < 1500) return; // one leave often fires several handlers
    lastStrikeAt = now;
    warnings += 1;
    log("LOCKDOWN_WARNING", `Warning ${warnings} of ${maxWarnings}: ${reason}`, "warning", true);
    log(eventType, reason, "warning");
    lockdownActive = true;

    if (warnings >= maxWarnings) {
      lockdownTitle.textContent = "Your exam is being submitted";
      lockdownMessage.textContent =
        `You left the secure exam screen ${maxWarnings} times, so your exam is being submitted now — answered or not.`;
      log("EXAM_AUTO_SUBMITTED", `Exam auto-submitted after ${warnings} lockdown warnings.`, "critical", true);
      returnButton.hidden = true;
      lockdownOverlay.hidden = false;
      submitExam(true);
      return;
    }

    lockdownTitle.textContent = `Warning ${warnings} of ${maxWarnings}`;
    lockdownMessage.textContent = `${reason} This has been recorded in your monitoring log.`;
    returnButton.hidden = false;
    lockdownOverlay.hidden = false;
  }

  returnButton.addEventListener("click", () => {
    lockdownOverlay.hidden = true;
    lockdownActive = false;
    enterFullscreen(); // runs inside the click gesture, so the browser allows it
  });

  beginButton.addEventListener("click", () => {
    gate.hidden = true;
    examStarted = true;
    enterFullscreen().then((ok) => {
      if (!ok) {
        gateNote.hidden = false;
        gateNote.textContent =
          "Full screen could not be started by this browser. Tab and window changes are still recorded and reviewed.";
      }
    });
  });

  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) strike("You left full screen.", "FULLSCREEN_EXIT");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) strike("You switched away from the exam or minimized the window.", "TAB_SWITCH");
  });
  window.addEventListener("blur", () => {
    strike("The exam window lost focus (minimized or switched).", "WINDOW_BLUR");
  });

  /* ------------------------------------------------------------------
   * Copy / paste / cut are blocked everywhere during the exam and recorded.
   * ------------------------------------------------------------------ */
  ["copy", "paste", "cut"].forEach((eventType) => {
    document.addEventListener(eventType, (event) => {
      if (submitted || !examStarted) return;
      log(`${eventType.toUpperCase()}_ATTEMPT`, `${eventType} is disabled during the exam.`, "warning");
      event.preventDefault();
    }, true);
  });
  document.addEventListener("contextmenu", (event) => {
    if (submitted || !examStarted) return;
    event.preventDefault();
    log("COPY_ATTEMPT", "Right-click menu is disabled during the exam.", "info");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !runner.hidden) {
      closeRunner();
      return;
    }
    if (submitted || !examStarted || !(event.ctrlKey || event.metaKey)) return;
    const key = String(event.key || "").toLowerCase();
    if (!["c", "v", "x", "a", "p", "s", "u"].includes(key)) return;
    event.preventDefault();
    const type = ["c", "x"].includes(key) ? "COPY" : key === "v" ? "PASTE" : null;
    if (type) log(`${type}_ATTEMPT`, `Keyboard shortcut Ctrl+${key.toUpperCase()} is disabled during the exam.`, "warning");
    else log("COPY_ATTEMPT", `Keyboard shortcut Ctrl+${key.toUpperCase()} is disabled during the exam.`, "info");
  });

  /* ------------------------------------------------------------------
   * Code drafts: local storage first, autosave to Django, versioned.
   * ------------------------------------------------------------------ */

  function localKey(editor) {
    return `${storagePrefix}question:${editor.dataset.questionId}`;
  }

  function setStatus(questionId, text) {
    const status = document.querySelector(`[data-save-status="${questionId}"]`);
    if (status) status.textContent = text;
  }

  function saveLocal(editor) {
    const draft = {
      source_code: editor.value,
      language: editor.dataset.language,
      changed_at: new Date().toISOString(),
    };
    try {
      localStorage.setItem(localKey(editor), JSON.stringify(draft));
    } catch (error) {
      setStatus(editor.dataset.questionId, "Local storage unavailable");
    }
  }

  async function saveCode(editor, keepalive) {
    saveLocal(editor);
    const questionId = editor.dataset.questionId;
    if (!navigator.onLine) {
      setStatus(questionId, "Offline; draft kept on this device");
      return false;
    }
    setStatus(questionId, "Saving...");
    try {
      const response = await csrfFetch(cfg.saveUrl, {
        question_id: Number(questionId),
        source_code: editor.value,
        language: editor.dataset.language,
      }, keepalive);
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Save failed");
      setStatus(questionId, `Saved ${new Date(data.saved_at).toLocaleTimeString()}`);
      return true;
    } catch (error) {
      setStatus(questionId, "Save pending; draft kept on this device");
      return false;
    }
  }

  function scheduleSave(editor) {
    saveLocal(editor);
    setStatus(editor.dataset.questionId, "Unsaved changes");
    window.clearTimeout(saveTimers.get(editor.dataset.questionId));
    saveTimers.set(editor.dataset.questionId, window.setTimeout(() => saveCode(editor), 650));
  }

  function restoreDrafts() {
    editors.forEach((editor) => {
      try {
        const draft = JSON.parse(localStorage.getItem(localKey(editor)) || "null");
        if (draft && typeof draft.source_code === "string" && draft.language === editor.dataset.language) {
          editor.value = draft.source_code;
          setStatus(editor.dataset.questionId, "Restored local draft; syncing...");
          saveCode(editor);
        }
      } catch (error) {
        localStorage.removeItem(localKey(editor));
      }
    });
  }

  function collectAnswers() {
    const answers = {};
    form.querySelectorAll("input[type=radio]:checked").forEach((input) => {
      answers[input.name.slice(2)] = input.value;
    });
    const codeAnswers = {};
    editors.forEach((editor) => {
      codeAnswers[editor.dataset.questionId] = {
        source_code: editor.value,
        language: editor.dataset.language,
      };
    });
    return { answers, code_answers: codeAnswers };
  }

  async function submitExam(automatic) {
    if (submitPromise) return submitPromise;
    if (submitted) return;
    if (!automatic) {
      suppressStrikes(2500);
      if (!window.confirm("Submit the exam now? You cannot change answers after this.")) return;
    }
    submitPromise = (async () => {
        if (!runner.hidden) closeRunner();
        stopRunWorker();
        editors.forEach((editor) => window.clearTimeout(saveTimers.get(editor.dataset.questionId)));
      await Promise.all(editors.map((editor) => saveCode(editor)));
      try {
        const response = await csrfFetch(cfg.submitUrl, collectAnswers());
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || "Submission failed");
        submitted = true;
        editors.forEach((editor) => localStorage.removeItem(localKey(editor)));
        window.location.assign(data.redirect);
      } catch (error) {
        submitPromise = null;
        suppressStrikes(2500);
        window.alert("Submission could not reach the server. Your code remains saved on this device; reconnect and submit again.");
      }
    })();
    return submitPromise;
  }

  function sessionToken() {
    const key = `${storagePrefix}session`;
    let token = sessionStorage.getItem(key);
    if (!token) {
      token = window.crypto && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
      sessionStorage.setItem(key, token);
    }
    return token;
  }

  async function sendHeartbeat() {
    try {
      await csrfFetch(cfg.heartbeatUrl, { session_token: sessionToken() });
    } catch (error) {
      // A later heartbeat retries session tracking.
    }
  }

  /* ------------------------------------------------------------------
   * Code runner workspace — Python executes in this browser via Pyodide.
   * ------------------------------------------------------------------ */

  let runWorker = null;
  let runCounter = 0;
  const pendingRuns = new Map();
  const RUN_TIMEOUT_MS = 8000;
  const RUNTIME_LOAD_TIMEOUT_MS = 60000;

  function stopRunWorker() {
    if (runWorker) {
      runWorker.terminate();
      runWorker = null;
    }
    pendingRuns.clear();
  }

  function ensureRunWorker() {
    if (runWorker) return runWorker;
    runWorker = new Worker(cfg.pyodideWorkerUrl);
    runWorker.onmessage = (event) => {
      const { runId } = event.data || {};
      const pending = pendingRuns.get(runId);
      if (!pending) return;
      if (event.data.status === "loading") {
        output.textContent = "Loading Python in your browser… the first run downloads the runtime.";
        return;
      }
      if (event.data.status === "ready") {
        pending.startExecutionTimeout();
        return;
      }
      pendingRuns.delete(runId);
      pending.finish(event.data);
    };
    runWorker.onerror = (event) => {
      const pending = Array.from(pendingRuns.values());
      pendingRuns.clear();
      stopRunWorker();
      pending.forEach((run) => run.finish({
        ok: false,
        error: `The browser runner failed to load or execute: ${event.message || "unknown worker error"}`,
      }));
      event.preventDefault && event.preventDefault();
    };
    return runWorker;
  }

  function runInBrowser(code) {
    const worker = ensureRunWorker();
    const runId = ++runCounter;
    return new Promise((resolve) => {
      let timer;
      const stopAfter = (milliseconds, message) => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => {
          if (!pendingRuns.has(runId)) return;
          pendingRuns.delete(runId);
          stopRunWorker();
          resolve({ ok: false, error: message });
        }, milliseconds);
      };
      pendingRuns.set(runId, {
        startExecutionTimeout() {
          stopAfter(
            RUN_TIMEOUT_MS,
            `Stopped after ${RUN_TIMEOUT_MS / 1000} seconds — the code may contain an endless loop. Nothing was executed on the exam server.`
          );
        },
        finish(data) {
          window.clearTimeout(timer);
          resolve(data);
        },
      });
      stopAfter(
        RUNTIME_LOAD_TIMEOUT_MS,
        "The Python runtime did not finish loading. Check the internet connection and try again."
      );
      worker.postMessage({ runId, code, indexUrl: cfg.pyodideIndexUrl });
    });
  }

  function openRunner(questionId) {
    const source = document.querySelector(`.program-source[data-question-id="${questionId}"]`);
    if (!source) return;
    activeQuestionId = questionId;
    activeExecutionAllowed = source.dataset.executionAllowed === "true";
    runnerEditor.value = source.value;
    runnerLanguage.value = source.dataset.language;
    document.getElementById("codeRunnerTitle").textContent = `Question ${questionId} code workspace`;
    output.textContent = "Click Run to see output.";
    runner.hidden = false;
    document.body.classList.add("runner-open");
    runnerEditor.focus();
  }

  function closeRunner() {
    if (activeQuestionId) {
      const source = document.querySelector(`.program-source[data-question-id="${activeQuestionId}"]`);
      if (source) {
        source.value = runnerEditor.value;
        scheduleSave(source);
      }
    }
    activeQuestionId = null;
    activeExecutionAllowed = false;
    runner.hidden = true;
    document.body.classList.remove("runner-open");
  }

  editors.forEach((editor) => editor.addEventListener("input", () => scheduleSave(editor)));
  document.querySelectorAll(".save-code-trigger").forEach((button) => {
    button.addEventListener("click", () => {
      const editor = document.querySelector(`.program-source[data-question-id="${button.dataset.questionId}"]`);
      if (editor) saveCode(editor);
    });
  });
  document.querySelectorAll(".code-runner-trigger").forEach((button) => {
    button.addEventListener("click", () => openRunner(button.dataset.questionId));
  });
  document.querySelectorAll("[data-runner-close]").forEach((button) => button.addEventListener("click", closeRunner));
  document.getElementById("runCode").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (!activeExecutionAllowed) {
      output.textContent = "Execution is disabled for this question. Your source is still autosaved and submitted normally.";
      return;
    }
    if (runnerLanguage.value !== "python") {
      output.textContent = `The in-browser runner supports Python only (this question is ${runnerLanguage.value}). Your source is still autosaved and submitted for marking.`;
      return;
    }
    const questionId = activeQuestionId;
    const source = document.querySelector(`.program-source[data-question-id="${questionId}"]`);
    if (source) {
      source.value = runnerEditor.value;
      scheduleSave(source);
    }
    button.disabled = true;
    output.textContent = "Running in your browser… the first run downloads the Python runtime (about 10 MB).";
    const result = await runInBrowser(runnerEditor.value);
    if (result.ok) {
      output.textContent = result.output;
    } else {
      output.textContent = `Error:\n${result.error}`;
    }
    button.disabled = false;
    // Record the run in the monitoring log; the code itself never runs server-side.
    try {
      await csrfFetch(cfg.runCodeUrl, { question_id: Number(questionId), language: "python" });
    } catch (error) {
      // The run stays local to the browser; the lecturer still marks the submitted source.
    }
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitExam(false);
  });

  window.addEventListener("online", () => editors.forEach((editor) => saveCode(editor)));
  window.addEventListener("pagehide", () => editors.forEach((editor) => saveCode(editor, true)));

  document.addEventListener("DOMContentLoaded", () => {
    restoreDrafts();
    sendHeartbeat();
    window.setInterval(sendHeartbeat, 15000);
    gate.hidden = false;
    lockdownOverlay.hidden = true;
    tick();
  });
})();
