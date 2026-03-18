const CACHE = 'kechenbiao-v3';
const STATIC = ['/', '/index.html', '/login.html', '/admin.html', '/style.css', '/icon.svg', '/manifest.json'];

self.addEventListener('install', e => {
    e.waitUntil(
        caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', e => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', e => {
    const url = new URL(e.request.url);

    // API 请求：网络优先，离线直接返回（不走缓存，让页面自己处理错误）
    if (url.pathname.startsWith('/api/')) return;

    // 静态资源：网络优先，失败时回退缓存（离线可用）
    e.respondWith(
        fetch(e.request).then(resp => {
            if (resp.ok) {
                caches.open(CACHE).then(c => c.put(e.request, resp.clone()));
            }
            return resp;
        }).catch(() => caches.match(e.request))
    );
});
