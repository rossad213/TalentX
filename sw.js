/* TalentX emergency service-worker cleanup.
 * This worker intentionally unregisters itself and clears prior TalentX shell
 * caches so Safari returns to the known-good lightweight web experience.
 */
self.addEventListener('install', event => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith('talentx-shell-')).map(key => caches.delete(key)));
    await self.registration.unregister();
    const clients = await self.clients.matchAll({type:'window'});
    for (const client of clients) {
      try { client.postMessage({type:'TALENTX_SW_REMOVED'}); } catch {}
    }
  })());
});

/* Do not intercept requests while cleanup is taking place. */
