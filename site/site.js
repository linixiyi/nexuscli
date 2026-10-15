// NexusCLI site behaviors: theme toggle + scroll-reveal + TOC highlighting
(function () {
  "use strict";

  // ----- Theme toggle (persisted) -----
  var saved = null;
  try { saved = localStorage.getItem("nexuscli-theme"); } catch (e) {}
  var prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  var theme = saved || (prefersLight ? "light" : "dark");
  document.documentElement.setAttribute("data-theme", theme);
  syncBtn();

  var btn = document.getElementById("themeToggle");
  if (btn) {
    btn.addEventListener("click", function () {
      theme = theme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", theme);
      try { localStorage.setItem("nexuscli-theme", theme); } catch (e) {}
      syncBtn();
    });
  }
  function syncBtn() {
    if (!btn) return;
    btn.textContent = theme === "dark" ? "☀" : "☾";
    btn.setAttribute("aria-label", theme === "dark" ? "切换到浅色主题" : "切换到深色主题");
  }

  // ----- Scroll reveal -----
  var reveals = document.querySelectorAll(".rv");
  if ("IntersectionObserver" in window && reveals.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add("in");
          io.unobserve(en.target);
        }
      });
    }, { threshold: 0.08 });
    reveals.forEach(function (el) { io.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add("in"); });
  }

  // ----- TOC scroll spy (doc pages) -----
  var toc = document.querySelector(".toc");
  if (!toc) return;
  var links = Array.prototype.slice.call(toc.querySelectorAll('a[href^="#"]'));
  if (!links.length) return;
  var heads = links
    .map(function (a) {
      var id = decodeURIComponent(a.getAttribute("href").slice(1));
      var h = document.getElementById(id);
      return h ? { link: a, head: h } : null;
    })
    .filter(Boolean);
  if (!heads.length) return;

  var active = null;
  function pick() {
    var y = window.scrollY + 120;
    var current = heads[0];
    for (var i = 0; i < heads.length; i++) {
      if (heads[i].head.getBoundingClientRect().top + window.scrollY <= y) current = heads[i];
    }
    if (current !== active) {
      if (active) active.link.classList.remove("on");
      active = current;
      if (active) active.link.classList.add("on");
    }
  }
  var ticking = false;
  window.addEventListener("scroll", function () {
    if (!ticking) {
      ticking = true;
      window.requestAnimationFrame(function () { pick(); ticking = false; });
    }
  }, { passive: true });
  pick();
})();
