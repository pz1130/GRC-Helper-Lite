(function () {
  try {
    var theme = localStorage.getItem("grc.theme") || (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.style.colorScheme = theme;
  } catch (error) {
    // Storage may be unavailable in hardened browser contexts; the CSS default remains usable.
  }
})();
