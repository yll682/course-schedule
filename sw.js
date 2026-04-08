const CACHE = 'kechenbiao-v10';
const STATIC = ['/', '/index.html', '/login.html', '/admin.html', '/style.css', '/icon.svg', '/manifest.json'];

self.addEventListener('install', e => {
    e.waitUntil(
        caches.open(CACHE).then(c => c.addAll(STATIC))
    );
    self.skipWaiting();
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

    // API 请求：网络优先，不走缓存
    if (url.pathname.startsWith('/api/')) {
        return;
    }

    // 所有资源（包括外部字体）：网络优先，失败时回退缓存
    e.respondWith(
        fetch(e.request)
            .then(resp => {
                // 缓存成功的响应
                if (resp.ok) {
                    // 先克隆响应，再缓存，避免 "Response body is already used" 错误
                    const respToCache = resp.clone();
                    caches.open(CACHE).then(cache => {
                        cache.put(e.request, respToCache);
                    });
                }
                return resp;
            })
            .catch(err => {
                // 网络失败时，尝试从缓存读取
                console.log('Fetch failed, trying cache:', e.request.url);
                return caches.match(e.request).then(cached => {
                    if (cached) {
                        return cached;
                    }
                    // 如果缓存也没有，返回错误响应
                    return new Response('', { status: 408, statusText: 'Network error and not in cache' });
                });
            })
    );
});
