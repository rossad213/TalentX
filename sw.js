/* TalentX PWA service worker.
 * Keep market/catalog data network-owned so installability never reintroduces
 * the mobile memory or stale-data problems solved by the lightweight client.
 */
const CACHE_NAME = 'talentx-shell-20260811-1';
const APP_ROOT = new URL('./', self.location.href).pathname;

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith('talentx-shell-') && key !== CACHE_NAME).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

function isDataRequest(url) {
  return url.pathname.startsWith(`${APP_ROOT}data/`) || url.pathname.includes('/TalentX/data/');
}

function isCacheableShell(request, url) {
  if (url.origin !== self.location.origin) return false;
  if (isDataRequest(url)) return false;
  return ['style', 'script', 'image', 'font', 'manifest'].includes(request.destination);
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // TalentX market data must always use the browser's live lightweight loader.
  if (isDataRequest(url)) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request));
    return;
  }

  if (!isCacheableShell(request, url)) return;

  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    try {
      const response = await fetch(request);
      if (response && response.ok) cache.put(request, response.clone()).catch(() => {});
      return response;
    } catch (error) {
      const cached = await cache.match(request);
      if (cached) return cached;
      throw error;
    }
  })());
});
