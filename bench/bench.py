"""Correctness-gated benchmark for mojo-base58.

There is no NumPy formulation of a base58 codec, so the fair reference is the
real `base58` package, whose CPython bigint loops are the fastest reasonable
implementation available. Every case compares the two encodings byte-for-byte
before timing, so a broken kernel shows up as a correctness failure rather than
a suspiciously good number.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))

import base58 as real  # noqa: E402

import mojo_base58 as mine  # noqa: E402


def _time(fn, arg, repeats=5):
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn(arg)
        best = min(best, time.perf_counter() - t0)
    return best


def bench(n: int, repeats: int):
    payload = os.urandom(n)
    encoded = mine.b58encode(payload)

    assert encoded == real.b58encode(payload), "encode mismatch"
    assert mine.b58decode(encoded) == payload == real.b58decode(encoded)
    assert mine.b58encode_check(payload) == real.b58encode_check(payload)

    ref_enc = _time(real.b58encode, payload, repeats)
    our_enc = _time(mine.b58encode, payload, repeats)
    ref_dec = _time(real.b58decode, encoded, repeats)
    our_dec = _time(mine.b58decode, encoded, repeats)
    return n, ref_enc, our_enc, ref_dec, our_dec


def main():
    # The kernel wins by a widening margin with size, so the repeat count drops
    # where a single call is already tens of milliseconds.
    plan = [(25, 200), (32, 200), (256, 100), (1024, 50), (4096, 10), (16384, 3)]
    print(f"{'payload':>9}{'enc base58':>13}{'enc mojo':>12}{'x':>7}"
          f"{'dec base58':>13}{'dec mojo':>12}{'x':>7}")
    print("-" * 73)
    for n, repeats in plan:
        n, re_, me, rd, md = bench(n, repeats)
        print(f"{n:>9}{re_*1e3:>11.3f}ms{me*1e3:>10.3f}ms{re_/me:>6.2f}x"
              f"{rd*1e3:>11.3f}ms{md*1e3:>10.3f}ms{rd/md:>6.2f}x")
    print()
    print("ratios above 1.00x favour mojo-base58; below 1.00x is a loss")


if __name__ == "__main__":
    main()
