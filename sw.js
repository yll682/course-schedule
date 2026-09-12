const CACHE = 'kechenbiao-v24';
const STATIC = ['/', '/index.html', '/login.html', '/admin.html', '/style.css', '/non-critical.css', '/icon.svg', '/manifest.json', '/ibm-plex-sans-sc.css'];
const FONT_URLS = [
    '/fonts/IBMPlexSansSC-Regular.woff2',
    '/fonts/IBMPlexSansSC-Medium.woff2',
    '/fonts/IBMPlexSansSC-SemiBold.woff2',
    '/fonts/IBMPlexSansSC-Bold.woff2'
];

self.addEventListener('install', e => {
    e.waitUntil(
        caches.open(CACHE).then(c => {
            // 先缓存静态资源（cache: 'reload' 绕过浏览器 HTTP 缓存，确保拿到最新版本）
            return c.addAll(STATIC.map(u => new Request(u, { cache: 'reload' }))).then(() => {
                // 然后缓存字体（字体较大，独立处理）
                return Promise.all(
                    FONT_URLS.map(url =>
                        fetch(url).then(resp => {
                            if (resp.ok) {
                                return c.put(url, resp);
                            }
                        }).catch(err => {
                            console.log('字体缓存失败:', url, err);
                        })
                    )
                );
            });
        })
    );
    self.skipWaiting();
});

self.addEventListener('message', e => {
    if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
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

    // 字体文件：缓存优先，提高加载速度
    if (url.pathname.startsWith('/fonts/')) {
        e.respondWith(
            caches.match(e.request).then(cached => {
                if (cached) {
                    // 有缓存就直接用，后台异步更新
                    fetch(e.request).then(resp => {
                        if (resp.ok) {
                            caches.open(CACHE).then(cache => cache.put(e.request, resp));
                        }
                    }).catch(() => {});
                    return cached;
                }
                // 无缓存则网络请求并缓存
                return fetch(e.request).then(resp => {
                    if (resp.ok) {
                        const respToCache = resp.clone();
                        caches.open(CACHE).then(cache => cache.put(e.request, respToCache));
                    }
                    return resp;
                });
            })
        );
        return;
    }

    // 只缓存同源资源，外部字体等由浏览器 HTTP 缓存处理
    if (url.origin !== location.origin) {
        return;
    }

    // HTML 页面：网络优先（绕过 HTTP 缓存），离线时回退到缓存。
    // 页面结构更新必须立即可见，不能等 stale-while-revalidate 的下一次访问
    const isPage = e.request.mode === 'navigate' || url.pathname.endsWith('.html') || url.pathname === '/';
    if (isPage) {
        e.respondWith(
            fetch(e.request, { cache: 'no-store' })
                .then(resp => {
                    if (resp.ok) {
                        const respToCache = resp.clone();
                        caches.open(CACHE).then(cache => cache.put(e.request, respToCache));
                    }
                    return resp;
                })
                .catch(() => caches.match(e.request).then(cached =>
                    cached || new Response('', { status: 408, statusText: 'Network error' })
                ))
        );
        return;
    }

    // 其余同源静态资源：缓存优先（性能优化）
    e.respondWith(
        caches.match(e.request).then(cached => {
            if (cached) {
                // 有缓存就先用，后台异步更新（stale-while-revalidate，绕过 HTTP 缓存）
                fetch(e.request, { cache: 'no-cache' }).then(resp => {
                    if (resp.ok) {
                        caches.open(CACHE).then(cache => cache.put(e.request, resp));
                    }
                }).catch(() => {});
                return cached;
            }
            // 无缓存则网络请求
            return fetch(e.request)
                .then(resp => {
                    if (resp.ok) {
                        const respToCache = resp.clone();
                        caches.open(CACHE).then(cache => cache.put(e.request, respToCache));
                    }
                    return resp;
                })
                .catch(err => {
                    console.log('Fetch failed:', e.request.url);
                    return new Response('', { status: 408, statusText: 'Network error' });
                });
        })
    );
});
