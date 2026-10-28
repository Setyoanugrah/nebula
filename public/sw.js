/* Nebula — parallel range downloader.
 * Menjahit beberapa permintaan Range jadi satu stream berurutan, jadi browser
 * menyimpannya langsung ke disk (memory konstan) sambil tetap paralel.
 * Hanya menangani /sw-dl; semua permintaan lain diteruskan apa adanya.
 */

const BLOCK = 8 * 1024 * 1024;

self.addEventListener("install", (e) => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname !== "/sw-dl") return;
  event.respondWith(serve(url));
});

async function serve(url) {
  const target = url.searchParams.get("u");
  const size = Number(url.searchParams.get("s") || 0);
  const name = url.searchParams.get("n") || "download";
  const par = Math.min(8, Math.max(1, Number(url.searchParams.get("p") || 6)));

  if (!target || !size) {
    return fetch(target || "/", { credentials: "include" });
  }

  const stream = new ReadableStream(
    {
      async start(controller) {
        try {
          await pump(controller, target, size, par);
          controller.close();
        } catch (err) {
          controller.error(err);
        }
      },
    },
    { highWaterMark: 1 }
  );

  return new Response(stream, {
    status: 200,
    headers: {
      "content-type": "application/octet-stream",
      "content-length": String(size),
      "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(name)}`,
      "cache-control": "no-store",
      "x-nebula-parallel": String(par),
    },
  });
}

async function pump(controller, target, size, par) {
  const total = Math.ceil(size / BLOCK);
  const pending = new Map();
  let nextFetch = 0;

  const launch = () => {
    while (pending.size < par && nextFetch < total) {
      const i = nextFetch++;
      const a = i * BLOCK;
      const b = Math.min(a + BLOCK, size) - 1;
      const p = fetch(target, {
        credentials: "include",
        headers: { Range: `bytes=${a}-${b}` },
      }).then(async (res) => {
        if (res.status !== 206 && res.status !== 200) {
          throw new Error("range " + res.status);
        }
        return res.arrayBuffer();
      });
      p.catch(() => {}); // jangan unhandled rejection saat menunggu urutan
      pending.set(i, p);
    }
  };

  for (let i = 0; i < total; i++) {
    launch();
    const p = pending.get(i);
    pending.delete(i);
    if (!p) throw new Error("blok hilang " + i);
    const buf = await p;
    controller.enqueue(new Uint8Array(buf));
  }
}
