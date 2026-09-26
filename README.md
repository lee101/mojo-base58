# mojo-base58

`mojo-base58` is a drop-in replacement for
[base58](https://pypi.org/project/base58/) 2.1.1 with the arbitrary-precision
radix conversion running as compiled Mojo. The public names, argument handling
and error messages are the same, so it imports alongside the real package; the
parity tests compare the two directly.

```python
import mojo_base58 as b58

b58.b58encode(b"\x00\x00hello")
# b'11Cn8eVZg'
b58.b58encode_check(bytes.fromhex("f54c5856264e7ac5b4a39e0a4a1f1c4f0f0f0f0f"))
# b'PN21YhXwmcoopn5K7KNEzwjqcpzA59RYy'
b58.b58decode("3YQ")
# b'!'
```

## Why this is a real port

A base58 codec spends essentially all of its time in one place: re-expressing a
large integer in a different radix. `base58` does that with a Python-level
`divmod` chain over a CPython bigint — one interpreter-level call per output
digit. That is a real numeric loop over a growing number, and it is exactly the
kind of work a compiled inner loop is for.

The kernels here keep the value in a little-endian vector of 32-bit limbs and
move several radix digits per pass. `chunk_width` picks the largest digit count
whose packed value still fits under `2**32`, which is what keeps a 32-bit limb
times the packed chunk inside 64 bits with no wider intermediate. For the
bitcoin alphabet that is 5 digits, since `58**5 = 656356768` fits and
`58**6 = 38068704224` does not.

## Covered subset

| area | implemented API | where the work happens |
| --- | --- | --- |
| Byte string codec | `b58encode` | Mojo: big-endian bytes to radix words, plus leading-zero accounting in Python |
| Byte string codec | `b58decode` | Mojo: radix words to big-endian bytes, plus symbol validation in Python |
| Integer codec | `b58encode_int`, `b58decode_int` | Mojo, via the same radix conversion |
| Checksums | `b58encode_check`, `b58decode_check` | Mojo for the base58 half; `hashlib` SHA-256d for the digest |
| Alphabets | `BITCOIN_ALPHABET`, `RIPPLE_ALPHABET`, `XRP_ALPHABET`, `alphabet` | Python constants, identical to upstream |
| Autofix | `autofix=` on both decode entry points | Python builds the 256-entry symbol table; Mojo indexes it |

Not implemented, and not invented:

- Nothing of the base58 surface is missing, but nothing beyond it is added
  either. There is no block-explorer helper, no address validation, and no
  multibase wrapper; those are string policy, not numeric work.
- SHA-256 stays on `hashlib`. That is already a C primitive in the standard
  library, and a slower reimplementation would be a defect rather than a port.

One deliberate behavioural difference: `b58encode_int(-1)` raises `ValueError`
here. Upstream's `while i:` loop never terminates on a negative input, because
`divmod` on a negative dividend never reaches zero; a hang is not a contract
worth reproducing.

## Install

The repository pins its own Mojo toolchain:

```bash
pixi install
pixi run build
pixi run test
```

`pixi run build` produces `dist/libmojo-base58.so`. Set `PYTHONPATH=python` when
using the package outside a Pixi task.

## Performance

Best-of-N wall clock in one process, against the real `base58` package. There is
no NumPy formulation of a base58 codec, so the upstream bigint loops are the
fair reference. Every case verifies byte-for-byte agreement before timing.

| payload | encode base58 | encode mojo | ratio | decode base58 | decode mojo | ratio |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 25 B | 0.015 ms | 0.025 ms | 0.59x | 0.016 ms | 0.052 ms | 0.31x |
| 32 B | 0.019 ms | 0.023 ms | 0.85x | 0.018 ms | 0.042 ms | 0.42x |
| 256 B | 0.236 ms | 0.064 ms | 3.69x | 0.299 ms | 0.056 ms | 5.32x |
| 1 KiB | 3.398 ms | 0.568 ms | 5.99x | 3.188 ms | 0.114 ms | 27.96x |
| 4 KiB | 50.526 ms | 8.685 ms | 5.82x | 46.880 ms | 0.940 ms | 49.89x |
| 16 KiB | 1137.233 ms | 143.944 ms | 7.90x | 759.483 ms | 12.841 ms | 59.14x |

The small-payload rows are losses and are reported as losses. A 25-byte bitcoin
address is one or two chunks of limb work either way, so the whole cost is the
ctypes call plus two NumPy scratch allocations, and CPython's bigint gets there
with a handful of `divmod` calls. The crossover is around 100 bytes, which is
why address-heavy code will not see a win from this.

Decode wins by more than encode because the upstream decode path is a Python
`for char in v: decimal = decimal * base + map[char]` loop, one dict lookup and
one bigint multiply-add per input character.

Reproduce with:

```bash
pixi run bench
```

## How it works

All kernels live in `src/kernels.mojo`, one compilation unit, because shared
library build cost is largely fixed. `build/build.sh` compiles it with
`mojo build --emit shared-lib` into `dist/libmojo-base58.so`.

The Python layer owns every array. Scratch limb buffers are allocated per call
as `numpy` arrays, which keeps the exported symbols free of allocation and
therefore free of `raises` — an `@export ... abi("C")` function cannot be
`raises`. Buffers cross the C ABI as 64-bit addresses and are rebuilt in Mojo
as `Pointer[T, AnyOrigin[mut=True]]`, because `@export` rejects a function whose
parameter types are inferred.

Two details are easy to get wrong and are pinned by tests:

- **The last chunk is trimmed, the others are not.** A division pass always
  produces a whole chunk of digits, and only the final pass, the one that
  exhausts the value, has padding that must be dropped. A one-byte input needs
  two digits but the kernel works five at a time.
- **The top chunk's digit count, not its remainder width, sets the output
  length.** Counting digits rather than trimming a fixed amount is what makes
  `b58encode(b"\x00"*11) == b"1"*11` come out right.

Encoding divides the limb vector by `base**chunk_width` and emits that many
digits per pass; decoding multiplies by `base**k` and adds a `k`-digit group at
a time. Both directions are exact integer work, so the parity tests assert
byte equality throughout. Nothing in the codec is floating point, so the usual
FMA caveat does not apply here.

## Tests

```bash
pixi run test
```

59 tests, all parity against the real `base58` package or exact integer
identities. Highlights: the chunk-trimming boundary at `58**5`, one-for-one
leading-zero accounting in both directions, the `autofix` rule exercised across
all three pivot counts (zero, one and two members of a confusable group), and
300 randomised payload/integer comparisons.

## License

MIT
