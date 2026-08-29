"""Fetch a file over the network only if a pinned key signed it.

Why this exists
---------------
The launcher refuses a self-update that is not signed (``core.self_update``) and
refuses a mod download that does not match a digest pinned inside the signed
executable (``mods.verify``). Both of those protect a path that ends in bytes
being written to disk.

The catalogs are a third path, and until now an unguarded one. ``addons.json``
and ``addon_tips.json`` are fetched live from a raw hosting URL every fifteen
minutes, and a successful fetch replaces what shipped in the executable. That
makes the catalog a **faster and quieter route to every player than a release
is**: a push reaches everyone within the cache window, with no build, no release
artefact, and nothing signed anywhere along the way.

The catalog is not inert data either. Entries name the repository an addon is
installed from and updated from, so whoever controls the catalog controls where
a player's addons come from.

What this does
--------------
Fetches ``<url>`` and ``<url>.sig``, checks the payload against the keys pinned
in ``core.signing``, and returns the text only if that succeeds. Any failure
returns ``None``: no keys, no signature published, a signature that verifies
under no pinned key, a truncated download, a network error.

Why returning ``None`` is the safe direction here
-------------------------------------------------
Unlike a failed update, a failed catalog fetch is not fatal and must not be
loud. The caller falls back to its cache and then to the copy bundled in the
signed executable, so the player keeps a working, trusted catalog and simply
stops receiving new entries until the signature is fixed. Refusing to update is
a much better failure than accepting an unverified update.

⚠️ Deployment note. Signatures have to be published before a build carrying this
code ships, or remote catalog refresh silently stops for everyone on that build.
``tools/sign.py`` already signs an arbitrary file:

    python tools/sign.py --key keys/ichalaunch-key1.pem ichalaunch/data/addons.json

and the resulting ``addons.json.sig`` must sit beside the file at the fetch URL.
"""

from __future__ import annotations

import requests

from ichalaunch.core.logging_setup import log
from ichalaunch.core.signing import (
    SIGNATURE_SUFFIX,
    Signature,
    SignatureError,
    signing_is_configured,
    verify_bytes,
)

# A signature sidecar is a small JSON object. Anything larger is not one, and
# refusing early keeps a hostile host from streaming an unbounded body at us.
_MAX_SIGNATURE_BYTES = 8 * 1024


def signature_url_for(url: str) -> str:
    """Where the detached signature for a fetched file lives."""
    return url + SIGNATURE_SUFFIX


def fetch_verified_text(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    label: str = "file",
) -> str | None:
    """Return the body of *url*, but only if a pinned key signed those bytes.

    Returns ``None`` on every failure. Callers are expected to fall back to a
    cached or bundled copy rather than treating this as an error.

    The bytes that are verified are the same bytes that are returned. The text
    is decoded from them afterwards, so there is no window in which one copy is
    checked and a different copy is used.
    """
    if not signing_is_configured():
        log.warning(
            "Refusing to fetch %s: this build pins no signing keys, so a remote "
            "%s cannot be verified. Using the bundled copy.",
            label,
            label,
        )
        return None

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        log.info("%s fetch failed: %s", label, exc)
        return None
    if response.status_code != 200 or not response.content:
        log.info("%s HTTP %s from %s", label, response.status_code, url)
        return None
    payload = response.content

    sig_url = signature_url_for(url)
    try:
        sig_response = requests.get(sig_url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        log.warning("%s signature fetch failed (%s); keeping the bundled copy", label, exc)
        return None
    if sig_response.status_code != 200:
        log.warning(
            "%s is not signed: HTTP %s for %s. Keeping the bundled copy rather "
            "than trusting an unsigned catalog.",
            label,
            sig_response.status_code,
            sig_url,
        )
        return None
    if len(sig_response.content) > _MAX_SIGNATURE_BYTES:
        log.warning("%s signature is implausibly large; refusing", label)
        return None

    try:
        key_id = verify_bytes(payload, Signature.parse(sig_response.content))
    except SignatureError as exc:
        log.warning(
            "Refusing remote %s: %s. The bundled copy will be used instead.",
            label,
            exc,
        )
        return None

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        log.warning("Refusing remote %s: verified bytes are not UTF-8 (%s)", label, exc)
        return None

    log.info("Verified remote %s against pinned key %s", label, key_id[:12])
    return text
