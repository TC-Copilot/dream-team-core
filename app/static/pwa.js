// Shared PWA bootstrap: registers the service worker and shows an online/offline status pill.
// Included on every page via <script src="pwa.js" defer></script>.
(() => {
  async function refreshCaches() {
    if (!("serviceWorker" in navigator)) {
      throw new Error("Service workers are not available in this browser.");
    }
    const registration = await navigator.serviceWorker.ready;
    const worker = navigator.serviceWorker.controller || registration.active;
    if (!worker) throw new Error("The PWA service worker is not active yet.");
    await new Promise((resolve, reject) => {
      const channel = new MessageChannel();
      const timer = setTimeout(() => reject(new Error("PWA cache refresh timed out.")), 10000);
      channel.port1.onmessage = (event) => {
        clearTimeout(timer);
        if (event.data?.ok) resolve(event.data);
        else reject(new Error(event.data?.error || "PWA cache refresh failed."));
      };
      worker.postMessage({ type: "REFRESH_APP_CACHES" }, [channel.port2]);
    });
    await registration.update();
  }

  window.DreamTeamPwa = Object.freeze({ refreshCaches });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch((err) => {
        console.warn("Service worker registration failed:", err);
      });
    });
  }

  function renderStatusPill() {
    if (document.getElementById("pwaStatusPill")) return;
    const pill = document.createElement("div");
    pill.id = "pwaStatusPill";
    pill.setAttribute("role", "status");
    pill.setAttribute("aria-live", "polite");
    pill.style.cssText = [
      "position:fixed", "bottom:14px", "right:14px", "z-index:9999",
      "font:600 12px/1 system-ui,sans-serif", "padding:6px 12px", "border-radius:999px",
      "box-shadow:0 4px 14px rgba(0,0,0,.18)", "transition:opacity .2s ease",
      "pointer-events:none", "opacity:0",
    ].join(";");
    document.body.appendChild(pill);
    return pill;
  }

  function updateStatus() {
    const pill = document.getElementById("pwaStatusPill") || renderStatusPill();
    if (!pill) return;
    if (navigator.onLine) {
      // Only flash "Back online" briefly; hide instead of permanently occupying screen space.
      pill.textContent = "● Back online";
      pill.style.background = "#16a34a";
      pill.style.color = "#fff";
      pill.style.opacity = "1";
      clearTimeout(pill._hideTimer);
      pill._hideTimer = setTimeout(() => { pill.style.opacity = "0"; }, 2500);
    } else {
      pill.textContent = "● Offline — showing cached data";
      pill.style.background = "#dc2626";
      pill.style.color = "#fff";
      pill.style.opacity = "1";
      clearTimeout(pill._hideTimer);
    }
  }

  window.addEventListener("online", updateStatus);
  window.addEventListener("offline", updateStatus);
  document.addEventListener("DOMContentLoaded", () => {
    if (!navigator.onLine) updateStatus();
  });
})();
