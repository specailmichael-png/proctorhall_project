/*
 * ProctorHall — in-browser AI proctoring.
 * Uses face-api.js (face count + landmarks -> rough head pose / gaze) and
 * tensorflow.js coco-ssd (phone / device detection). Both are free,
 * open-source, and run entirely client-side — no server GPU, no paid API.
 *
 * Monitoring is deliberately one-way for the student: every event is recorded
 * and sent to the lecturer's monitoring log, but the student only ever sees a
 * generic "monitoring is live" status so the exam screen stays distraction free.
 */

(function () {
  const cfg = window.PROCTOR_CONFIG;
  const video = document.getElementById("webcam");
  const overlay = document.getElementById("overlay");
  const statusEl = document.getElementById("proctorStatus");
  const monitorDot = document.getElementById("monitorDot");
  const monitorLabel = document.getElementById("monitorLabel");

  let cocoModel = null;
  let modelsReady = false;
  let lastLogAt = {};
  let shuttingDown = false;
  const LOG_COOLDOWN_MS = 4000; // don't spam the same violation type

  function setStatus(text, kind) {
    const dotClass = kind === "ok" ? "dot-ok" : "dot-warn";
    if (statusEl) statusEl.innerHTML = `<span class="dot ${dotClass}"></span><span>${text}</span>`;
    if (monitorDot) monitorDot.className = `dot ${dotClass}`;
    if (monitorLabel) monitorLabel.textContent = text;
  }

  async function logViolation(eventType, detail, severity, force) {
    severity = severity || "warning";
    const now = Date.now();
    if (!force && lastLogAt[eventType] && now - lastLogAt[eventType] < LOG_COOLDOWN_MS) return;
    lastLogAt[eventType] = now;

    try {
      await fetch(cfg.logUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": cfg.csrfToken },
        body: JSON.stringify({
          attempt_id: cfg.attemptId,
          event_type: eventType,
          detail: detail,
          severity: severity,
        }),
      });
    } catch (e) {
      console.warn("Could not send proctor log", e);
    }
  }
  window.ProctorHall = { logViolation };

  async function initCamera() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 480, height: 360 }, audio: false });
      video.srcObject = stream;
      stream.getVideoTracks().forEach((track) => {
        track.addEventListener("ended", () => {
          if (!shuttingDown) logViolation("CAMERA_INTERRUPTION", "Camera stream ended during the exam.", "critical");
        });
        track.addEventListener("mute", () => {
          if (!shuttingDown) logViolation("CAMERA_INTERRUPTION", "Camera stream was interrupted.", "warning");
        });
      });
      await new Promise((resolve) => (video.onloadedmetadata = resolve));
      overlay.width = video.videoWidth;
      overlay.height = video.videoHeight;
      return true;
    } catch (err) {
      setStatus("Camera access is required — your lecturer has been notified.", "warn");
      logViolation("no_camera", "Camera access was denied or unavailable.", "critical", true);
      return false;
    }
  }

  async function loadModels() {
    setStatus("Preparing secure environment…", "warn");
    await faceapi.nets.tinyFaceDetector.loadFromUri(cfg.faceModelBaseUrl);
    await faceapi.nets.faceLandmark68TinyNet.loadFromUri(cfg.faceModelBaseUrl);

    await cocoSsd.load({ base: "lite_mobilenet_v2" }).then((model) => { cocoModel = model; });

    modelsReady = true;
    setStatus("Monitoring is live", "ok");
  }

  function estimateLooksAway(landmarks) {
    const nose = landmarks.getNose();
    const jaw = landmarks.getJawOutline();
    const leftEdge = jaw[0].x;
    const rightEdge = jaw[16].x;
    const noseX = nose[3].x;
    const faceWidth = rightEdge - leftEdge;
    if (faceWidth <= 0) return false;
    const relative = (noseX - leftEdge) / faceWidth;
    return relative < 0.32 || relative > 0.68;
  }

  let awayFrames = 0;
  let noFaceFrames = 0;

  async function faceLoop() {
    if (!modelsReady) return;

    const options = new faceapi.TinyFaceDetectorOptions({ inputSize: 224 });
    const detections = await faceapi
      .detectAllFaces(video, options)
      .withFaceLandmarks(true);

    const ctx = overlay.getContext("2d");
    ctx.clearRect(0, 0, overlay.width, overlay.height);

    if (detections.length === 0) {
      noFaceFrames += 1;
      awayFrames = 0;
      if (noFaceFrames >= 3) {
        logViolation("NO_FACE", "No face detected in frame.", "critical");
        setStatus("Please sit in front of the camera", "warn");
      }
    } else if (detections.length > 1) {
      noFaceFrames = 0;
      logViolation("MULTIPLE_FACE", `${detections.length} faces detected in frame.`, "critical");
      setStatus("Only one person may be in frame", "warn");
      faceapi.draw.drawDetections(overlay, detections.map((d) => d.detection));
    } else {
      noFaceFrames = 0;
      faceapi.draw.drawDetections(overlay, detections.map((d) => d.detection));
      const away = estimateLooksAway(detections[0].landmarks);
      if (away) {
        awayFrames += 1;
        if (awayFrames >= 4) {
          logViolation("look_away", "Candidate appears to be looking away from the screen.", "warning");
        }
      } else {
        awayFrames = 0;
        setStatus("Monitoring is live", "ok");
      }
    }

    setTimeout(faceLoop, 400);
  }

  async function objectLoop() {
    if (!cocoModel) return;
    try {
      const predictions = await cocoModel.detect(video);
      const suspicious = predictions.filter((p) =>
        ["cell phone", "laptop", "book", "tablet"].includes(p.class) && p.score > 0.55
      );
      suspicious.forEach((p) => {
        // Recorded only — never surfaced to the student, to keep the exam screen calm.
        logViolation(
          "DEVICE_DETECTED",
          `Possible ${p.class} detected in frame (${Math.round(p.score * 100)}% confidence).`,
          "critical"
        );
      });
    } catch (e) {
      // model warm-up hiccups are non-fatal
    }
    setTimeout(objectLoop, 2500);
  }

  async function start() {
    const camOk = await initCamera();
    if (!camOk) return;
    try {
      await loadModels();
      faceLoop();
      objectLoop();
    } catch (err) {
      setStatus("Proctoring unavailable — flagged for lecturer review", "warn");
      logViolation("MODEL_ERROR", "One or more proctoring models could not be loaded.", "critical", true);
      console.error("Could not load proctoring models", err);
    }
  }

  window.addEventListener("pagehide", () => {
    shuttingDown = true;
    if (video.srcObject) video.srcObject.getTracks().forEach((track) => track.stop());
  });

  document.addEventListener("DOMContentLoaded", start);
})();
