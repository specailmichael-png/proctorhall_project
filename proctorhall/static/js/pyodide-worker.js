/*
 * ProctorHall — browser code runner worker.
 *
 * Loads Pyodide (CPython compiled to WebAssembly) inside this dedicated Web
 * Worker and executes the student's Python source there. The code never
 * reaches the exam server, so an untrusted answer cannot compromise the
 * platform, and the page can terminate this worker if code runs too long.
 */
"use strict";

let pyodidePromise = null;
const MAX_OUTPUT_CHARS = 20000;

function getPyodide(indexUrl) {
  if (!pyodidePromise) {
    importScripts(`${indexUrl}/pyodide.js`);
    pyodidePromise = loadPyodide({ indexURL: indexUrl });
  }
  return pyodidePromise;
}

function clip(text) {
  if (text.length <= MAX_OUTPUT_CHARS) return text;
  return `${text.slice(0, MAX_OUTPUT_CHARS)}\n… output truncated`;
}

self.onmessage = async (event) => {
  const { runId, code, indexUrl } = event.data || {};
  let pyodide;
  self.postMessage({ runId, status: "loading" });
  try {
    pyodide = await getPyodide(indexUrl);
  } catch (error) {
    self.postMessage({
      runId,
      ok: false,
      error: "The Python runtime could not be loaded in this browser. Test runs need an internet connection the first time.",
    });
    return;
  }
  self.postMessage({ runId, status: "ready" });

  let stdout = "";
  let stderr = "";
  try {
    pyodide.setStdout({ batched: (text) => { stdout += `${text}\n`; } });
    pyodide.setStderr({ batched: (text) => { stderr += `${text}\n`; } });
    pyodide.setStdin({ stdin: () => "" });
  } catch (error) {
    // Older Pyodide builds differ slightly on stream APIs; output capture still works.
  }

  try {
    await pyodide.runPythonAsync(code);
    self.postMessage({ runId, ok: true, output: clip(`${stdout}${stderr}`.trim()) || "The code ran with no output." });
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    self.postMessage({
      runId,
      ok: false,
      error: clip(`${stdout}${stderr}${stderr ? "\n" : ""}${message}`.trim() || "The code could not run."),
    });
  }
};
