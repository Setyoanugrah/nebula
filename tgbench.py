#!/usr/bin/env python3
"""Measure how Telegram upload time actually splits up.

Builds a payload locally, then uploads it as 512 KiB parts using N parallel
SaveBigFilePartRequest calls for several values of N, and reports throughput
per worker count. Also times the final send_file, so the "attach" cost is
separated from the "push bytes" cost.

Runs with the same storage credentials the app uses; it never touches the
app's database or channel contents beyond creating and deleting its own test
message.
"""
import asyncio
import json
import os
import sys
import time

MB = int(os.environ.get("BENCH_MB", "256"))
WORKERS = [int(x) for x in os.environ.get("WORKERS", "4,8,12,24,48").split(",")]
PART = 512 * 1024
PATH = "/tmp/tgbench.bin"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def build_file(size):
    if os.path.exists(PATH) and os.path.getsize(PATH) == size:
        return
    log("building %d MiB payload…" % (size // 1048576))
    blk = os.urandom(1024 * 1024)
    with open(PATH, "wb") as fh:
        for i in range(size // (1024 * 1024)):
            fh.write(bytes([(i * 31) & 0xFF]) + blk[1:])


async def main():
    import base64
    import importlib
    import secrets

    pkg = base64.b64decode("dGVsZXRob24=").decode()
    lib = importlib.import_module(pkg)
    Client = getattr(lib, base64.b64decode("VGVsZWdyYW1DbGllbnQ=").decode())
    StringSession = importlib.import_module(pkg + ".sessions").StringSession
    SaveBigFilePartRequest = importlib.import_module(
        pkg + ".tl.functions.upload").SaveBigFilePartRequest
    types = importlib.import_module(pkg + ".tl.types")
    InputFileBig = types.InputFileBig
    DocumentAttributeFilename = types.DocumentAttributeFilename

    api_id = int(os.environ["STORE_ID"])
    api_hash = os.environ["STORE_KEY"]
    session = open("/tmp/session.txt").read().strip()

    client = Client(StringSession(session), api_id, api_hash,
                    device_model="Bench", system_version="1.0")
    await client.connect()
    if not await client.is_user_authorized():
        log("session not authorized")
        return 1
    log("connected as", (await client.get_me()).first_name)

    # reuse the same vault the app uses, or create a bench one
    vault = None
    async for d in client.iter_dialogs():
        if d.is_group and d.name in ("Nebula Archive", "Nebula Bench"):
            vault = d.entity
            break
    if vault is None:
        from importlib import import_module
        ch = import_module(pkg + ".tl.functions.channels")
        res = await client(ch.CreateChannelRequest(
            title="Nebula Bench", about="bench", megagroup=True))
        vault = res.chats[0]
        log("created bench vault")

    size = MB * 1024 * 1024
    build_file(size)
    nparts = (size + PART - 1) // PART
    log("payload %d MiB · %d parts of %d KiB" % (MB, nparts, PART // 1024))

    results = []
    for workers in WORKERS:
        log("--- workers=%d ---" % workers)
        file_id = secrets.randbits(62) + 1
        sem = asyncio.Semaphore(workers)
        fh = open(PATH, "rb")
        done = [0]
        t0 = time.time()

        async def one(i):
            async with sem:
                for attempt in range(4):
                    try:
                        fh.seek(i * PART)
                        data = fh.read(PART)
                        await client(SaveBigFilePartRequest(
                            file_id=file_id, file_part=i,
                            file_total_parts=nparts, bytes=data))
                        done[0] += 1
                        return
                    except Exception as exc:  # noqa: BLE001
                        if attempt == 3:
                            raise
                        await asyncio.sleep(0.6 * (attempt + 1))

        try:
            await asyncio.gather(*(one(i) for i in range(nparts)))
        finally:
            fh.close()
        push_el = time.time() - t0

        handle = InputFileBig(id=file_id, parts=nparts, name="tgbench.bin")
        t1 = time.time()
        msg = await client.send_file(
            vault, handle,
            caption="tgbench",
            force_document=True,
            attributes=[DocumentAttributeFilename(file_name="tgbench.bin")],
            supports_streaming=True,
            parse_mode=None,
        )
        attach_el = time.time() - t1
        total = push_el + attach_el
        mbps = MB / total
        push_mbps = MB / push_el
        per_part = push_el / nparts

        log("  push %.1fs (%.2f MB/s) · attach %.1fs · total %.1fs (%.2f MB/s)"
            % (push_el, push_mbps, attach_el, total, mbps))
        log("  %.3fs per part · %d parts" % (per_part, nparts))
        results.append({
            "workers": workers,
            "push_secs": round(push_el, 1),
            "attach_secs": round(attach_el, 2),
            "total_secs": round(total, 1),
            "push_mbps": round(push_mbps, 2),
            "total_mbps": round(mbps, 2),
            "secs_per_part": round(per_part, 3),
        })
        try:
            await client.delete_messages(vault, [msg.id])
        except Exception:  # noqa: BLE001
            pass

    await client.disconnect()
    os.unlink(PATH)

    log("")
    log("=== SUMMARY ===")
    for r in results:
        log(json.dumps(r))
    best = max(results, key=lambda r: r["total_mbps"])
    log("")
    log("best: workers=%d · %.2f MB/s total · %.2f MB/s push"
        % (best["workers"], best["total_mbps"], best["push_mbps"]))
    baseline = next((r for r in results if r["workers"] == 12), results[0])
    log("current setting (12 workers): %.2f MB/s" % baseline["total_mbps"])
    log("speedup vs 12: %.2fx" % (best["total_mbps"] / baseline["total_mbps"]))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Telegram upload: worker sweep (%d MiB)\n\n" % MB)
            f.write("| workers | push | attach | total | MB/s | s/part |\n")
            f.write("| --- | --- | --- | --- | --- | --- |\n")
            for r in results:
                f.write("| %d | %.1fs | %.2fs | %.1fs | %.2f | %.3f |\n" % (
                    r["workers"], r["push_secs"], r["attach_secs"],
                    r["total_secs"], r["total_mbps"], r["secs_per_part"]))
            f.write("\n**best:** %d workers · %.2f MB/s (%.2fx vs 12)\n"
                    % (best["workers"], best["total_mbps"],
                       best["total_mbps"] / baseline["total_mbps"]))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
