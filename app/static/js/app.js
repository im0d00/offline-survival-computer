/* =========================================================
   Offline Survival Computer – Global JavaScript
   ========================================================= */

"use strict";

// Mobile sidebar toggle with improved interactions
(function () {
  const toggleBtn = document.getElementById("sidebarToggle");
  const sidebar = document.getElementById("sidebar");

  if (!toggleBtn || !sidebar) return;

  // Toggle sidebar visibility
  toggleBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    const isExpanded = sidebar.classList.toggle("show");
    toggleBtn.setAttribute("aria-expanded", isExpanded);
  });

  // Close sidebar when clicking outside on mobile
  document.addEventListener("click", function (e) {
    if (
      window.innerWidth < 768 &&
      sidebar.classList.contains("show") &&
      !sidebar.contains(e.target) &&
      e.target !== toggleBtn
    ) {
      sidebar.classList.remove("show");
      toggleBtn.setAttribute("aria-expanded", "false");
    }
  });

  // Close sidebar when pressing Escape key
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && sidebar.classList.contains("show")) {
      sidebar.classList.remove("show");
      toggleBtn.setAttribute("aria-expanded", "false");
    }
  });
})();

// Smooth scroll behavior for anchor links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
  anchor.addEventListener("click", function (e) {
    const target = document.querySelector(this.getAttribute("href"));
    if (target) {
      e.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
});

// Auto-dismiss alerts after 5 seconds
document.querySelectorAll(".alert:not(.alert-permanent)").forEach(alert => {
  setTimeout(() => {
    const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
    bsAlert?.close();
  }, 5000);
});
