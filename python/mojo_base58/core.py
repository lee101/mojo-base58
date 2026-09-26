"""Base58 and Base58Check with the digit conversion running in Mojo.

The public names and semantics mirror `base58` 2.1.1, so this package drops into
existing code. What differs is where the work happens: the arbitrary-precision
radix conversion is a compiled limb loop rather than a chain of Python
`divmod` calls on a CPython bigint, which is where essentially all of the time
in a base58 codec goes.

Double-SHA256 for the `*_check` variants stays on `hashlib`. That is a C
primitive in the standard library already; there is nothing to gain by
reimplementing a cryptographic hash here, and a slower one would be a defect.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Union

from . import _lib

__all__ = [
    "BITCOIN_ALPHABET",
    "RIPPLE_ALPHABET",
    "XRP_ALPHABET",
    "alphabet",
    "b58decode",
    "b58decode_check",
    "b58decode_int",
    "b58encode",
    "b58encode_check",
    "b58encode_int",
    "scrub_input",
]

# 58 character alphabet used
BITCOIN_ALPHABET = (
    b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
)
RIPPLE_ALPHABET = (
    b"rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz"
)
XRP_ALPHABET = RIPPLE_ALPHABET

# Retro compatibility
alphabet = BITCOIN_ALPHABET


def scrub_input(v: Union[str, bytes]) -> bytes:
    if isinstance(v, str):
        v = v.encode("ascii")
    return v


def b58encode(v: Union[str, bytes], alphabet: bytes = BITCOIN_ALPHABET) -> bytes:
    """Encode a string using Base58.

    Leading 0x00 bytes are not part of the integer value, so each one becomes a
    leading `alphabet[0]` character instead, exactly as in `base58.b58encode`.
    """
    v = scrub_input(v)

    origlen = len(v)
    v = v.lstrip(b"\0")
    newlen = len(v)

    return alphabet[0:1] * (origlen - newlen) + _lib.encode_words(v, alphabet)


def b58decode(
    v: Union[str, bytes], alphabet: bytes = BITCOIN_ALPHABET, *, autofix: bool = False
) -> bytes:
    """Decode a Base58 encoded string.

    Each leading `alphabet[0]` character becomes a leading 0x00 byte. Unknown
    characters raise ValueError; with `autofix` the `0Oo` and `Il1` confusable
    groups are folded in when the alphabet contains exactly one member of them,
    matching `base58.b58decode`.
    """
    v = v.rstrip()
    v = scrub_input(v)

    origlen = len(v)
    v = v.lstrip(alphabet[0:1])
    newlen = len(v)

    return b"\0" * (origlen - newlen) + _lib.decode_words(v, alphabet, autofix=autofix)


def b58encode_int(
    i: int, default_one: bool = True, alphabet: bytes = BITCOIN_ALPHABET
) -> bytes:
    """Encode an integer using Base58.

    The integer is widened to its minimal big-endian byte form and handed to the
    same compiled radix conversion; that is the same value the upstream
    `divmod` chain accumulates, reached a different way.

    Unlike `base58.b58encode_int`, a negative input raises ValueError instead of
    looping forever, because `divmod` on a negative dividend never reaches zero.
    """
    if not isinstance(i, int):
        raise TypeError("a integer is required")
    if i < 0:
        raise ValueError("Negative numbers are not supported")
    if not i and default_one:
        return alphabet[0:1]
    if not i:
        return b""
    nbytes = (i.bit_length() + 7) // 8
    return _lib.encode_words(i.to_bytes(nbytes, "big"), alphabet)


def b58decode_int(
    v: Union[str, bytes], alphabet: bytes = BITCOIN_ALPHABET, *, autofix: bool = False
) -> int:
    """Decode a Base58 encoded string as an integer."""
    if b" " not in alphabet:
        v = v.rstrip()
    v = scrub_input(v)
    return int.from_bytes(_lib.decode_words(v, alphabet, autofix=autofix), "big")


def b58encode_check(v: Union[str, bytes], alphabet: bytes = BITCOIN_ALPHABET) -> bytes:
    """Encode a string using Base58 with a 4 character checksum."""
    v = scrub_input(v)
    digest = sha256(sha256(v).digest()).digest()
    return b58encode(v + digest[:4], alphabet=alphabet)


def b58decode_check(
    v: Union[str, bytes], alphabet: bytes = BITCOIN_ALPHABET, *, autofix: bool = False
) -> bytes:
    """Decode and verify the checksum of a Base58 encoded string."""
    result = b58decode(v, alphabet=alphabet, autofix=autofix)
    result, check = result[:-4], result[-4:]
    digest = sha256(sha256(result).digest()).digest()
    if check != digest[:4]:
        raise ValueError("Invalid checksum")
    return result
