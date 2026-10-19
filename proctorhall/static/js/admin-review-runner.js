"use strict";

(function () {
  const button = document.getElementById("runSubmission");
  const source = document.querySelector(".review-code");
  const output = document.getElementById("submissionOutput");
  const config = window.ADMIN_RUNNER_CONFIG;
  if (!button || !source || !output || !config) return;

  const RUN_TIMEOUT_MS = 8000;
  const RUNTIME_LOAD_TIMEOUT_MS = 60000;
  let worker = null;
  let runId = 0;
  let timeoutId = null;

  function stopWorker() {
    if (timeoutId) window.clearTimeout(timeoutId);
    timeoutId = null;
    if (worker) worker.terminate();
    worker = null;
  }

  button.addEventListener("click", () => {
    if (worker) stopWorker();
    button.disabled = true;
    output.textContent = "Running in this browser… the first run downloads the Python runtime.";

    try {
      worker = new Worker(config.workerUrl);
    } catch (error) {
      button.disabled = false;
      output.textContent = `Could not start the browser worker: ${error.message || error}`;
      return;
    }

    const currentRun = ++runId;
    const stopAfter = (milliseconds, message) => {
      if (timeoutId) window.clearTimeout(timeoutId);
      timeoutId = window.setTimeout(() => {
        if (currentRun !== runId) return;
        stopWorker();
        button.disabled = false;
        output.textContent = message;
      }, milliseconds);
    };
    worker.onmessage = (event) => {
      if (currentRun !== runId) return;
      const result = event.data || {};
      if (result.status === "loading") {
        output.textContent = "Loading Python in your browser… the first run downloads the runtime.";
        return;
      }
      if (result.status === "ready") {
        stopAfter(
          RUN_TIMEOUT_MS,
          "Stopped after 8 seconds. The code may contain an endless loop; nothing ran on the ProctorHall server."
        );
        return;
      }
      output.textContent = result.ok ? result.output : `Error:\n${result.error || "The code could not run."}`;
      stopWorker();
      button.disabled = false;
    };
    worker.onerror = (event) => {
      if (currentRun !== runId) return;
      output.textContent = `The browser runner failed: ${event.message || "unknown worker error"}`;
      stopWorker();
      button.disabled = false;
      event.preventDefault();
    };
    stopAfter(
      RUNTIME_LOAD_TIMEOUT_MS,
      "The Python runtime did not finish loading. Check the internet connection and try again."
    );
    worker.postMessage({
      runId: currentRun,
      code: source.value,
      indexUrl: config.pyodideIndexUrl,
    });
  });

  window.addEventListener("pagehide", stopWorker);
})();
