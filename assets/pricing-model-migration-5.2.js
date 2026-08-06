(() => {
  const marker = 'talentx_pricing_migration_5_2';
  const stateKey = 'talentx_v2_state';
  try {
    if (localStorage.getItem(marker)) return;
    const parsed = JSON.parse(localStorage.getItem(stateKey) || 'null');
    if (parsed && typeof parsed === 'object') {
      parsed.prices = {};
      // app.js still owns the local-state schema version. Keep that value so
      // holdings, cash, watchlists, and transactions remain intact.
      parsed.pricingModelVersion = '4.1-event-driven-pricing';
      localStorage.setItem(stateKey, JSON.stringify(parsed));
    }
    localStorage.setItem(marker, new Date().toISOString());
  } catch {
    // A damaged or unavailable localStorage entry should never block the site.
  }
})();
