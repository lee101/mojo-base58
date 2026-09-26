"""ctypes bridge to the compiled base58 kernels.

The shared library owns no memory. Every buffer crosses the C ABI as a 64-bit
address, so the argtypes below must stay `c_int64` for addresses; `c_int`
truncates them and segfaults. Scratch buffers are allocated here, on the Python
side, which keeps the exported Mojo symbols free of allocation and of `raises`.
"""

from __future__ import annotations

import ctypes
import math
import pathlib

import numpy as np

_HERE = pathlib.Path(__file__).resolve()
_ROOT = _HERE.parents[2]
_LIB_PATH = _ROOT / "dist" / "libmojo-base58.so"


def _load():
    if not _LIB_PATH.exists():
        raise RuntimeError(
            f"{_LIB_PATH} not found; run `bash build/build.sh` first"
        )
    lib = ctypes.CDLL(str(_LIB_PATH))
    lib.b58_bytes_to_words.restype = ctypes.c_int64
    lib.b58_bytes_to_words.argtypes = [
        ctypes.c_int64, ctypes.c_int64, ctypes.c_int64, ctypes.c_int64,
        ctypes.c_int64, ctypes.c_int64, ctypes.c_int64, ctypes.c_int64,
    ]
    lib.b58_words_to_bytes.restype = ctypes.c_int64
    lib.b58_words_to_bytes.argtypes = [
        ctypes.c_int64, ctypes.c_int64, ctypes.c_int64, ctypes.c_int64,
        ctypes.c_int64, ctypes.c_int64, ctypes.c_int64, ctypes.c_int64,
    ]
    lib.b58_chunk_width.restype = ctypes.c_int64
    lib.b58_chunk_width.argtypes = [ctypes.c_int64]
    return lib


lib = _load()


def _bits_per_digit(base: int) -> int:
    """Upper bound on the bits one radix digit can carry."""
    return int(base).bit_length()


def chunk_width(base: int) -> int:
    """Radix digits the Mojo kernel moves per limb pass."""
    return int(lib.b58_chunk_width(ctypes.c_int64(base)))


def encode_words(data: bytes, alphabet: bytes) -> bytes:
    """Convert a big-endian byte string to radix-`len(alphabet)` words.

    Leading zero bytes are dropped, matching what `b58encode_int(..., False)`
    computes over the integer value of `data`. An all-zero input yields no
    words, and the words come back most significant first.
    """
    base = len(alphabet)
    if base < 2:
        raise ValueError("alphabet must have at least 2 symbols")
    n = len(data)
    if n == 0:
        return b""
    src = np.frombuffer(data, dtype=np.uint8)
    alpha = np.frombuffer(alphabet, dtype=np.uint8)
    # A value below 2**(8n) needs at most floor(8n / log2(base)) + 1 digits.
    # `base.bit_length()` overstates log2(base), so it cannot bound the count.
    # The kernel emits one whole chunk of digits per division pass and trims only
    # the last, so the word buffer is sized in whole chunks.
    width = chunk_width(base)
    max_digits = int(n * 8 / math.log2(base)) + 2
    max_words = ((max_digits + width - 1) // width) * width
    max_limbs = (n * 8 + 31) // 32 + 1
    limbs = np.zeros(max_limbs, dtype=np.uint32)
    wbuf = np.zeros(max_words, dtype=np.uint8)
    start = lib.b58_bytes_to_words(
        src.ctypes.data, n, alpha.ctypes.data, base,
        limbs.ctypes.data, max_limbs, wbuf.ctypes.data, max_words,
    )
    if start < 0:
        raise RuntimeError("base58 encode scratch too small")
    return wbuf[start:].tobytes()


def decode_map(alphabet: bytes, autofix: bool = False) -> np.ndarray:
    """Build the 256-entry symbol table the decode kernel indexes, -1 for unknown.

    The `autofix` groups match `base58._get_base58_decode_map`: a confusable
    group only folds in when the alphabet contains exactly one of its members,
    because two members means the ambiguity is unresolvable.
    """
    invmap = {c: i for i, c in enumerate(alphabet)}
    if autofix:
        for group in (b"0Oo", b"Il1"):
            pivots = [c for c in group if c in invmap]
            if len(pivots) == 1:
                for alternative in group:
                    invmap[alternative] = invmap[pivots[0]]
    dmap = np.full(256, -1, dtype=np.int32)
    for c, i in invmap.items():
        dmap[c] = i
    return dmap


def decode_words(
    words: bytes, alphabet: bytes, *, autofix: bool = False
) -> bytes:
    """Convert a radix-`len(alphabet)` word string to a big-endian byte string.

    Leading zero digits contribute nothing to the value and are absorbed by the
    big-integer conversion, so a run of `alphabet[0]` characters yields an empty
    result and the caller re-adds the zero bytes. Raises ValueError naming the
    first character that is not in the alphabet.
    """
    base = len(alphabet)
    if base < 2:
        raise ValueError("alphabet must have at least 2 symbols")
    n = len(words)
    if n == 0:
        return b""
    src = np.frombuffer(words, dtype=np.uint8)
    dmap = decode_map(alphabet, autofix)
    bpd = _bits_per_digit(base)
    max_limbs = (n * bpd + 31) // 32 + 2
    max_out = (n * bpd + 7) // 8 + 1
    limbs = np.zeros(max_limbs, dtype=np.uint32)
    obuf = np.zeros(max_out, dtype=np.uint8)
    written = lib.b58_words_to_bytes(
        src.ctypes.data, n, dmap.ctypes.data, base,
        limbs.ctypes.data, max_limbs, obuf.ctypes.data, max_out,
    )
    if written < 0:
        if written == -1:
            raise RuntimeError("base58 decode scratch too small")
        bad = -written - 2
        raise ValueError("Invalid character {!r}".format(chr(words[bad])))
    return obuf.tobytes()[:written]
