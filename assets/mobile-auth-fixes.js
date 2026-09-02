/* Mobile login visibility and auth back navigation. */
(() => {
  const originalAuthPage = window.authPage;
  if (typeof originalAuthPage === 'function') {
    window.authPage = function(mode) {
      const html = originalAuthPage(mode);
      return html.replace(
        '<main class="auth-form-panel"><section class="auth-card">',
        '<main class="auth-form-panel"><button class="auth-mobile-back" type="button" onclick="go(\'dashboard\')" aria-label="Go back to TalentX home">← Back</button><section class="auth-card">'
      );
    };
  }
})();
