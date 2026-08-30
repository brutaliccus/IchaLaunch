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
# Generated with the key ceremony (tools/keygen.py). Private keys are NOT in
# this repository. Fingerprints (SHA-256 of the raw 32-byte public key) are
# also listed in the README so a swap here alone is visible.
#
#   key 1  active signer
#   key 2  offline backup, held by a different person, on removable media
#   key 3  offline backup, held by a third person / printed
# ---------------------------------------------------------------------------
PINNED_KEYS: tuple[str, ...] = (
    # key 1 - ACTIVE SIGNER  sha256:04fd0725af49fcb3a1fbe69845ef3bb1007ecc911ece3a093d7e623fe8878a23
    "cNGIDU6hwD8nlj+kXxMr32a47pRNYPszTfH7S4p+oEk=",
    # key 2 - BACKUP         sha256:b75ae8582b9e4f338f7af4a7e77540445b988bcfb6bab04a4c1e91003f7c3272
    "CRKDjrxlpw9o//JB0uIIAxL/TYKhzeVNdKwokWeMhvE=",
    # key 3 - BACKUP         sha256:92991a640ca7adc5b49f69a75af799a6cca4c5521db99527c6cc5b69b9476752
    "aHwLZWIufvs6ZQGGQ2WwBRSAdCAqeMl6Mtucf1AbpWI=",
)

SIGNATURE_SUFFIX = ".sig"


class SignatureError(Exception):
    """Raised when a payload is not covered by a trusted signature.

    Callers must let this propagate. There is deliberately no 'continue anyway'
    path and no --insecure flag: an update that cannot be verified is an update
    that does not get installed.
    """


# What an attestation is for, so a signature made for one job can never be
# replayed as another. Any future signed artefact gets its own purpose string.
ATTESTATION_PURPOSE_UPDATE = "ichalaunch-launcher-update"


@dataclass(frozen=True)
class Attestation:
    """What the signer is asserting about a payload, beyond "I signed it".

    A raw payload signature answers "did we produce these bytes". It cannot
    answer "did we produce these bytes *as version 1.5.2*", and that gap is
    exploitable: an attacker who can publish a release, holding no key at all,
    re-uploads a genuine older build together with its genuine signature under a
    newer tag. Every check passes, because the bytes really were signed by us,
    and every client silently installs the older build. Every fix since then is
    undone in one step.

    Binding the version and the purpose *inside* the signed data closes that,
    because the version is no longer something the publisher can restate.
    """

    purpose: str
    version: str
    sha256: str

    def canonical(self) -> bytes:
        """The exact bytes that get signed.

        Sorted keys, no whitespace, UTF-8. Signer and verifier must derive this
        identically from the same fields or a valid signature will not verify,
        so it is defined once, here, and never rebuilt by a caller.
        """
        return json.dumps(
            {"purpose": self.purpose, "sha256": self.sha256, "version": self.version},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


@dataclass(frozen=True)
class Signature:
    """A detached signature and the key that is claimed to have made it."""

    key_id: str
    signature: bytes
    attestation: "Attestation | None" = None
    attestation_signature: bytes | None = None

    @classmethod
    def parse(cls, raw: bytes | str) -> "Signature":
        """Read the sidecar format: one JSON object, no comments, no options.

        ``{"key_id": "<b64 pubkey>", "sig": "<b64 signature>"}``

        Optionally, and preferred:

        ``{"key_id": ..., "sig": ..., "attestation": {"purpose": ...,
        "version": ..., "sha256": ...}, "attestation_sig": "<b64>"}``

        ``sig`` still covers the raw payload and is unchanged, so a sidecar
        carrying an attestation is still accepted by builds that predate this
        field. That matters: the client doing the verifying is the *old* one, so
        a format that older builds reject would strand everyone on the release
        before the fix.

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

        attestation = None
        attestation_signature = None
        if obj.get("attestation") is not None or obj.get("attestation_sig") is not None:
            # Half an attestation is not a legacy sidecar, it is a broken or
            # tampered one. Refuse rather than silently dropping to the weaker path.
            try:
                body = obj["attestation"]
                attestation = Attestation(
                    purpose=str(body["purpose"]).strip(),
                    version=str(body["version"]).strip(),
                    sha256=str(body["sha256"]).strip().lower(),
                )
                attestation_signature = base64.b64decode(
                    str(obj["attestation_sig"]).strip(), validate=True
                )
            except Exception as exc:  # noqa: BLE001
                raise SignatureError(f"Attestation is not readable: {exc}") from exc
            if len(attestation_signature) != 64:
                raise SignatureError(
                    f"Attestation signature is {len(attestation_signature)} bytes, "
                    "expected 64 (Ed25519)"
                )

        return cls(
            key_id=key_id,
            signature=signature,
            attestation=attestation,
            attestation_signature=attestation_signature,
        )


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


def verify_attestation(
    payload: bytes,
    signature: Signature,
    *,
    expected_purpose: str,
    expected_version: str | None = None,
    revoked: frozenset[str] = frozenset(),
) -> str:
    """Verify what the signer *asserted* about the payload, not just the bytes.

    Returns the key id that signed the attestation. Raises ``SignatureError`` if
    there is no attestation, if it does not verify, or if any field disagrees
    with what the caller is actually installing.

    The digest is recomputed here from the payload the caller holds, not read
    from anywhere else, so there is no window in which a verified digest and an
    installed file can differ.
    """
    import hashlib

    if signature.attestation is None or signature.attestation_signature is None:
        raise SignatureError(
            "This signature carries no attestation, so it cannot prove which "
            "version it covers."
        )

    att = signature.attestation
    inner = Signature(key_id=signature.key_id, signature=signature.attestation_signature)
    key_id = verify_bytes(att.canonical(), inner, revoked=revoked)

    if att.purpose != expected_purpose:
        raise SignatureError(
            f"Attestation is for {att.purpose!r}, not {expected_purpose!r}. A "
            "signature made for one job must not be reusable for another."
        )

    actual = hashlib.sha256(payload).hexdigest()
    if actual != att.sha256:
        raise SignatureError(
            "Attested digest does not match the payload.\n"
            f"  attested {att.sha256}\n  actual   {actual}"
        )

    if expected_version is not None and att.version != expected_version:
        raise SignatureError(
            f"Attestation says this is version {att.version!r}, but it is being "
            f"installed as {expected_version!r}. A genuine build published under "
            "the wrong tag is exactly what this check exists to catch."
        )

    return key_id


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
