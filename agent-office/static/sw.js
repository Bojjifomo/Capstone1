// Service worker: only push notifications. No caching, so a new server version shows up at once.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  event.waitUntil((async () => {
    let data = {};
    try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
    if (!data.title) {
      try { data = await (await fetch("/api/push/last")).json(); } catch (e) { data = {}; }
    }
    await self.registration.showNotification(data.title || "The Guild Hall", {
      body: data.body || "",
      tag: data.tag || "office",
      data: { url: typeof data.url === "string" && data.url.startsWith("/") ? data.url : "/" },
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/icon-192.png",
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const win of wins) {
      if ("focus" in win) {
        if ("navigate" in win) await win.navigate(url);
        return win.focus();
      }
    }
    return self.clients.openWindow(url);
  })());
});
