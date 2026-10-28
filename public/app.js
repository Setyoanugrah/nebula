const $ = (s) => document.querySelector(s);
const state = { me: null, folder: "", q: "", files: [], folders: [], mode: "login", delay: 60, view: "" };

const I = {
  image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="m4 17 5-5 4 4 3-3 4 4"/></svg>',
  video: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3z"/></svg>',
  audio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M9 17V6l10-2v11"/><circle cx="6.5" cy="17.5" r="2.5"/><circle cx="16.5" cy="15.5" r="2.5"/></svg>',
  pdf: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/></svg>',
  archive: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v4m0 3v3m0 3v2"/></svg>',
  text: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 4v11m0 0 4-4m-4 4-4-4"/><path d="M5 19h14"/></svg>',
  share: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="17" cy="6" r="2.6"/><circle cx="7" cy="12" r="2.6"/><circle cx="17" cy="18" r="2.6"/><path d="m9.3 10.7 5.4-3.2M9.3 13.3l5.4 3.2"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m4 20 4-1 10-10-3-3L5 16z"/><path d="m14 6 3 3"/></svg>',
  restore: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 12a8 8 0 1 0 3-6.2"/><path d="M4 4v5h5"/></svg>',
};

function iconFor(name, mime) {
  const e = (name.split(".").pop() || "").toLowerCase();
  if ((mime || "").startsWith("image/")) return ["image", I.image];
  if ((mime || "").startsWith("video/")) return ["video", I.video];
  if ((mime || "").startsWith("audio/")) return ["audio", I.audio];
  if (e === "pdf") return ["pdf", I.pdf];
  if (["zip", "rar", "7z", "tar", "gz", "xz"].includes(e)) return ["archive", I.archive];
  if (["txt", "md", "json", "csv", "log", "js", "ts", "py", "html", "css"].includes(e)) return ["text", I.text];
  return ["file", I.file];
}

const size = (b) => {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024)));
  return (b / 1024 ** i).toFixed(i ? 1 : 0) + " " + u[i];
};
const when = (t) => new Date(t * 1000).toLocaleDateString("id-ID", { day: "2-digit", month: "short" });

function toast(msg, bad) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 3800);
}

const FNV_OFF = 0x811c9dc5, FNV_PRIME = 0x01000193;
function fnvUpdate(h, str) {
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, FNV_PRIME) >>> 0; }
  return h;
}
let powToken = null;
function resetVerify() {
  powToken = null;
  const box = $("#verifyBox"), txt = $("#vText"), btn = $("#authBtn"), chk = $("#verifyCheck");
  if (box) box.classList.remove("ok", "busy");
  if (txt) txt.textContent = "Verifikasi keamanan";
  if (btn) btn.disabled = true;
  if (chk) chk.checked = false;
}
async function solveChallenge() {
  const box = $("#verifyBox"), txt = $("#vText"), btn = $("#authBtn"), chk = $("#verifyCheck");
  powToken = null;
  if (btn) btn.disabled = true;
  if (!box) return;
  box.classList.remove("ok");
  box.classList.add("busy");
  if (txt) txt.textContent = "memeriksa…";
  try {
    const c = await api("/api/challenge");
    await new Promise((r) => setTimeout(r, 20));
    const mask = (1 << c.bits) - 1;
    const base = fnvUpdate(FNV_OFF, c.seed);
    let found = null;
    for (let n = 0; n < 4000000; n++) {
      if ((fnvUpdate(base, String(n)) & mask) === 0) { found = c.seed + ":" + n; break; }
    }
    if (!found) throw new Error("timeout");
    powToken = found;
    box.classList.remove("busy");
    box.classList.add("ok");
    if (txt) txt.textContent = "Terverifikasi";
    if (btn) btn.disabled = false;
  } catch (e) {
    box.classList.remove("busy");
    if (txt) txt.textContent = "Gagal — coba lagi";
    if (chk) chk.checked = false;
  }
}

async function apiRetry(path, opts = {}, tries = 4) {
  let last;
  for (let i = 0; i < tries; i++) {
    try { return await api(path, opts); }
    catch (e) {
      last = e;
      const msg = String((e && e.message) || "");
      const net = e instanceof TypeError || /fetch|network|load failed|macet|dibatalkan|timeout|putus/i.test(msg);
      if (!net) throw e;
      await sleep(900 * (i + 1));
    }
  }
  throw last;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "content-type": "application/json" }, ...opts });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || "error " + r.status);
  return d;
}

let limitShown = false;
function showLimitNote() {
  if (limitShown) return;
  limitShown = true;
  const n = $("#limitNote");
  if (!n) return;
  requestAnimationFrame(() => n.classList.add("show"));
}

function setMe(me) {
  state.me = me;
  if (!me) return;
  showLimitNote();
  $("#whoami").textContent = "@" + me.username;
  const p = me.quota ? Math.min(100, (me.used / me.quota) * 100) : 0;
  $("#pct").textContent = p.toFixed(p < 10 && p > 0 ? 1 : 0) + "%";
  $("#barFill").style.width = p + "%";
  $("#usage").textContent = size(me.used) + " / " + size(me.quota);
}

async function loadFiles() {
  const qs = new URLSearchParams({ folder: state.folder, q: state.q });
  if (state.view === "trash") qs.set("view", "trash");
  const d = await api("/api/files?" + qs);
  state.files = d.files;
  state.folders = d.folders;
  if (d.del) state.delay = d.del;
  render();
}

function render() {
  const grid = $("#grid");
  grid.innerHTML = "";
  const files = state.files;
  const trashed = state.view === "trash";
  const folders = trashed ? [] : state.folders.filter((f) => f.name !== state.folder);
  $("#crumb").textContent = trashed ? "tong sampah" : state.folder || "Semua file";
  $("#count").textContent = files.length ? files.length + " file" : "";

  if (!state.q && !trashed) {
    folders.forEach((f) => {
      const el = document.createElement("div");
      el.className = "card";
      el.innerHTML = `<div class="thumb t-icon-file" data-open="${encodeURIComponent(f.name)}">${I.folder}</div>
        <div class="meta"><div class="name">${esc(f.name)}</div><div class="sub"><span>folder</span></div></div>
        <div class="actions"><button class="act del" data-delfolder="${f.id}" title="hapus folder">${I.trash}</button></div>`;
      grid.append(el);
    });
  }

  files.forEach((f) => {
    const [cls, svg] = iconFor(f.name, f.mime);
    const ready = (f.status || "ready") === "ready";
    const isImg = (f.mime || "").startsWith("image/");
    const badge =
      f.status === "pending"
        ? '<span class="badge wip">memproses</span>'
        : f.status === "failed"
        ? '<span class="badge fail">gagal</span>'
        : f.share_token
        ? '<span class="badge">dibagikan</span>'
        : "";
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = `
      <div class="thumb ${isImg && ready ? "" : "t-icon-" + cls}" ${ready ? `data-preview="${f.id}"` : ""}>
        ${isImg && ready ? `<img loading="lazy" src="/api/file/${f.id}/view" alt="">` : svg}
        ${badge}
      </div>
      <div class="meta">
        <div class="name" title="${esc(f.name)}">${esc(f.name)}</div>
        <div class="sub"><span>${size(f.size)}</span><span>${when(f.created_at)}</span></div>
      </div>
      <div class="actions">
        ${
          trashed
            ? `<button class="act" data-restore="${f.id}" title="pulihkan">${I.restore}</button>
        <button class="act del" data-purge="${f.id}" title="hapus permanen">${I.trash}</button>`
            : ready
            ? `<button class="act" data-dl="${f.id}" title="unduh">${I.download}</button>
        <button class="act" data-share="${f.id}" title="bagikan">${I.share}</button>
        <button class="act" data-rename="${f.id}" title="ganti nama">${I.edit}</button>
        <button class="act del" data-del="${f.id}" title="pindah ke tong sampah">${I.trash}</button>`
            : `<button class="act del" data-del="${f.id}" title="pindah ke tong sampah">${I.trash}</button>`
        }
      </div>`;
    grid.append(el);
  });

  grid.querySelectorAll("[data-preview]").forEach((n) =>
    n.addEventListener("click", () => preview(n.dataset.preview))
  );
  grid.querySelectorAll("[data-open]").forEach((n) =>
    n.addEventListener("click", () => { state.folder = decodeURIComponent(n.dataset.open); state.q = ""; $("#q").value = ""; closeSide(); loadFiles(); })
  );
  grid.querySelectorAll("[data-dl]").forEach((n) =>
    n.addEventListener("click", () => {
      const f = state.files.find((x) => x.id === n.dataset.dl);
      if (f) parallelDownload(f);
    })
  );
  grid.querySelectorAll("[data-share]").forEach((n) =>
    n.addEventListener("click", () => shareFile(n.dataset.share))
  );
  grid.querySelectorAll("[data-rename]").forEach((n) =>
    n.addEventListener("click", () => renameFile(n.dataset.rename))
  );
  grid.querySelectorAll("[data-del]").forEach((n) =>
    n.addEventListener("click", () => delFile(n.dataset.del))
  );
  grid.querySelectorAll("[data-restore]").forEach((n) =>
    n.addEventListener("click", async () => {
      try { await api(`/api/file/${n.dataset.restore}/restore`, { method: "POST" }); toast("file dipulihkan"); loadFiles(); refreshTrash(); }
      catch (e) { toast(e.message, 1); }
    })
  );
  grid.querySelectorAll("[data-purge]").forEach((n) =>
    n.addEventListener("click", async () => {
      if (!confirm("Hapus permanen? Tidak bisa dibatalkan.")) return;
      try { await api(`/api/file/${n.dataset.purge}/purge`, { method: "DELETE" }); toast("dihapus permanen"); loadFiles(); refreshTrash(); }
      catch (e) { toast(e.message, 1); }
    })
  );
  grid.querySelectorAll("[data-delfolder]").forEach((n) =>
    n.addEventListener("click", () => delFolder(n.dataset.delfolder))
  );

  $("#empty").classList.toggle("hidden", files.length > 0 || (!state.q && folders.length > 0));
  grid.classList.toggle("hidden", files.length === 0 && (state.q || folders.length === 0));
  renderFolders(folders);
}

function renderFolders(folders) {
  const box = $("#folderList");
  box.innerHTML = "";
  const all = [{ name: "", label: "Semua file" }].concat(state.folders);
  const items = [{ name: "", label: "Semua file" }].concat(folders);
  const list = state.folder ? items : items;
  items.forEach((f) => {
    if (f.name === state.folder) return;
    const b = document.createElement("button");
    b.className = "nav";
    b.textContent = f.name || "Semua file";
    b.addEventListener("click", () => { state.folder = f.name; state.q = ""; $("#q").value = ""; closeSide(); loadFiles(); });
    box.append(b);
  });
  document.querySelectorAll(".nav[data-folder]").forEach((n) => n.classList.toggle("active", state.folder === ""));
}

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function closeSide() { $(".side").classList.remove("open"); }

/* ---------- modals ---------- */
function modal(html) {
  $("#modal").innerHTML = html;
  $("#overlay").classList.remove("hidden");
  $("#overlay").onclick = (e) => { if (e.target.id === "overlay") closeModal(); };
}
function closeModal() { $("#overlay").classList.add("hidden"); $("#modal").innerHTML = ""; }

async function preview(id) {
  const f = state.files.find((x) => x.id === id);
  if (!f) return;
  const m = f.mime || "";
  const url = `/api/file/${id}/view`;
  let body = "";
  if (m.startsWith("image/")) body = `<img src="${url}" alt="">`;
  else if (m.startsWith("video/")) body = `<video src="${url}" controls autoplay playsinline></video>`;
  else if (m.startsWith("audio/")) body = `<audio src="${url}" controls autoplay></audio>`;
  else if (m === "application/pdf") body = `<iframe src="${url}"></iframe>`;
  else if (m.startsWith("text/") || /\.(txt|md|json|csv|log|js|py|html|css)$/i.test(f.name)) {
    try { body = `<pre>${esc(await (await fetch(url)).text())}</pre>`; } catch { body = `<p class="muted">gagal memuat</p>`; }
  } else body = `<p class="muted">tidak ada pratinjau. <a href="/api/file/${id}/download">unduh</a></p>`;
  modal(`<h3>${esc(f.name)}</h3><div class="preview">${body}</div>
    <div class="foot"><span class="muted" style="margin-right:auto">${size(f.size)}</span>
    <button class="btn" id="dlModal">unduh</button><button class="btn" onclick="closeModal()">tutup</button></div>`);
  const dlb = $("#dlModal");
  if (dlb) dlb.onclick = () => { closeModal(); parallelDownload(f); };
}

async function shareFile(id) {
  const d = await api(`/api/file/${id}/share`, { method: "POST" }).catch((e) => { toast(e.message, 1); });
  if (!d) return;
  const link = location.origin + "/s/" + d.token;
  modal(`<h3>bagikan file</h3><div class="row"><input id="shareLink" readonly value="${link}"></div>
    <div class="foot"><button class="btn danger" data-unshare="${id}">hentikan</button>
    <button class="btn primary" id="copyLink">salin</button></div>`);
  $("#copyLink").onclick = async () => {
    try { await navigator.clipboard.writeText(link); } catch { $("#shareLink").select(); document.execCommand("copy"); }
    toast("link disalin");
  };
  const un = document.querySelector("[data-unshare]");
  if (un) un.onclick = async () => { await api(`/api/file/${id}/share`, { method: "DELETE" }); closeModal(); loadFiles(); toast("berbagi dihentikan"); };
  loadFiles();
}

function ask(title, value, cb) {
  modal(`<h3>${title}</h3><div class="row"><input id="askInput" value="${esc(value || "")}"></div>
    <div class="foot"><button class="btn" onclick="closeModal()">batal</button><button class="btn primary" id="askOk">simpan</button></div>`);
  const input = $("#askInput");
  input.focus(); input.select();
  const go = () => { const v = input.value.trim(); if (v) { closeModal(); cb(v); } };
  $("#askOk").onclick = go;
  input.onkeydown = (e) => { if (e.key === "Enter") go(); };
}

function renameFile(id) {
  const f = state.files.find((x) => x.id === id);
  ask("ganti nama", f.name, async (v) => {
    try { await api(`/api/file/${id}`, { method: "PATCH", body: JSON.stringify({ name: v }) }); loadFiles(); toast("nama diperbarui"); }
    catch (e) { toast(e.message, 1); }
  });
}

async function delFile(id) {
  if (!confirm("pindahkan file ke tong sampah?")) return;
  try { const d = await api(`/api/file/${id}`, { method: "DELETE" }); setMe(d.me); loadFiles(); refreshTrash(); toast("dipindah ke tong sampah"); }
  catch (e) { toast(e.message, 1); }
}

async function refreshTrash() {
  try {
    const d = await api("/api/trash");
    const n = (d.files || []).length;
    const b = $("#trashCount");
    if (!b) return;
    b.textContent = n;
    b.classList.toggle("hidden", n === 0);
  } catch {}
}

function setView(v) {
  state.view = v;
  state.folder = "";
  state.q = "";
  $("#q").value = "";
  document.querySelectorAll(".nav").forEach((n) => n.classList.remove("active"));
  if (v === "trash") $("#trashNav").classList.add("active");
  else document.querySelector('.nav[data-folder=""]').classList.add("active");
  closeSide();
  loadFiles();
}

async function delFolder(id) {
  if (!confirm("hapus folder? file di dalamnya dipindah ke Semua file.")) return;
  try { await api(`/api/folders/${id}`, { method: "DELETE" }); loadFiles(); toast("folder dihapus"); }
  catch (e) { toast(e.message, 1); }
}

/* ---------- upload ---------- */
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

/* jumlah koneksi paralel — bottleneck-nya per-koneksi, bukan bandwidth server */
const UP_PARALLEL = 6;
const DL_PARALLEL = 8;
const DL_MIN_SPLIT = 2 * 1024 * 1024;
const DL_BLOCK = 8 * 1024 * 1024;
const DL_MEM_MAX = 192 * 1024 * 1024;   // di atas ini jangan ditahan di RAM

/* ---------- download paralel ---------- */
const dlJobs = new Map();

function dlFallback(url, name) {
  const a = document.createElement("a");
  a.href = url; a.download = name || "";
  document.body.appendChild(a); a.click(); a.remove();
}

/* Service worker: jalur paralel yang tetap hemat memori (stream ke disk). */
let swReady = null;
function ensureSw() {
  if (!("serviceWorker" in navigator)) return Promise.resolve(false);
  if (swReady) return swReady;
  swReady = navigator.serviceWorker
    .register("/sw.js", { scope: "/" })
    .then(() => navigator.serviceWorker.ready)
    .then(() => true)
    .catch(() => false);
  return swReady;
}

function swDownload(url, name, size, par) {
  const q = new URLSearchParams({ u: url, s: String(size), n: name || "download", p: String(par) });
  return dlFallback("/sw-dl?" + q, name);
}

async function fastDownload(url, name, total, mime, key) {
  if (dlJobs.has(key)) return toast("file ini sedang diunduh");

  // file kecil atau tanpa info ukuran: biar browser yang urus
  if (!total || total < DL_MIN_SPLIT) return dlFallback(url, name);

  const canStream = typeof window.showSaveFilePicker === "function";

  // Jalur utama: service worker menjahit range paralel jadi satu stream ke disk.
  // Memory konstan, jadi aman untuk file besar, dan jalan di semua browser modern.
  const swOk = await ensureSw();
  if (swOk && navigator.serviceWorker.controller) {
    const par = Math.max(2, Math.min(DL_PARALLEL, Math.ceil(total / DL_MIN_SPLIT)));
    toast("mengunduh " + name + " · " + par + " koneksi");
    return swDownload(url, name, total, par);
  }

  // Tanpa SW dan file kegedean buat RAM: jangan dipaksa, serahkan ke browser
  if (!canStream && total > DL_MEM_MAX) return dlFallback(url, name);

  const ctrl = { cancelled: false, parallel: 0 };
  dlJobs.set(key, ctrl);

  const bar = $("#progress"), fill = $("#pFill"), pct = $("#pPct"), nm = $("#pName"), note = $("#pNote");
  bar.classList.remove("hidden");
  nm.textContent = "mengunduh " + name;
  fill.style.width = "0%"; pct.textContent = "0%";
  note.textContent = "0 / " + size(total);

  let handle = null;
  if (canStream) {
    try {
      handle = await window.showSaveFilePicker({ suggestedName: name });
    } catch (e) {
      bar.classList.add("hidden"); dlJobs.delete(key);
      return; // user batal
    }
  }

  const started = Date.now();
  let got = 0;
  const show = () => {
    const p = total ? Math.min(100, Math.round((got / total) * 100)) : 0;
    fill.style.width = p + "%"; pct.textContent = p + "%";
    const secs = Math.max(1, (Date.now() - started) / 1000);
    const mbps = got / 1048576 / secs;
    note.textContent = size(got) + " / " + size(total) + " · " +
      (mbps >= 1 ? mbps.toFixed(1) : mbps.toFixed(2)) + " MB/s" +
      (ctrl.parallel > 1 ? " · " + ctrl.parallel + " koneksi" : "");
  };

  const blockCount = Math.max(2, Math.min(DL_PARALLEL, Math.ceil(total / DL_MIN_SPLIT)));
  const blockSize = Math.ceil(total / blockCount);
  const bufs = new Array(blockCount);
  let failed = null;
  let writable = null;
  if (handle) writable = await handle.createWritable();

  const fetchBlock = async (i) => {
    const a = i * blockSize;
    const b = Math.min(a + blockSize, total) - 1;
    let pos = a;
    const parts = [];
    while (pos <= b) {
      if (ctrl.cancelled || failed) return;
      const end = Math.min(pos + DL_BLOCK - 1, b);
      let res;
      try {
        res = await fetch(url, { headers: { Range: `bytes=${pos}-${end}` } });
      } catch (e) { failed = e; return; }
      if (res.status !== 206 && res.status !== 200) {
        failed = new Error("unduh gagal: " + res.status);
        return;
      }
      let buf;
      try { buf = await res.arrayBuffer(); } catch (e) { failed = e; return; }
      if (ctrl.cancelled) return;
      // server tanpa Range: isi penuh balik sekaligus
      if (res.status === 200 && buf.byteLength === total && blockCount > 1) {
        if (writable) { await writable.write({ type: "write", position: 0, data: buf }); }
        else bufs[i] = buf;
        got = total; show();
        return;
      }
      if (writable) {
        await writable.write({ type: "write", position: pos, data: buf });
      } else {
        parts.push(new Uint8Array(buf));
      }
      pos += buf.byteLength;
      got += buf.byteLength;
      show();
      if (buf.byteLength === 0) break;
    }
    if (writable) return;
    const len = parts.reduce((n, p) => n + p.length, 0);
    const out = new Uint8Array(len);
    let off = 0;
    for (const p of parts) { out.set(p, off); off += p.length; }
    bufs[i] = out.buffer;
  };

  ctrl.parallel = blockCount;
  try {
    await Promise.all(Array.from({ length: blockCount }, (_, i) => fetchBlock(i)));
    if (failed) throw failed;
    if (ctrl.cancelled) throw new Error("dibatalkan");

    if (writable) {
      note.textContent = "menyelesaikan…";
      await writable.close();
      writable = null;
    } else {
      note.textContent = "menyusun file…";
      const blob = new Blob(bufs.filter(Boolean), { type: mime || "application/octet-stream" });
      const href = URL.createObjectURL(blob);
      dlFallback(href, name);
      setTimeout(() => URL.revokeObjectURL(href), 60000);
    }
    const secs = Math.max(1, (Date.now() - started) / 1000);
    toast(name + " · " + (total / 1048576 / secs).toFixed(1) + " MB/s");
  } catch (e) {
    if (writable) { try { await writable.abort(); } catch {} }
    if (!ctrl.cancelled) toast((e && e.message) || "unduh gagal", 1);
  } finally {
    bar.classList.add("hidden");
    dlJobs.delete(key);
  }
}

function parallelDownload(f) {
  return fastDownload(`/api/file/${f.id}/download`, f.name, f.size, f.mime, f.id);
}

function sendChunk(id, offset, blob, onProgress) {
  return new Promise((resolve) => {
    let settled = false;
    const xhr = new XMLHttpRequest();
    let last = Date.now();
    const done = (v) => {
      if (settled) return;
      settled = true;
      clearInterval(wd);
      resolve(v);
    };
    const wd = setInterval(() => {
      if (Date.now() - last > 20000) {
        try { xhr.abort(); } catch {}
        done({ ok: false, error: "koneksi macet" });
      }
    }, 5000);
    xhr.open("POST", "/api/upload/chunk?id=" + encodeURIComponent(id) + "&offset=" + offset);
    xhr.setRequestHeader("content-type", "application/octet-stream");
    xhr.timeout = 90000;
    xhr.upload.onprogress = (e) => {
      last = Date.now();
      if (e.lengthComputable && onProgress) onProgress(e.loaded);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return done({ ok: true });
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch {}
      done({ ok: false, status: xhr.status, error: d.error || "upload gagal" });
    };
    xhr.onerror = () => done({ ok: false, error: "koneksi putus" });
    xhr.onabort = () => done({ ok: false, error: "dibatalkan" });
    xhr.ontimeout = () => done({ ok: false, error: "timeout" });
    xhr.send(blob);
  });
}

async function uploadFiles(list) {
  if (!list || !list.length) return;
  const bar = $("#progress"), fill = $("#pFill"), pct = $("#pPct"), nm = $("#pName"), note = $("#pNote");
  const files = [...list];
  const total = files.reduce((a, f) => a + f.size, 0) || 1;
  let sent = 0, last = null;
  const started = Date.now();
  bar.classList.remove("hidden");
  fill.style.width = "0%"; pct.textContent = "0%";
  note.textContent = "0 / " + size(total);
  try {
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      nm.textContent = files.length > 1 ? (i + 1) + "/" + files.length + " · " + f.name : f.name;
      const init = await apiRetry("/api/upload/init", {
        method: "POST",
        body: JSON.stringify({ name: f.name, size: f.size, folder: state.folder, mime: f.type || "" }),
      });
      if (init.wait > 0) {
        for (let left = init.wait; left > 0; left--) {
          note.textContent = "jeda 1 menit antar upload · tunggu " + left + "s";
          await sleep(1000);
        }
      }
      const CH = init.chunk_size || 4 * 1024 * 1024;
      const totalChunks = Math.max(1, Math.ceil(f.size / CH));
      const inflight = new Map();
      let doneBytes = 0;
      const show = () => {
        let live = 0;
        inflight.forEach((v) => (live += v));
        const done = Math.min(total, sent + doneBytes + live);
        const p = Math.min(100, Math.round((done / total) * 100));
        fill.style.width = p + "%"; pct.textContent = p + "%";
        const secs = Math.max(1, (Date.now() - started) / 1000);
        const mbps = done / 1048576 / secs;
        note.textContent = size(done) + " / " + size(total) + " · " + (mbps >= 1 ? mbps.toFixed(1) : mbps.toFixed(2)) + " MB/s";
      };
      const sendOne = async (idx) => {
        const off = idx * CH;
        const part = f.slice(off, Math.min(off + CH, f.size));
        let ok = false;
        for (let t = 0; t < 6 && !ok; t++) {
          const res = await sendChunk(init.id, off, part, (loaded) => { inflight.set(idx, loaded); show(); });
          if (res.ok) ok = true;
          else if (res.status === 404 || res.status === 413) throw new Error(res.error || "upload gagal");
          else await sleep(700 * (t + 1));
        }
        if (!ok) throw new Error("koneksi putus");
        inflight.delete(idx);
        doneBytes += part.size;
        show();
      };
      const runWave = async (indices) => {
        inflight.clear();
        let cursor = 0, failed = null;
        const worker = async () => {
          while (cursor < indices.length && !failed) {
            const idx = indices[cursor++];
            try { await sendOne(idx); } catch (e) { failed = e; }
          }
        };
        const lanes = Math.max(1, Math.min(UP_PARALLEL, indices.length));
        await Promise.all(Array.from({ length: lanes }, () => worker()));
        if (failed) throw failed;
      };
      let indices = Array.from({ length: totalChunks }, (_, i) => i);
      for (let attempt = 0; ; attempt++) {
        try {
          await runWave(indices);
          break;
        } catch (e) {
          if (attempt >= 2) throw e;
          note.textContent = "menyambung ulang…";
          await sleep(2500);
          const st = await apiRetry("/api/upload/status?id=" + encodeURIComponent(init.id));
          const ranges = st.ranges || [];
          indices = [];
          for (let i = 0; i < totalChunks; i++) {
            const a = i * CH, b = Math.min(a + CH, f.size);
            if (!ranges.some(([x, y]) => x <= a && b <= y)) indices.push(i);
          }
          doneBytes = st.received || 0;
          show();
          if (!indices.length) break;
        }
      }
      sent += f.size;
      doneBytes = 0;
      inflight.clear();
      note.textContent = "menyimpan…";
      last = await apiRetry("/api/upload/finish", { method: "POST", body: JSON.stringify({ id: init.id }) });
      while (last && last.retry) {
        for (let left = last.wait; left > 0; left--) {
          note.textContent = "menunggu antrean · " + left + "s";
          await sleep(1000);
        }
        last = await apiRetry("/api/upload/finish", { method: "POST", body: JSON.stringify({ id: init.id }) });
      }
      if (last.me) setMe(last.me);
    }
    bar.classList.add("hidden");
    loadFiles();
    startPendingPoll();
    toast(files.length + " file dikirim, diproses di server");
    state.delay = (last && last.delay) || state.delay;
    startCooldown(state.delay);
  } catch (e) {
    bar.classList.add("hidden");
    toast(e.message || "upload gagal", 1);
    loadFiles();
  }
  $("#pick").value = "";
}

let pendingTimer = null;
function startPendingPoll() {
  clearInterval(pendingTimer);
  pendingTimer = setInterval(async () => {
    try {
      await loadFiles();
      if (!state.files.some((f) => f.status === "pending")) clearInterval(pendingTimer);
    } catch {}
  }, 4000);
}

let cdTimer = null;
function startCooldown(sec) {
  clearInterval(cdTimer);
  const lbl = document.querySelector(".upload-label");
  const txt = $("#upTxt");
  if (!lbl || !txt) return;
  const until = Date.now() + sec * 1000;
  try { if (state.me) localStorage.setItem("cd:" + state.me.username, String(until)); } catch {}
  lbl.classList.add("busy");
  const tick = () => {
    const left = Math.max(0, Math.ceil((until - Date.now()) / 1000));
    if (left <= 0) {
      clearInterval(cdTimer);
      lbl.classList.remove("busy");
      txt.textContent = "unggah";
      try { if (state.me) localStorage.removeItem("cd:" + state.me.username); } catch {}
      return;
    }
    txt.textContent = "tunggu " + left + "s";
  };
  tick();
  cdTimer = setInterval(tick, 250);
}

function resumeCooldown() {
  if (!state.me) return;
  try {
    const until = Number(localStorage.getItem("cd:" + state.me.username) || 0);
    if (until > Date.now()) startCooldown(Math.ceil((until - Date.now()) / 1000));
  } catch {}
}

/* ---------- api keys ---------- */
function keyWhen(t) {
  if (!t) return "belum pernah dipakai";
  const d = new Date(t * 1000);
  return d.toLocaleDateString("id-ID", { day: "2-digit", month: "short", year: "2-digit" }) +
    " " + d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
}

function keyRow(k) {
  const scope = k.scope === "read" ? '<span class="badge static">read</span>' : '<span class="badge static full">full</span>';
  return `<div class="keyrow" data-key="${k.id}">
      <div class="keymain">
        <div class="keyname">${esc(k.name)} ${scope}</div>
        <div class="keysub">${esc(k.prefix)}••••••••••  ·  ${k.uses}x  ·  ${keyWhen(k.last_used)}</div>
      </div>
      <div class="keyacts">
        <button class="ghost" data-krename="${k.id}" title="ganti nama">nama</button>
        <button class="ghost" data-kscope="${k.id}" title="ubah scope">scope</button>
        <button class="ghost danger" data-kdel="${k.id}" title="cabut">cabut</button>
      </div>
    </div>`;
}

async function keysModal() {
  let d;
  try { d = await api("/api/keys"); } catch (e) { return toast(e.message, 1); }
  const list = d.keys.length ? d.keys.map(keyRow).join("") : '<p class="muted" style="margin:6px 0 0">belum ada api key.</p>';
  modal(`<h3>api key</h3>
    <p class="muted" style="margin:0 0 14px;font-size:12.5px">pakai di bot telegram, ai, atau script. kirim lewat header <code>Authorization: Bearer nbla…</code> atau <code>X-API-Key</code>.</p>
    <div class="keylist">${list}</div>
    <div class="keynew">
      <input id="keyName" placeholder="nama key (mis. bot-telegram)" maxlength="60">
      <select id="keyScope">
        <option value="full">full — baca + tulis</option>
        <option value="read">read — baca saja</option>
      </select>
      <button class="btn primary" id="keyCreate">buat key</button>
    </div>
    <div class="foot"><span class="muted" style="margin-right:auto;font-size:11.5px">${d.keys.length}/${d.max} key</span>
    <button class="btn" onclick="closeModal()">tutup</button></div>`);

  $("#keyCreate").onclick = async () => {
    const name = $("#keyName").value.trim();
    const scope = $("#keyScope").value;
    try {
      const r = await api("/api/keys", { method: "POST", body: JSON.stringify({ name, scope }) });
      showNewKey(r.key);
    } catch (e) { toast(e.message, 1); }
  };

  document.querySelectorAll("[data-kdel]").forEach((b) => {
    b.onclick = async () => {
      try { await api("/api/keys/" + b.dataset.kdel, { method: "DELETE" }); toast("key dicabut"); keysModal(); }
      catch (e) { toast(e.message, 1); }
    };
  });
  document.querySelectorAll("[data-krename]").forEach((b) => {
    b.onclick = () => {
      const cur = (d.keys.find((x) => x.id === b.dataset.krename) || {}).name || "";
      ask("ganti nama key", cur, async (v) => {
        try { await api("/api/keys/" + b.dataset.krename, { method: "PATCH", body: JSON.stringify({ name: v }) }); keysModal(); }
        catch (e) { toast(e.message, 1); }
      });
    };
  });
  document.querySelectorAll("[data-kscope]").forEach((b) => {
    b.onclick = async () => {
      const cur = (d.keys.find((x) => x.id === b.dataset.kscope) || {}).scope;
      const next = cur === "read" ? "full" : "read";
      try { await api("/api/keys/" + b.dataset.kscope, { method: "PATCH", body: JSON.stringify({ scope: next }) }); keysModal(); toast("scope jadi " + next); }
      catch (e) { toast(e.message, 1); }
    };
  });
}

function showNewKey(raw) {
  modal(`<h3>api key dibuat</h3>
    <p class="muted" style="margin:0 0 12px;font-size:12.5px">salin sekarang — key ini tidak ditampilkan lagi.</p>
    <div class="row"><input id="newKeyVal" readonly value="${esc(raw)}"></div>
    <div class="foot"><button class="btn primary" id="copyKey">salin</button>
    <button class="btn" id="doneKey">selesai</button></div>`);
  const inp = $("#newKeyVal");
  inp.focus(); inp.select();
  $("#copyKey").onclick = async () => {
    try { await navigator.clipboard.writeText(raw); } catch { inp.select(); document.execCommand("copy"); }
    toast("key disalin");
  };
  $("#doneKey").onclick = () => keysModal();
}

/* ---------- auth ---------- */
async function boot() {
  if (location.pathname.startsWith("/s/")) return bootShare();
  try {
    const me = await api("/api/me");
    setMe(me);
    $("#auth").classList.add("hidden");
    $("#app").classList.remove("hidden");
    await loadFiles();
    resumeCooldown();
    refreshTrash();
  } catch {
    $("#auth").classList.remove("hidden");
    resetVerify();
  }
}

async function bootShare() {
  const token = location.pathname.split("/")[2] || "";
  $("#share").classList.remove("hidden");
  try {
    const d = await api("/api/share/" + token);
    const [cls, svg] = iconFor(d.name, d.mime);
    const isImg = (d.mime || "").startsWith("image/");
    $("#shareBody").innerHTML = `
      <div style="height:16px"></div>
      <div class="thumb t-icon-${cls}" style="aspect-ratio:16/9;border-radius:14px;border:1px solid var(--line);display:grid;place-items:center;overflow:hidden">
        ${isImg ? `<img src="/api/share/${token}/download?inline=1" style="width:100%;height:100%;object-fit:cover">` : svg}
      </div>
      <div style="margin:14px 0 4px;color:var(--txt);font-weight:600;word-break:break-word">${esc(d.name)}</div>
      <div class="muted" style="font-size:12.5px">${size(d.size)} · ${when(d.created_at)}</div>
      <button class="btn primary" id="shareDl" style="width:100%;margin-top:16px">unduh</button>`;
    const sb = $("#shareDl");
    if (sb) sb.onclick = () => fastDownload(`/api/share/${token}/download`, d.name, d.size, d.mime, "share:" + token);
  } catch { $("#shareBody").textContent = "file tidak ditemukan atau link dicabut."; }
}

$("#authForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#authBtn");
  btn.disabled = true;
  try {
    if (!powToken) throw new Error("tunggu verifikasi selesai");
    const body = JSON.stringify({
      username: $("#username").value.trim(),
      password: $("#password").value,
      nickname: $("#nickname") ? $("#nickname").value : "",
      pow: powToken,
    });
    const me = await api("/api/" + state.mode, { method: "POST", body });
    setMe(me);
    $("#auth").classList.add("hidden");
    $("#app").classList.remove("hidden");
    await loadFiles();
    resumeCooldown();
  } catch (err) {
    toast(err.message, 1);
    resetVerify();
  }
});

$("#swapMode").onclick = () => {
  state.mode = state.mode === "login" ? "register" : "login";
  $("#authSub").textContent = state.mode === "login" ? "masuk untuk lanjut" : "bikin akun baru";
  $("#authBtn").textContent = state.mode === "login" ? "masuk" : "daftar";
  $("#swapMode").textContent = state.mode === "login" ? "daftar" : "masuk";
  document.querySelector(".swap").firstChild.textContent = state.mode === "login" ? "belum punya akun? " : "sudah punya akun? ";
  resetVerify();
};

$("#logout").onclick = async () => { await api("/api/logout", { method: "POST" }); location.reload(); };
$("#keysBtn").onclick = keysModal;
$("#verifyCheck").onchange = (e) => { if (e.target.checked) solveChallenge(); else resetVerify(); };
$("#pick").onchange = (e) => uploadFiles(e.target.files);
$("#newFolder").onclick = () => ask("folder baru", "", async (v) => {
  try { await api("/api/folders", { method: "POST", body: JSON.stringify({ name: v }) }); loadFiles(); toast("folder dibuat"); }
  catch (e) { toast(e.message, 1); }
});
$("#trashNav").onclick = () => setView(state.view === "trash" ? "" : "trash");
$("#menu").onclick = () => $(".side").classList.toggle("open");

let searchT;
$("#q").oninput = (e) => { clearTimeout(searchT); searchT = setTimeout(() => { state.q = e.target.value.trim(); loadFiles(); }, 250); };

const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.background = "rgba(124,92,255,.06)"; }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => { drop.style.background = ""; }));
drop.addEventListener("drop", (e) => { e.preventDefault(); uploadFiles(e.dataTransfer.files); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

boot();
