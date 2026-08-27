(() => {
  const STATE_KEY = 'talentx_v2_state';
  const REPAIR_KEY = 'talentx_price_state_repair_20260827_v1';

  try {
    if (localStorage.getItem(REPAIR_KEY) === 'done') return;

    const raw = localStorage.getItem(STATE_KEY);
    if (raw) {
      const state = JSON.parse(raw);
      if (state && typeof state === 'object') {
        // Server/catalog prices are authoritative after the Aug 27 Sports
        // event-price repair. Preserve the user's account/portfolio state, but
        // discard stale browser-side price overrides that can otherwise mask a
        // newly published catalog indefinitely.
        state.prices = {};
        localStorage.setItem(STATE_KEY, JSON.stringify(state));
      }
    }

    localStorage.setItem(REPAIR_KEY, 'done');
  } catch (error) {
    // A malformed local prototype state should never stop TalentX from loading.
    console.warn('TalentX local price-state repair skipped:', error);
  }
})();
