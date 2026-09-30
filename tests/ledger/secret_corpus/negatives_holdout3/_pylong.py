"""Python implementations of some algorithms for use by longobject.c.
The goal is to provide asymptotically faster algorithms that can be
used for operations on integers with many digits.  In those cases, the
performance overhead of the Python implementation is not significant
since the asymptotic behavior is what dominates runtime. Functions
provided by this module should be considered private and not part of any
public API.

Note: for ease of maintainability, please prefer clear code and avoid
"micro-optimizations".  This module will only be imported and used for
integers with a huge number of digits.  Saving a few microseconds with
tricky or non-obvious code is not worth it.  For people looking for
maximum performance, they should use something like gmpy2."""

import re
import decimal
try:
    import _decimal
except ImportError:
    _decimal = None

# A number of functions have this form, where `w` is a desired number of
# digits in base `base`:
#
#    def inner(...w...):
#        if w <= LIMIT:
#            return something
#        lo = w >> 1
#        hi = w - lo
#        something involving base**lo, inner(...lo...), j, and inner(...hi...)
#    figure out largest w needed
#    result = inner(w)
#
# They all had some on-the-fly scheme to cache `base**lo` results for reuse.
# Power is costly.
#
# This routine aims to compute all amd only the needed powers in advance, as
# efficiently as reasonably possible. This isn't trivial, and all the
# on-the-fly methods did needless work in many cases. The driving code above
# changes to:
#
#    figure out largest w needed
#    mycache = compute_powers(w, base, LIMIT)
#    result = inner(w)
#
# and `mycache[lo]` replaces `base**lo` in the inner function.
#
# If an algorithm wants the powers of ceiling(w/2) instead of the floor,
# pass keyword argument `need_hi=True`.
#
# While this does give minor speedups (a few percent at best), the
# primary intent is to simplify the functions using this, by eliminating
# the need for them to craft their own ad-hoc caching schemes.
#
# See code near end of file for a block of code that can be enabled to
# run millions of tests.
def compute_powers(w, base, more_than, *, need_hi=False, show=False):
    seen = set()
    need = set()
    ws = {w}
    while ws:
        w = ws.pop() # any element is fine to use next
        if w in seen or w <= more_than:
            continue
        seen.add(w)
        lo = w >> 1
        hi = w - lo
        # only _need_ one here; the other may, or may not, be needed
        which = hi if need_hi else lo
        need.add(which)
        ws.add(which)
        if lo != hi:
            ws.add(w - which)

    # `need` is the set of exponents needed. To compute them all
    # efficiently, possibly add other exponents to `extra`. The goal is
    # to ensure that each exponent can be gotten from a smaller one via
    # multiplying by the base, squaring it, or squaring and then
    # multiplying by the base.
    #
    # If need_hi is False, this is already the case (w can always be
    # gotten from w >> 1 via one of the squaring strategies). But we do
    # the work anyway, just in case ;-)
    #
    # Note that speed is irrelevant. These loops are working on little
    # ints (exponents) and go around O(log w) times. The total cost is
    # insignificant compared to just one of the bigint multiplies.
    cands = need.copy()
    extra = set()
    while cands:
        w = max(cands)
        cands.remove(w)
        lo = w >> 1
        if lo > more_than and w-1 not in cands and lo not in cands:
            extra.add(lo)
            cands.add(lo)
    assert need_hi or not extra

    d = {}
    for n in sorted(need | extra):
        lo = n >> 1
        hi = n - lo
        if n-1 in d:
            if show:
                print("* base", end="")
            result = d[n-1] * base # cheap!
        elif lo in d:
            # Multiplying a bigint by itself is about twice as fast
            # in CPython provided it's the same object.
            if show:
                print("square", end="")
            result = d[lo] * d[lo] # same object
            if hi != lo:
                if show:
                    print(" * base", end="")
                assert 2 * lo + 1 == n
                result *= base
        else: # rare
            if show:
                print("pow", end='')
            result = base ** n
        if show:
            print(" at", n, "needed" if n in need else "extra")
        d[n] = result

    assert need <= d.keys()
    if excess := d.keys() - need:
        assert need_hi
        for n in excess:
            del d[n]
    return d

_unbounded_dec_context = decimal.getcontext().copy()
_unbounded_dec_context.prec = decimal.MAX_PREC
_unbounded_dec_context.Emax = decimal.MAX_EMAX
_unbounded_dec_context.Emin = decimal.MIN_EMIN
_unbounded_dec_context.traps[decimal.Inexact] = 1 # sanity check

def int_to_decimal(n):
    """Asymptotically fast conversion of an 'int' to Decimal."""

    # Function due to Tim Peters.  See GH issue #90716 for details.
    # https://github.com/python/cpython/issues/90716
    #
    # The implementation in longobject.c of base conversion algorithms
    # between power-of-2 and non-power-of-2 bases are quadratic time.
    # This function implements a divide-and-conquer algorithm that is
    # faster for large numbers.  Builds an equal decimal.Decimal in a
    # "clever" recursive way.  If we want a string representation, we
    # apply str to _that_.

    from decimal import Decimal as D
    BITLIM = 200

    # Don't bother caching the "lo" mask in this; the time to compute it is
    # tiny compared to the multiply.
    def inner(n, w):
        if w <= BITLIM:
            return D(n)
        w2 = w >> 1
        hi = n >> w2
        lo = n & ((1 << w2) - 1)
        return inner(lo, w2) + inner(hi, w - w2) * w2pow[w2]

    with decimal.localcontext(_unbounded_dec_context):
        nbits = n.bit_length()
        w2pow = compute_powers(nbits, D(2), BITLIM)
        if n < 0:
            negate = True
            n = -n
        else:
            negate = False
        result = inner(n, nbits)
        if negate:
            result = -result
    return result

def int_to_decimal_string(n):
    """Asymptotically fast conversion of an 'int' to a decimal string."""
    w = n.bit_length()
    if w > 450_000 and _decimal is not None:
        # It is only usable with the C decimal implementation.
        # _pydecimal.py calls str() on very large integers, which in its
        # turn calls int_to_decimal_string(), causing very deep recursion.
        return str(int_to_decimal(n))

    # Fallback algorithm for the case when the C decimal module isn't
    # available.  This algorithm is asymptotically worse than the algorithm
    # using the decimal module, but better than the quadratic time
    # implementation in longobject.c.

    DIGLIM = 1000
    def inner(n, w):
        if w <= DIGLIM:
            return str(n)
        w2 = w >> 1
        hi, lo = divmod(n, pow10[w2])
        return inner(hi, w - w2) + inner(lo, w2).zfill(w2)

    # The estimation of the number of decimal digits.
    # There is no harm in small error.  If we guess too large, there may
    # be leading 0's that need to be stripped.  If we guess too small, we
    # may need to call str() recursively for the remaining highest digits,
    # which can still potentially be a large integer. This is manifested
    # only if the number has way more than 10**15 digits, that exceeds
    # the 52-bit physical address limit in both Intel64 and AMD64.
    w = int(w * 0.3010299956639812 + 1)  # log10(2)
    pow10 = compute_powers(w, 5, DIGLIM)
    for k, v in pow10.items():
        pow10[k] = v << k # 5**k << k == 5**k * 2**k == 10**k
    if n < 0:
        n = -n
        sign = '-'
    else:
        sign = ''
    s = inner(n, w)
    if s[0] == '0' and n:
        # If our guess of w is too large, there may be leading 0's that
        # need to be stripped.
        s = s.lstrip('0')
    return sign + s

def _str_to_int_inner(s):
    """Asymptotically fast conversion of a 'str' to an 'int'."""

    # Function due to Bjorn Martinsson.  See GH issue #90716 for details.
    # https://github.com/python/cpython/issues/90716
    #
    # The implementation in longobject.c of base conversion algorithms
    # between power-of-2 and non-power-of-2 bases are quadratic time.
    # This function implements a divide-and-conquer algorithm making use
    # of Python's built in big int multiplication. Since Python uses the
    # Karatsuba algorithm for multiplication, the time complexity
    # of this function is O(len(s)**1.58).

    DIGLIM = 2048

    def inner(a, b):
        if b - a <= DIGLIM:
            return int(s[a:b])
        mid = (a + b + 1) >> 1
        return (inner(mid, b)
                + ((inner(a, mid) * w5pow[b - mid])
                    << (b - mid)))

    w5pow = compute_powers(len(s), 5, DIGLIM)
    return inner(0, len(s))


# Asymptotically faster version, using the C decimal module. See
# comments at the end of the file. This uses decimal arithmetic to
# convert from base 10 to base 256. The latter is just a string of
# bytes, which CPython can convert very efficiently to a Python int.

# log of 10 to base 256 with best-possible 53-bit precision. Obtained
# via:
#    from mpmath import mp
#    mp.prec = 1000
#    print(float(mp.log(10, 256)).hex())
_LOG_10_BASE_256 = float.fromhex('0x1.a934f0979a371p-2') # about 0.415

# _spread is for internal testing. It maps a key to the number of times
# that condition obtained in _dec_str_to_int_inner:
#     key 0 - quotient guess was right
#     key 1 - quotient had to be boosted by 1, one time
#     key 999 - one adjustment wasn't enough, so fell back to divmod
from collections import defaultdict
_spread = defaultdict(int)
del defaultdict

def _dec_str_to_int_inner(s, *, GUARD=8):
    # Yes, BYTELIM is "large". Large enough that CPython will usually
    # use the Karatsuba _str_to_int_inner to convert the string. This
    # allowed reducing the cutoff for calling _this_ function from 3.5M
    # to 2M digits. We could almost certainly do even better by
    # fine-tuning this and/or using a larger output base than 256.
    BYTELIM = 100_000
    D = decimal.Decimal
    result = bytearray()
    # See notes at end of file for discussion of GUARD.
    assert GUARD > 0 # if 0, `decimal` can blow up - .prec 0 not allowed

    def inner(n, w):
        #assert n < D256 ** w # required, but too expensive to check
        if w <= BYTELIM:
            # XXX Stefan Pochmann discovered that, for 1024-bit ints,
            # `int(Decimal)` took 2.5x longer than `int(str(Decimal))`.
            # Worse, `int(Decimal) is still quadratic-time for much
            # larger ints. So unless/until all that is repaired, the
            # seemingly redundant `str(Decimal)` is crucial to speed.
            result.extend(int(str(n)).to_bytes(w)) # big-endian default
            return
        w1 = w >> 1
        w2 = w - w1
        if 0:
            # This is maximally clear, but "too slow". `decimal`
            # division is asymptotically fast, but we have no way to
            # tell it to reuse the high-precision reciprocal it computes
            # for pow256[w2], so it has to recompute it over & over &
            # over again :-(
            hi, lo = divmod(n, pow256[w2][0])
        else:
            p256, recip = pow256[w2]
            # The integer part will have a number of digits about equal
            # to the difference between the log10s of `n` and `pow256`
            # (which, since these are integers, is roughly approximated
            # by `.adjusted()`). That's the working precision we need,
            ctx.prec = max(n.adjusted() - p256.adjusted(), 0) + GUARD
            hi = +n * +recip # unary `+` chops back to ctx.prec digits
            ctx.prec = decimal.MAX_PREC
            hi = hi.to_integral_value() # lose the fractional digits
            lo = n - hi * p256
            # Because we've been uniformly rounding down, `hi` is a
            # lower bound on the correct quotient.
            assert lo >= 0
            # Adjust quotient up if needed. It usually isn't. In random
            # testing on inputs through 5 billion digit strings, the
            # test triggered once in about 200 thousand tries.
            count = 0
            if lo >= p256:
                count = 1
                lo -= p256
                hi += 1
                if lo >= p256:
                    # Complete correction via an exact computation. I
                    # believe it's not possible to get here provided
                    # GUARD >= 3. It's tested by reducing GUARD below
                    # that.
                    count = 999
                    hi2, lo = divmod(lo, p256)
                    hi += hi2
            _spread[count] += 1
            # The assert should always succeed, but way too slow to keep
            # enabled.
            #assert hi, lo == divmod(n, pow256[w2][0])
        inner(hi, w1)
        del hi # at top levels, can free a lot of RAM "early"
        inner(lo, w2)

    # How many base 256 digits are needed?. Mathematically, exactly
    # floor(log256(int(s))) + 1. There is no cheap way to compute this.
    # But we can get an upper bound, and that's necessary for our error
    # analysis to make sense. int(s) < 10**len(s), so the log needed is
    # < log256(10**len(s)) = len(s) * log256(10). However, using
    # finite-precision floating point for this, it's possible that the
    # computed value is a little less than the true value. If the true
    # value is at - or a little higher than - an integer, we can get an
    # off-by-1 error too low. So we add 2 instead of 1 if chopping lost
    # a fraction > 0.9.

    # The "WASI" test platform can complain about `len(s)` if it's too
    # large to fit in its idea of "an index-sized integer".
    lenS = s.__len__()
    log_ub = lenS * _LOG_10_BASE_256
    log_ub_as_int = int(log_ub)
    w = log_ub_as_int + 1 + (log_ub - log_ub_as_int > 0.9)
    # And what if we've plain exhausted the limits of HW floats? We
    # could compute the log to any desired precision using `decimal`,
    # but it's not plausible that anyone will pass a string requiring
    # trillions of bytes (unless they're just trying to "break things").
    if w.bit_length() >= 46:
        # "Only" had < 53 - 46 = 7 bits to spare in IEEE-754 double.
        raise ValueError(f"cannot convert string of len {lenS} to int")
    with decimal.localcontext(_unbounded_dec_context) as ctx:
        D256 = D(256)
        pow256 = compute_powers(w, D256, BYTELIM, need_hi=True)
        rpow256 = compute_powers(w, 1 / D256, BYTELIM, need_hi=True)
        # We're going to do inexact, chopped arithmetic, multiplying by
        # an approximation to the reciprocal of 256**i. We chop to get a
        # lower bound on the true integer quotient. Our approximation is
        # a lower bound, the multiplication is chopped too, and
        # to_integral_value() is also chopped.
        ctx.traps[decimal.Inexact] = 0
        ctx.rounding = decimal.ROUND_DOWN
        for k, v in pow256.items():
            # No need to save much more precision in the reciprocal than
            # the power of 256 has, plus some guard digits to absorb
            # most relevant rounding errors. This is highly significant:
            # 1/2**i has the same number of significant decimal digits
            # as 5**i, generally over twice the number in 2**i,
            ctx.prec = v.adjusted() + GUARD + 1
            # The unary "+" chops the reciprocal back to that precision.
            pow256[k] = v, +rpow256[k]
        del rpow256 # exact reciprocals no longer needed
        ctx.prec = decimal.MAX_PREC
        inner(D(s), w)
    return int.from_bytes(result)

def int_from_string(s):
    """Asymptotically fast version of PyLong_FromString(), conversion
    of a string of decimal digits into an 'int'."""
    # PyLong_FromString() has already removed leading +/-, checked for invalid
    # use of underscore characters, checked that string consists of only digits
    # and underscores, and stripped leading whitespace.  The input can still
    # contain underscores and have trailing whitespace.
    s = s.rstrip().replace('_', '')
    func = _str_to_int_inner
