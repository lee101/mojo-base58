"""Base-N digit conversion for base58, in compiled Mojo.

The numeric core of a base58 codec is one operation: re-expressing an
arbitrary-precision integer in a different radix. Both directions below keep the
value in a little-endian vector of 32-bit limbs and move several radix digits at
a time, so the inner loop is a multiply-accumulate (decode) or a
divide-remainder (encode) over contiguous limbs.

Scratch memory is supplied by the caller. The kernels allocate nothing, which
keeps the exported symbols free of `raises` and lets the Python layer own every
buffer, as the FFI contract requires.

Buffers cross the C ABI as 64-bit addresses and are rebuilt here, because
`@export` rejects a function with an inferred parameter type.
"""

comptime BPtr = Pointer[UInt8, AnyOrigin[mut=True]]
comptime W32 = Pointer[UInt32, AnyOrigin[mut=True]]
comptime I32 = Pointer[Int32, AnyOrigin[mut=True]]

comptime TWO32 = 4294967296


def bu8(addr: Int) -> BPtr:
    return BPtr(unsafe_from_address=addr)


def bw32(addr: Int) -> W32:
    return W32(unsafe_from_address=addr)


def bi32(addr: Int) -> I32:
    return I32(unsafe_from_address=addr)


def bpow(base: Int, k: Int) -> UInt64:
    """Return base**k; every k the callers use keeps the result under 2**32."""
    var r = UInt64(1)
    var b = UInt64(base)
    for _ in range(k):
        r = r * b
    return r


def chunk_width(base: Int) -> Int:
    """Largest number of radix-`base` digits whose packed value stays under 2**32.

    Capping the packed value at 2**32 is what keeps a 32-bit limb times the
    packed chunk inside 64 bits, so no intermediate ever needs a wider type.
    For the bitcoin alphabet (radix 58) this is 5, since 58**5 = 656356768
    fits and 58**6 = 38068704224 does not.
    """
    if base < 2:
        return 1
    var m = UInt64(1)
    var k = 0
    for _ in range(6):
        if m * UInt64(base) >= UInt64(TWO32):
            break
        m = m * UInt64(base)
        k += 1
    if k == 0:
        return 1
    return k


@export("b58_bytes_to_words")
def b58_bytes_to_words(
    src_addr: Int,
    n: Int,
    alpha_addr: Int,
    base: Int,
    limb_addr: Int,
    max_limbs: Int,
    wbuf_addr: Int,
    max_words: Int,
) abi("C") -> Int:
    """Reinterpret a big-endian byte string in radix `base`.

    `limbs` is scratch of at least `max_limbs` UInt32, `wbuf` is scratch of at
    least `max_words` bytes. The radix words are written into the tail of
    `wbuf`, most significant first, and the index of the first one written is
    returned, so the caller reads `wbuf[start:]`. Zero inputs write nothing and
    return `max_words`, matching the `default_one=False` behaviour of
    `base58.b58encode`.
    """
    if n <= 0 or max_words <= 0 or base < 2:
        return max_words
    var src = bu8(src_addr)
    var alpha = bu8(alpha_addr)
    var limbs = bw32(limb_addr)
    var wb = bu8(wbuf_addr)

    var nlimb = (n * 8 + 31) // 32
    if nlimb > max_limbs:
        return -1
    for i in range(nlimb):
        limbs[unsafe_offset=i] = UInt32(0)
    # Pack the big-endian byte stream into 32-bit limbs. The last byte of the
    # stream is the least significant, so it lands at the bottom of limb 0.
    for b in range(n):
        var rev = n - 1 - b
        var limb = rev // 4
        var shift = 8 * (rev % 4)
        limbs[unsafe_offset=limb] = limbs[unsafe_offset=limb] | (
            UInt32(src[unsafe_offset=b]) << UInt32(shift)
        )

    var top = nlimb - 1
    while top >= 0:
        if limbs[unsafe_offset=top] != UInt32(0):
            break
        top -= 1
    if top < 0:
        return 0

    var K = chunk_width(base)
    var M = bpow(base, K)
    var pos = max_words
    var start = max_words

    while top >= 0:
        var rem = UInt64(0)
        for j in range(top + 1):
            var i = top - j
            var cur = (rem << UInt64(32)) | UInt64(limbs[unsafe_offset=i])
            limbs[unsafe_offset=i] = UInt32(cur // M)
            rem = cur % M
        while top >= 0:
            if limbs[unsafe_offset=top] != UInt32(0):
                break
            top -= 1
        if pos < K:
            return -1
        # Every pass but the last is a full K digits wide, so its leading zero
        # digits are real. The last pass is narrower, and its padding is not.
        var ndig = 0
        var probe = rem
        while probe != UInt64(0):
            probe = probe // UInt64(base)
            ndig += 1
        pos -= K
        for j in range(K):
            var d = Int(rem % UInt64(base))
            rem = rem // UInt64(base)
            wb[unsafe_offset=pos + (K - 1 - j)] = alpha[unsafe_offset=d]
        if top < 0:
            start = pos + (K - ndig)

    return start


@export("b58_words_to_bytes")
def b58_words_to_bytes(
    src_addr: Int,
    n: Int,
    dmap_addr: Int,
    base: Int,
    limb_addr: Int,
    max_limbs: Int,
    obuf_addr: Int,
    out_cap: Int,
) abi("C") -> Int:
    """Reinterpret a radix-`base` word string as a big-endian byte string.

    `dmap` is a 256-entry table of radix values, with -1 marking a byte that is
    not in the alphabet. On an unknown byte the call returns -(2 + index) so the
    caller can name the offending character; -1 means the scratch or output
    space was too small. Otherwise the return value is the number of
    significant bytes written, with leading zero bytes already stripped.
    """
    if base < 2:
        return -1
    var src = bu8(src_addr)
    var dmap = bi32(dmap_addr)
    var limbs = bw32(limb_addr)
    var ob = bu8(obuf_addr)

    for i in range(n):
        if dmap[unsafe_offset=src[unsafe_offset=i]] < 0:
            return -(2 + i)

    var used = 1
    limbs[unsafe_offset=0] = UInt32(0)
    var K = chunk_width(base)

    var i = 0
    while i < n:
        var k = K
        if n - i < k:
            k = n - i
        var g = UInt64(0)
        for j in range(k):
            var d = UInt64(dmap[unsafe_offset=src[unsafe_offset=i + j]])
            g = g * UInt64(base) + d
        var m = bpow(base, k)
        var carry = g
        for j in range(used):
            var t = UInt64(limbs[unsafe_offset=j]) * m + carry
            limbs[unsafe_offset=j] = UInt32(t & UInt64(0xFFFFFFFF))
            carry = t >> UInt64(32)
        while carry != UInt64(0):
            if used >= max_limbs:
                return -1
            limbs[unsafe_offset=used] = UInt32(carry & UInt64(0xFFFFFFFF))
            carry = carry >> UInt64(32)
            used += 1
        i += k

    # Drop whole zero limbs from the top, then zero bytes within the top limb.
    # A value of zero writes no bytes at all rather than a single 0x00, so the
    # caller sees the same empty result `b58decode` gets from an all-'1' input.
    var top = used - 1
    while top > 0:
        if limbs[unsafe_offset=top] != UInt32(0):
            break
        top -= 1
    var nbytes = 0
    if limbs[unsafe_offset=top] != UInt32(0):
        var btop = 3
        while btop > 0:
            if (limbs[unsafe_offset=top] >> UInt32(8 * btop)) != UInt32(0):
                break
            btop -= 1
        nbytes = top * 4 + (btop + 1)
    if nbytes > out_cap:
        return -1
    for b in range(nbytes):
        var li = nbytes - 1 - b
        ob[unsafe_offset=b] = UInt8(
            (limbs[unsafe_offset=li // 4] >> UInt32(8 * (li % 4))) & UInt32(0xFF)
        )
    return nbytes


@export("b58_chunk_width")
def b58_chunk_width(base: Int) abi("C") -> Int:
    """Expose `chunk_width` so the Python layer can size its scratch exactly."""
    return chunk_width(base)
