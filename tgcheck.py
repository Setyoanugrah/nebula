import asyncio, os, traceback
from telethon import TelegramClient
from telethon.sessions import StringSession

async def main():
    s = os.environ["SESSION"].strip()
    print("session len", len(s), "prefix", s[:4], flush=True)
    c = TelegramClient(StringSession(s), int(os.environ["API_ID"]), os.environ["API_KEY"],
                       device_model="Nebula", system_version="1.0")
    try:
        await c.connect()
        print("connected:", c.is_connected(), flush=True)
        auth = await c.is_user_authorized()
        print("authorized:", auth, flush=True)
        if auth:
            me = await c.get_me()
            print("me:", me.id, me.username, flush=True)
            print("dc:", c.session.dc_id, flush=True)
            dialogs = []
            async for d in c.iter_dialogs(limit=20):
                dialogs.append((d.name, d.is_group))
            print("dialogs:", dialogs, flush=True)
    except Exception as e:
        print("ERROR:", type(e).__name__, e, flush=True)
        traceback.print_exc()
    finally:
        await c.disconnect()

asyncio.run(main())
