(() => {
  const marker = 'talentx_pricing_repair_2026_08_27_v2';
  const stateKey = 'talentx_v2_state';
  try {
    if (localStorage.getItem(marker)) return;
    const parsed = JSON.parse(localStorage.getItem(stateKey) || 'null');
    if (parsed && typeof parsed === 'object') {
      // The Aug 27 pricing repair replaced corrupted server-side Sports prices.
      // Browser-side prototype price overrides from the old model must not mask
      // those corrected catalog values. Preserve account/portfolio state and
      // clear only the stale local price map.
      parsed.prices = {};
      parsed.pricingModelVersion = '4.1-event-driven-pricing';
      localStorage.setItem(stateKey, JSON.stringify(parsed));
    }
    localStorage.setItem(marker, new Date().toISOString());
  } catch {
    // A damaged or unavailable localStorage entry should never block the site.
  }
})();
