"""Content pinning for everything the mod installer downloads.

The launcher already refuses a self-update that is not signed by a pinned key
(``ichalaunch.core.signing``). That protects the launcher updating *itself*. It
says nothing about the DLLs, patches and archives the launcher installs *into
the game*, which arrive from a dozen third-party GitHub accounts, a Google
Drive folder and an R2 bucket. Any one of those accounts being compromised puts
arbitrary code into the game directory of every player who clicks Apply.

Why a hash is enough here, when it is not enough for the self-update
-------------------------------------------------------------------
``signing`` argues that a hash cannot establish trust, because whoever controls
the host that publishes the hash also controls the file. That objection does
not apply to this module, and the difference is where the number lives.

These digests are not fetched. They are written into ``mods.json``, which ships
inside the launcher executable, which is itself covered by the Ed25519 chain in
``signing``. So the digest is already anchored to our signing key before any
download begins. The host serving the bytes never gets a say in what they are
compared against. An attacker who owns the upstream release, the bucket, or DNS
can serve whatever they like and the install still refuses.

What a pin does and does not promise
------------------------------------
A pin says "these are the exact bytes a maintainer downloaded, tested, and
chose". It does not say the bytes are safe. Pinning a malicious file pins
malice perfectly. The pin removes the *upstream* from the trust set; it does
not remove the maintainer.

Unpinned sources still install, but there are none left
-------------------------------------------------------
An entry without ``sha256`` installs as before. That is deliberate: absence of a
pin is a maintainer decision recorded in a signed catalog, not something an
attacker can arrange, and it allowed pins to land without breaking every install
at once.

Every downloadable source in the shipped catalog is now pinned, and the suite
asserts that it stays that way via ``unpinned_source_ids()``. So the permissive
branch is a migration affordance rather than a live gap: adding a mod without a
digest fails a test rather than reaching a player.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024


class SourceHashMismatch(Exception):
    """A download did not match the digest pinned for it.

    Raised instead of returning a flag because there is no safe way to continue.
    """


def normalized_digest(value: Any) -> str | None:
    """A catalog ``sha256`` field as 64 lowercase hex chars, or None if unusable.

    Accepts the ``sha256:`` prefix and surrounding whitespace so a digest pasted
    from ``Get-FileHash`` or ``sha256sum`` works unedited. Anything that is not
    a well-formed SHA-256 returns None and is treated as "no pin" rather than as
    a failed comparison, so a typo cannot silently become a permanent refusal.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:") :].strip()
    if len(text) != 64:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return text


def expected_digest(source: dict[str, Any] | None) -> str | None:
    """The digest pinned for this catalog source, if any."""
    if not isinstance(source, dict):
        return None
    return normalized_digest(source.get("sha256"))


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_path(path: Path) -> str:
    """Hash a file by streaming it.

    Deliberately does not use ``core.filesystem.sha256_file``: that helper
    returns None for locked or missing paths so that *detection* can skip them.
    Skipping is the wrong answer here. A payload we cannot read is a payload we
    cannot verify, and the caller must refuse rather than shrug.
    """
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def verify_payload(
    source: dict[str, Any] | None,
    payload: Path | bytes,
    *,
    label: str = "download",
) -> str | None:
    """Check a freshly downloaded payload against its pin.

    Returns the digest actually seen when a pin was present and matched, or None
    when the source carries no pin. Raises :class:`SourceHashMismatch` when a pin
    is present and the bytes do not match it, or when the payload cannot be read
    to be hashed.

    Fails closed in every direction. There is deliberately no override, because
    an override is the first thing an attacker would talk a user into using.
    """
    expected = expected_digest(source)
    if expected is None:
        return None

    try:
        actual = digest_bytes(payload) if isinstance(payload, bytes) else digest_path(payload)
    except OSError as exc:
        raise SourceHashMismatch(
            f"Refusing to install {label}: the download could not be read to "
            f"verify it against the expected SHA-256 ({exc})."
        ) from exc

    if actual != expected:
        raise SourceHashMismatch(
            f"Refusing to install {label}: content does not match the pinned "
            f"SHA-256.\n  expected {expected}\n  got      {actual}\n"
            "The file served upstream is not the file this build was built to "
            "trust. Nothing has been written to the game directory."
        )
    return actual


def unpinned_source_ids(catalog: list[dict[str, Any]]) -> list[str]:
    """Ids of catalog entries that download something without a pin covering it.

    ``local`` sources are excluded: they copy a file the user already has on
    disk rather than fetching one, so there is no upstream to distrust.
    """
    out: list[str] = []
    for mod in catalog:
        mod_id = str(mod.get("id") or "")
        if not mod_id:
            continue
        for key in ("source", "addon_source"):
            source = mod.get(key)
            if not isinstance(source, dict):
                continue
            if source.get("type") == "local":
                continue
            if expected_digest(source) is None:
                out.append(mod_id if key == "source" else f"{mod_id}:{key}")
    return out
