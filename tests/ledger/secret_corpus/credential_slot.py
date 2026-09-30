"""
"Random value in a credential slot" (D-0011 amendment 8): high-entropy values that are not known secret
formats, placed ONLY in contexts that name a credential (build(..., credential_slots=...)). Expected
behaviour: strip. Stripping is scored as correct, in its own group, never as a false positive.
"""
import base64
import uuid

from secret_corpus.corpus import ALNUM, HEX, generator, rand

_SRC = "D-0011 amendment 8 (owner): random values in credential slots are expected to be stripped"


@generator(category="credential-slot", provider="random", kind="hex-64", source=_SRC)
def hex64(rng):
    return rand(rng, HEX, 64)


@generator(category="credential-slot", provider="random", kind="hex-40", source=_SRC)
def hex40(rng):
    return rand(rng, HEX, 40)


@generator(category="credential-slot", provider="random", kind="base64-32-bytes", source=_SRC)
def b64_32(rng):
    return base64.b64encode(rng.randbytes(32)).decode()


@generator(category="credential-slot", provider="random", kind="uuid", source=_SRC)
def uuid4(rng):
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


@generator(category="credential-slot", provider="random", kind="alnum-24", source=_SRC)
def alnum24(rng):
    return rand(rng, ALNUM, 24)
