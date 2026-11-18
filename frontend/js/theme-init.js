// Runs before first paint (classic script, CSP-safe) so the saved theme never flashes.
(function () {
  try {
    var saved = localStorage.getItem("st.theme");
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  } catch (e) { /* storage unavailable: follow the OS preference */ }
})();
