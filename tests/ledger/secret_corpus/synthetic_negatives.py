"""
Synthetic negatives (D-0007 amendment 2): high-entropy strings that are NOT secrets, and that a coding
agent's events are full of. Any redaction inside them is a false positive. Deterministic, like the
positives. Real-code negatives are the committed files in negatives/ and negatives_external/.
"""
import base64
import hashlib
import uuid

from secret_corpus.corpus import ALNUM, HEX, generator, rand


@generator(category="negative", provider="synthetic", kind="uuid-list", expected="negative", embed=False,
           source="RFC 9562 (UUID layout)")
def uuids(rng):
    ids = [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(rng.randint(3, 8))]
    return "request_ids = [\n" + "".join(f'    "{i}",\n' for i in ids) + "]\n"


@generator(category="negative", provider="synthetic", kind="git-log", expected="negative", embed=False,
           source="git-log(1) --format output; git object ids are SHA-1/SHA-256 hex")
def git_log(rng):
    lines = []
    for _ in range(rng.randint(3, 8)):
        full = rand(rng, HEX, 40)
        lines.append(f"commit {full}\nAuthor: Dev <dev@example.com>\n\n    {rng.choice(['fix: retry', 'feat: add endpoint', 'chore: bump deps'])}\n")
    lines.append("\n".join(f"{rand(rng, HEX, 7)} {rng.choice(['merge main', 'refactor auth', 'docs'])}" for _ in range(4)))
    return "\n".join(lines) + "\n"


@generator(category="negative", provider="synthetic", kind="lockfile-integrity", expected="negative", embed=False,
           source="npm package-lock.json 'integrity' (SRI, W3C Subresource Integrity); pip --hash=sha256")
def lockfile(rng):
    blob = rng.randbytes(64)
    sri = "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()
    pip_hash = hashlib.sha256(blob).hexdigest()
    return (f'"node_modules/pkg-{rand(rng, ALNUM.lower(), 6)}": {{\n  "version": "1.{rng.randint(0, 20)}.0",\n'
            f'  "resolved": "https://registry.npmjs.org/pkg/-/pkg-1.0.0.tgz",\n  "integrity": "{sri}"\n}}\n'
            f"requests==2.32.3 --hash=sha256:{pip_hash}\n")


@generator(category="negative", provider="synthetic", kind="base64-blob", expected="negative", embed=False,
           source="RFC 4648 base64; RFC 2397 data URIs")
def base64_blob(rng):
    data = base64.b64encode(rng.randbytes(rng.randint(48, 400))).decode()
    return rng.choice([
        f'<img src="data:image/png;base64,{data}">\n',
        f'{{"attachment": {{"encoding": "base64", "content": "{data}"}}}}\n',
        f"fixture_bytes = base64.b64decode(\n    \"{data}\"\n)\n",
    ])


@generator(category="negative", provider="synthetic", kind="content-hashes", expected="negative", embed=False,
           source="sha256sum(1) output; Docker image digests (OCI image spec, sha256:<hex>)")
def hashes(rng):
    rows = [f"{rand(rng, HEX, 64)}  dist/{rng.choice(['app', 'cli', 'worker'])}-{rng.randint(1, 9)}.tar.gz" for _ in range(3)]
    return "\n".join(rows) + f"\nimage: ghcr.io/example/app@sha256:{rand(rng, HEX, 64)}\n"
