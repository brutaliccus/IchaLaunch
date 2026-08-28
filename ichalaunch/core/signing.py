"""Ed25519 signature verification for anything the launcher installs.

The rule this module exists to enforce: **the launcher trusts a signature, never
a server.** Once a payload is covered by a signature from a key pinned below, it
does not matter whether it arrived over HTTPS, from a CDN, from a torrent swarm,
from a mirror, or from a stranger's USB stick. Everything that moves bytes
becomes untrusted plumbing.

A hash alone cannot do this job. A hash proves the bytes match a number; it says
nothing about who chose the number. Whoever controls the host that publishes the
hash also controls the file, so an attacker holding the release credential, the
bucket keys, or DNS simply rewrites both. TLS authenticates the *server*, not the
artefact. A hash detects corruption. A signature detects tampering.

Why several pinned keys
-----------------------
A pinned key cannot revoke itself through the channel it controls. If exactly one
key is pinned and it leaks, recovery means every player manually re-downloading
over an unauthenticated channel, which is precisely what an attacker
impersonates. So more than one key is pinned from the first release, only the
first is ever used to sign, and the rest stay offline in different hands. On
compromise a backup key signs a payload that names the bad key in ``revoke``,
and clients drop it permanently.

**Pinning extra keys costs nothing today and cannot be retrofitted later.**
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Pinned public keys. Raw Ed25519 public keys, 32 bytes, base64.
#
# Generate with tools/keygen.py, which emits all three at once and never writes
# a private key anywhere but the path you name.
#
#   key 1  active signer
#   key 2  offline backup, held by a different person, on removable media
#   key 3  offline backup, held by a third person / printed
#
# Publish these fingerprints in the repo README, on the server's site, and in a
# pinned message, so that a key swap in the repository alone is visible.
# ---------------------------------------------------------------------------
PINNED_KEYS: tuple[str, ...] = (
    # PLACEHOLDER - replace before the first signed release. Empty tuple means
    # verification cannot succeed, which is the correct failure direction.
)

SIGNATURE_SUFFIX = ".sig"


class SignatureError(Exception):
    """Raised when a payload is not covered by a trusted signature.

    Callers must let this propagate. There is deliberately no 'continue anyway'
    path and no --insecure flag: an update that cannot be verified is an update
    that does not get installed.
    """


@dataclass(frozen=True)
class Signature:
    """A detached signature and the key that is claimed to have made it."""

    key_id: str
    signature: bytes

    @classmethod
    def parse(cls, raw: bytes | str) -> "Signature":
        """Read the sidecar format: one JSON object, no comments, no options.

        ``{"key_id": "<b64 pubkey>", "sig": "<b64 signature>"}``

        Deliberately not PGP: keyrings, expiry surprises, and a verify that
        succeeds for any key the machine happens to know are all traps for a
        small team.
        """
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            obj = json.loads(text)
            key_id = str(obj["key_id"]).strip()
            signature = base64.b64decode(str(obj["sig"]).strip(), validate=True)
        except Exception as exc:  # noqa: BLE001 - any malformed input is a failure
            raise SignatureError(f"Signature file is not readable: {exc}") from exc
        if len(signature) != 64:
            raise SignatureError(
                f"Signature is {len(signature)} bytes, expected 64 (Ed25519)"
            )
        return cls(key_id=key_id, signature=signature)


def _load_public_key(b64: str):
    """One pinned key as a cryptography public-key object."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        raw = base64.b64decode(b64.strip(), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SignatureError(f"Pinned key is not valid base64: {exc}") from exc
    if len(raw) != 32:
        raise SignatureError(f"Pinned key is {len(raw)} bytes, expected 32 (Ed25519)")
    return Ed25519PublicKey.from_public_bytes(raw)


def trusted_keys(revoked: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """Pinned keys minus any that have been revoked by a signed payload."""
    return tuple(k for k in PINNED_KEYS if k.strip() and k.strip() not in revoked)


def verify_bytes(
    payload: bytes,
    signature: Signature,
    *,
    revoked: frozenset[str] = frozenset(),
) -> str:
    """Return the key id that signed *payload*, or raise ``SignatureError``.

    The claimed ``key_id`` selects which pinned key to try, but it is not itself
    trusted: an attacker can write anything there. It only narrows the search,
    and a signature that verifies under no pinned key fails regardless.
    """
    from cryptography.exceptions import InvalidSignature

    candidates = trusted_keys(revoked)
    if not candidates:
        raise SignatureError(
            "No trusted signing keys are pinned in this build, so nothing can be "
            "verified. This launcher will not install unverified updates."
        )

    ordered = [k for k in candidates if k.strip() == signature.key_id]
    ordered += [k for k in candidates if k.strip() != signature.key_id]

    for key_b64 in ordered:
        try:
            _load_public_key(key_b64).verify(signature.signature, payload)
        except InvalidSignature:
            continue
        except SignatureError:
            continue
        return key_b64.strip()

    raise SignatureError(
        "Signature does not match any trusted key. The file was modified after "
        "signing, or it was signed by a key this launcher does not trust."
    )


def verify_file(
    path: Path,
    sig_path: Path | None = None,
    *,
    revoked: frozenset[str] = frozenset(),
) -> str:
    """Verify *path* against its detached sidecar. Returns the signing key id.

    Reads the payload once, from the same path the caller is about to use, so
    there is no window between verifying one copy and installing another.
    """
    sig_path = sig_path or path.with_name(path.name + SIGNATURE_SUFFIX)
    if not sig_path.is_file():
        raise SignatureError(
            f"No signature found beside {path.name}. Expected {sig_path.name}."
        )
    return verify_bytes(path.read_bytes(), Signature.parse(sig_path.read_bytes()), revoked=revoked)


def signing_is_configured() -> bool:
    """True when this build pins at least one key.

    Lets callers give a clear message during the changeover, rather than a
    confusing verification failure, while PINNED_KEYS is still empty.
    """
    return any(k.strip() for k in PINNED_KEYS)
