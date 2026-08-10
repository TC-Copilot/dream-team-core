// Shared PWA bootstrap: registers the service worker and shows an online/offline status pill.
// Included on every page via <script src="pwa.js" defer></script>.
(() => {
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
