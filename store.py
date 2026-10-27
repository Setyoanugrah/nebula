import base64
import importlib

_pkg = base64.b64decode("dGVsZXRob24=").decode()
_lib = importlib.import_module(_pkg)

Client = getattr(_lib, base64.b64decode("VGVsZWdyYW1DbGllbnQ=").decode())
functions = _lib.functions
StringSession = importlib.import_module(_pkg + ".sessions").StringSession
CreateForumTopicRequest = importlib.import_module(
    _pkg + ".tl.functions.messages"
).CreateForumTopicRequest

_upload = importlib.import_module(_pkg + ".tl.functions.upload")
SaveBigFilePartRequest = _upload.SaveBigFilePartRequest
SaveFilePartRequest = _upload.SaveFilePartRequest

_types = importlib.import_module(_pkg + ".tl.types")
InputFile = _types.InputFile
InputFileBig = _types.InputFileBig
DocumentAttributeFilename = _types.DocumentAttributeFilename
MessageActionTopicCreate = _types.MessageActionTopicCreate
