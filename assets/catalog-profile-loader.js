/* TalentX compact-catalog profile hydration.
 * app.js can browse/search compact records. When a profile view is rendered,
 * hydrate that one record from its small full-data shard, then re-render.
 */
(() => {
  const SHARD_COUNT = 128;
  const loaded = new Set();
  const loading = new Map();

  function bucketFor(value) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < value.length; i += 1) {
      h ^= value.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h % SHARD_COUNT;
  }

  async function hydrate(id) {
    const record = typeof byId === 'function' ? byId(id) : null;
    if (!record || !record.__compact || loaded.has(id)) return record;
    if (loading.has(id)) return loading.get(id);

    const task = (async () => {
      const shard = String(bucketFor(id)).padStart(3, '0');
      const localUrl = `./data/profile_shards/${shard}.json`;
      let res = await fetch(localUrl, {cache: 'no-store'});
      if (!res.ok && window.__talentxDataFallbackBase) {
        res = await fetch(`${window.__talentxDataFallbackBase}/profile_shards/${shard}.json`, {cache: 'no-store'});
      }
      if (!res.ok) throw new Error(`Profile shard ${shard} could not load`);
      const payload = await res.json();
      const full = payload && payload[id];
      if (!full) throw new Error(`Profile ${id} missing from shard ${shard}`);
      Object.keys(record).forEach(key => delete record[key]);
      Object.assign(record, full);
      loaded.add(id);
      return record;
    })().finally(() => loading.delete(id));

    loading.set(id, task);
    return task;
  }

  if (typeof profile !== 'function') return;
  const originalProfile = profile;

  profile = function compactAwareProfile() {
    const record = typeof byId === 'function' ? byId(selectedId) : null;
    if (!record || !record.__compact) return originalProfile();

    hydrate(selectedId)
      .then(() => {
        if (route === 'profile' && selectedId === record.id && typeof render === 'function') render();
      })
      .catch(err => {
        console.error('TalentX profile hydration failed', err);
        const app = document.querySelector('#app');
        if (app && route === 'profile') {
          app.innerHTML = '<div class="card empty"><strong>Profile details could not load.</strong><br>Please try again.</div>';
        }
      });

    return '<div class="card loading">Loading profile details…</div>';
  };

  window.TalentXProfileHydration = {hydrate, bucketFor};
})();
