"""Windows DPAPI (Data Protection API) を使った文字列の暗号化・復号。

Discord Bot トークンのように、settings.json に平文で保存すると漏洩リスクが
ある値を、Windows の CryptProtectData/CryptUnprotectData (crypt32.dll) で
「現在のWindowsユーザー」スコープに暗号化してから保存するために使う。

新規のサードパーティ依存を追加しない方針のため、標準ライブラリの ctypes
のみで実装する (pywin32 等は使わない)。このアプリはWindows専用のため、
crypt32/kernel32 が存在しない環境 (非Windows) は対象外。

重要な制約:
    - 暗号化はカレントWindowsユーザーのDPAPIマスターキーに紐づく。
      同じPCの別Windowsユーザー、または別PC(OS再インストール後を含む)では
      復号できない (OSError になる)。その場合はトークンの再入力を促す
      以外に復旧手段はない。
    - CRYPTPROTECT_UI_FORBIDDEN を指定し、失敗時にOSのUIダイアログが
      表示されないようにする (バックグラウンド処理から呼ばれるため)。
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes

# CryptProtectData/CryptUnprotectData の dwFlags。
# UIを一切出さない (ダイアログ表示に失敗する/フリーズする環境を避ける)。
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


_PDATA_BLOB = ctypes.POINTER(_DATA_BLOB)

_crypt32.CryptProtectData.argtypes = [
    _PDATA_BLOB,        # pDataIn
    wintypes.LPCWSTR,   # szDataDescr
    _PDATA_BLOB,        # pOptionalEntropy
    ctypes.c_void_p,    # pvReserved
    ctypes.c_void_p,    # pPromptStruct
    wintypes.DWORD,     # dwFlags
    _PDATA_BLOB,        # pDataOut
]
_crypt32.CryptProtectData.restype = wintypes.BOOL

_crypt32.CryptUnprotectData.argtypes = [
    _PDATA_BLOB,        # pDataIn
    ctypes.c_void_p,    # ppszDataDescr (使わない)
    _PDATA_BLOB,        # pOptionalEntropy
    ctypes.c_void_p,    # pvReserved
    ctypes.c_void_p,    # pPromptStruct
    wintypes.DWORD,     # dwFlags
    _PDATA_BLOB,        # pDataOut
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL


def _bytes_to_blob(data: bytes) -> tuple[_DATA_BLOB, object]:
    """bytes から DATA_BLOB を組み立てる。

    戻り値の2つ目 (buf) は、DATA_BLOB.pbData が指すメモリの実体。
    呼び出し側はWinAPI呼び出しが終わるまで参照を保持し続ける必要がある
    (GC で先に解放されるとダングリングポインタになるため)。
    """
    buf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    return blob, buf


def _take_and_free(blob: _DATA_BLOB) -> bytes:
    """DATA_BLOB からbytesを取り出し、Windows側が確保したバッファを解放する。

    CryptProtectData/CryptUnprotectData が pDataOut に確保するメモリは
    LocalAlloc 由来のため、呼び出し側が LocalFree で解放する責務を持つ
    (MSDN 記載の仕様)。取り出した後は必ず解放し、リークさせない。
    """
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        if blob.pbData:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))


def protect(data: bytes) -> bytes:
    """CryptProtectData でカレントユーザースコープに暗号化する。

    Args:
        data: 暗号化する生バイト列。

    Returns:
        暗号化されたバイト列 (DPAPI blob)。

    Raises:
        OSError: 暗号化に失敗した場合。
    """
    if not isinstance(data, bytes):
        raise TypeError("data はbytesである必要があります")

    blob_in, _keep_alive = _bytes_to_blob(data)
    blob_out = _DATA_BLOB()

    ok = _crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(f"CryptProtectData に失敗しました (GetLastError={err})")

    return _take_and_free(blob_out)


def unprotect(blob: bytes) -> bytes:
    """CryptUnprotectData で復号する。

    別Windowsユーザー/別PCで暗号化されたblob、または破損したblobを渡すと
    復号できずに失敗する (別ユーザー/別PCでは仕様上復号不可。その場合は
    再入力を促す)。

    Args:
        blob: protect() で暗号化されたバイト列。

    Returns:
        復号された元のバイト列。

    Raises:
        OSError: 復号に失敗した場合 (別ユーザー/別PC/破損データを含む)。
    """
    if not isinstance(blob, bytes):
        raise TypeError("blob はbytesである必要があります")

    blob_in, _keep_alive = _bytes_to_blob(blob)
    blob_out = _DATA_BLOB()

    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(f"CryptUnprotectData に失敗しました (GetLastError={err})")

    return _take_and_free(blob_out)


def encrypt_to_b64(plain: str) -> str:
    """文字列をDPAPIで暗号化し、settings.json格納用にBase64文字列へ変換する。"""
    encrypted = protect(plain.encode("utf-8"))
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_from_b64(b64: str) -> str:
    """encrypt_to_b64 の逆変換。

    不正なBase64文字列や、復号自体に失敗した場合は例外 (ValueError/OSError)
    を送出する。呼び出し側 (SettingsStore) で捕捉して既定値へフォールバック
    する想定。
    """
    raw = base64.b64decode(b64.encode("ascii"), validate=True)
    decrypted = unprotect(raw)
    return decrypted.decode("utf-8")
