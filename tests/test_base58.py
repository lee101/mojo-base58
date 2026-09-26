"""Parity against the real `base58` package, plus analytic byte-level checks.

Base58 is exact integer work, so every comparison here is byte-for-byte
(`rtol=0, atol=0` territory). There is no floating point anywhere in the codec
and no FMA contraction to worry about: either the digits match or the kernel is
wrong.
"""

import os

import base58 as real
import numpy as np
import pytest

import mojo_base58 as mine

ALPHA = mine.BITCOIN_ALPHABET


def test_alphabets_match_upstream():
    assert mine.BITCOIN_ALPHABET == real.BITCOIN_ALPHABET
    assert mine.RIPPLE_ALPHABET == real.RIPPLE_ALPHABET
    assert mine.XRP_ALPHABET == real.XRP_ALPHABET
    assert mine.alphabet == real.alphabet


@pytest.mark.parametrize(
    "n", [0, 1, 2, 3, 4, 5, 6, 7, 8, 15, 16, 24, 25, 32, 33, 64, 127]
)
def test_encode_matches_upstream(n):
    payload = os.urandom(n)
    assert mine.b58encode(payload) == real.b58encode(payload)


def test_decode_matches_upstream():
    for text in (b"3YQ", b"1", b"1111", b"z", b"2NEpo7TZRRrLZSi2U", b"8"):
        assert mine.b58decode(text) == real.b58decode(text)


def test_round_trip_preserves_every_byte():
    payload = os.urandom(97)
    assert mine.b58decode(mine.b58encode(payload)) == payload


def test_leading_zero_bytes_become_leading_ones():
    """Each 0x00 byte is not part of the value, so it maps to one `1` char.

    A kernel that folded the zeros into the big integer, or that trimmed them
    without a one-for-one count, would disagree here.
    """
    for count in range(1, 9):
        payload = b"\x00" * count + os.urandom(6)
        got = mine.b58encode(payload)
        assert got == real.b58encode(payload)
        assert got.startswith(b"1" * count)
        assert mine.b58decode(got) == payload


def test_all_zero_payload_is_all_ones():
    assert mine.b58encode(b"\x00" * 11) == b"1" * 11
    assert mine.b58encode(b"\x00" * 11) == real.b58encode(b"\x00" * 11)


def test_leading_ones_decode_to_zero_bytes():
    for count in range(1, 9):
        text = b"1" * count + b"3YQ"
        assert mine.b58decode(text) == real.b58decode(text)
        assert mine.b58decode(text) == b"\x00" * count + mine.b58decode(b"3YQ")


def test_top_chunk_padding_is_trimmed():
    """The kernel emits whole chunks and trims only the last one.

    A one-byte value needs two base58 digits but the kernel works five at a
    time, so a missing trim shows up as three spurious leading `1` characters.
    """
    assert mine.b58encode(b"\x01") == b"2"
    assert mine.b58encode(b"\x01") == real.b58encode(b"\x01")
    assert mine.b58encode(b"\xff") == real.b58encode(b"\xff")
    # 58**5 is one digit past a single chunk, and digit 1 is `2` in this
    # alphabet, so the encoding is `2` followed by five `1`s.
    boundary = (58**5).to_bytes(4, "big")
    assert mine.b58encode(boundary) == b"211111"
    assert mine.b58encode(boundary) == real.b58encode(boundary)
    # 58**5 - 1 is the largest value that still fits in one chunk: all `z`.
    biggest = (58**5 - 1).to_bytes(4, "big")
    assert mine.b58encode(biggest) == b"zzzzz"
    assert mine.b58encode(biggest) == real.b58encode(biggest)
    assert len(mine.b58encode(biggest)) == 5
    assert len(mine.b58encode(boundary)) == 6


@pytest.mark.parametrize("exponent", [0, 1, 5, 6, 40, 100, 400, 1000])
def test_int_encode_matches_upstream(exponent):
    value = (1 << exponent) - 1
    assert mine.b58encode_int(value) == real.b58encode_int(value)
    assert mine.b58decode_int(mine.b58encode_int(value)) == value


def test_int_encode_handles_a_non_power_of_two_magnitude():
    value = 123456789012345678901234567890
    assert mine.b58encode_int(value) == real.b58encode_int(value)
    assert mine.b58decode_int(mine.b58encode_int(value)) == value


def test_zero_int_default_one_switch():
    assert mine.b58encode_int(0) == b"1"
    assert mine.b58encode_int(0) == real.b58encode_int(0)
    assert mine.b58encode_int(0, False) == b""
    assert mine.b58encode_int(0, False) == real.b58encode_int(0, False)


def test_negative_int_is_rejected_rather_than_hanging():
    """Upstream loops forever on a negative input; this raises instead."""
    with pytest.raises(ValueError):
        mine.b58encode_int(-1)


def test_ripple_alphabet_round_trip():
    payload = os.urandom(64)
    got = mine.b58encode(payload, mine.RIPPLE_ALPHABET)
    assert got == real.b58encode(payload, real.RIPPLE_ALPHABET)
    assert mine.b58decode(got, mine.RIPPLE_ALPHABET) == payload


def test_invalid_character_raises_like_upstream():
    for text in (b"0OIl", b"ab0cd", b"!!", b"3YQ\n0"):
        with pytest.raises(ValueError) as ours:
            mine.b58decode(text)
        with pytest.raises(ValueError) as theirs:
            real.b58decode(text)
        assert str(ours.value) == str(theirs.value)


@pytest.mark.parametrize(
    "alphabet,text,autofix",
    [
        (mine.BITCOIN_ALPHABET, b"2I", True),
        (mine.BITCOIN_ALPHABET, b"2l", True),
        (mine.BITCOIN_ALPHABET, b"20", True),
        (mine.BITCOIN_ALPHABET, b"2O", True),
        (mine.BITCOIN_ALPHABET, b"2I", False),
        (mine.RIPPLE_ALPHABET, b"2I", True),
        (mine.RIPPLE_ALPHABET, b"2l", True),
        (mine.RIPPLE_ALPHABET, b"20", True),
        (mine.RIPPLE_ALPHABET, b"2o", True),
        # Two members of `0Oo` in the alphabet: unresolvable, so no folding.
        (b"0Oxyz", b"xo", True),
        (b"0Oxyz", b"x0", True),
        # No member of `Il1` in the alphabet: nothing to fold onto.
        (b"Oxyz", b"xI", True),
        (b"Oxyz", b"xl", True),
        # Exactly one member: the whole group folds onto it.
        (b"Oxyz", b"x0", True),
        (b"Oxyz", b"xo", True),
    ],
)
def test_autofix_matches_upstream(alphabet, text, autofix):
    """A confusable group folds only when the alphabet holds exactly one member.

    The three alphabets above cover all three cases: two members (`0Oxyz`),
    none (`Oxyz` for `Il1`), and one (`BITCOIN_ALPHABET`, `Oxyz` for `0Oo`).
    Without that rule a decoder would silently accept a different string.
    """
    try:
        theirs = real.b58decode(text, alphabet, autofix=autofix)
    except ValueError as exc:
        with pytest.raises(ValueError) as ours:
            mine.b58decode(text, alphabet, autofix=autofix)
        assert str(ours.value) == str(exc)
        return
    assert mine.b58decode(text, alphabet, autofix=autofix) == theirs


def test_trailing_whitespace_is_stripped():
    assert mine.b58decode(b"3YQ \n") == real.b58decode(b"3YQ \n")
    assert mine.b58decode_int(b"3YQ \n") == real.b58decode_int(b"3YQ \n")


def test_str_input_is_ascii_encoded():
    assert mine.b58encode("hi there") == real.b58encode("hi there")
    assert mine.b58decode(mine.b58encode("hi there")) == b"hi there"


def test_check_variants_match_upstream():
    for n in (0, 1, 20, 25, 32, 64):
        payload = os.urandom(n)
        encoded = mine.b58encode_check(payload)
        assert encoded == real.b58encode_check(payload)
        assert mine.b58decode_check(encoded) == payload


def test_corrupt_checksum_is_rejected():
    payload = os.urandom(25)
    encoded = bytearray(mine.b58encode_check(payload))
    encoded[-1] = ALPHA[(ALPHA.index(encoded[-1]) + 1) % len(ALPHA)]
    with pytest.raises(ValueError):
        mine.b58decode_check(bytes(encoded))


def test_chunk_width_keeps_the_packed_chunk_inside_64_bits():
    """The kernel moves `chunk_width` digits per pass, and base**width < 2**32.

    That inequality is what keeps a 32-bit limb times the packed chunk inside 64
    bits; if it were violated the carry would be silently lost.
    """
    from mojo_base58 import _lib

    for base in (2, 8, 10, 16, 36, 58, 64):
        width = _lib.chunk_width(base)
        assert base**width < 2**32
        assert width == 6 or base ** (width + 1) >= 2**32


def test_random_payloads_match_upstream():
    rng = np.random.default_rng(20260926)
    for _ in range(200):
        n = int(rng.integers(0, 300))
        payload = bytes(rng.integers(0, 256, size=n, dtype="uint8"))
        got = mine.b58encode(payload)
        assert got == real.b58encode(payload)
        assert mine.b58decode(got) == payload


def test_random_ints_match_upstream():
    rng = np.random.default_rng(7)
    for _ in range(100):
        value = int.from_bytes(rng.integers(0, 256, size=25, dtype="uint8"), "big")
        assert mine.b58encode_int(value) == real.b58encode_int(value)
        assert mine.b58decode_int(mine.b58encode_int(value)) == value
