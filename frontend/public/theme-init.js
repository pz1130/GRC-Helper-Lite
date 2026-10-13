(function () {
  try {
    var theme = localStorage.getItem("grc.theme") || "dark";
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.style.colorScheme = theme;
  } catch (error) {
    // Storage may be unavailable in hardened browser contexts; the CSS default remains usable.
  }
})();
