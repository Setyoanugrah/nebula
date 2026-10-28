import base64, json, os, sys
from nacl import encoding, public
pk = public.PublicKey(os.environ["PK"].encode(), encoding.Base64Encoder())
box = public.SealedBox(pk)
sealed = box.encrypt(os.environ["SECRET"].encode())
print("SEALED:" + base64.b64encode(sealed).decode())
