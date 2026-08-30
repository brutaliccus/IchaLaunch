#!/usr/bin/env python3
"""Sign a release artefact so the launcher will accept it.

Usage:
    python tools/sign.py --key keys/ichalaunch-key1.pem dist/IchaLaunch.exe

Writes ``dist/IchaLaunch.exe.sig`` beside it. Upload BOTH to the release.

Fails closed
------------
Before it exits, this re-verifies its own output using the exact same verifier
the launcher imports, against the keys actually pinned in this checkout. So a
signature made with a key nobody trusts, or a mismatched file, is caught here
rather than by every player at once.

The private key never leaves this machine and is never read by CI. If signing
ever moves into a GitHub Action, the scheme is worthless: anyone who can push a
workflow file can read the secret, which reduces "trusted release" back to
"whoever has repo access".
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", type=Path, help="file to sign")
    ap.add_argument("--key", type=Path, required=True, help="private key PEM from keygen.py")
    ap.add_argument("--out", type=Path, default=None, help="signature path (default: <target>.sig)")
    ap.add_argument(
        "--version",
        default=None,
        help="version this artefact IS (default: ichalaunch.__version__). Binding it "
             "inside the signature is what stops a genuine build being republished "
             "under a different tag.",
    )
    args = ap.parse_args()

    from cryptography.hazmat.primitives import serialization

    from ichalaunch import __version__
    from ichalaunch.core.signing import (
        ATTESTATION_PURPOSE_UPDATE,
        Attestation,
        Signature,
        SignatureError,
        signing_is_configured,
        verify_attestation,
        verify_bytes,
    )

    if not args.target.is_file():
        print(f"No such file: {args.target}", file=sys.stderr)
        return 1
    if not args.key.is_file():
        print(f"No such key: {args.key}", file=sys.stderr)
        return 1

    payload = args.target.read_bytes()
    pw = getpass.getpass(f"password for {args.key.name}: ").encode()
    try:
        priv = serialization.load_pem_private_key(args.key.read_bytes(), password=pw)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not unlock the key: {exc}", file=sys.stderr)
        return 1

    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = base64.b64encode(pub_raw).decode()
    sig = priv.sign(payload)

    # Sign a second, tiny thing that says what this payload IS. The payload
    # signature alone proves origin; it says nothing about version, so a genuine
    # old build can be republished under a new tag by anyone who can cut a
    # release. The attestation moves the version inside the signature, where the
    # publisher cannot restate it.
    version = (args.version or __version__).strip()
    attestation = Attestation(
        purpose=ATTESTATION_PURPOSE_UPDATE,
        version=version,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    attestation_sig = priv.sign(attestation.canonical())

    sidecar = json.dumps(
        {
            "key_id": key_id,
            "sig": base64.b64encode(sig).decode(),
            "attestation": {
                "purpose": attestation.purpose,
                "version": attestation.version,
                "sha256": attestation.sha256,
            },
            "attestation_sig": base64.b64encode(attestation_sig).decode(),
        },
        indent=2,
    ) + "\n"

    # Verify BEFORE writing anything, using the launcher's own code path.
    if not signing_is_configured():
        print(
            "This checkout pins no keys in ichalaunch/core/signing.py, so the "
            "signature cannot be checked and no launcher would accept it.\n"
            "Run tools/keygen.py and paste PINNED_KEYS in first.",
            file=sys.stderr,
        )
        return 1
    try:
        parsed = Signature.parse(sidecar)
        used = verify_bytes(payload, parsed)
        verify_attestation(
            payload,
            parsed,
            expected_purpose=ATTESTATION_PURPOSE_UPDATE,
            expected_version=version,
        )
    except SignatureError as exc:
        print(f"Refusing to write a signature the launcher would reject: {exc}", file=sys.stderr)
        return 1

    out = args.out or args.target.with_name(args.target.name + ".sig")
    out.write_text(sidecar, encoding="utf-8")
    print(f"  signed  {args.target}")
    print(f"  wrote   {out}")
    print(f"  key     {used[:16]}…  (verified against the pinned set)")
    print(f"  version {version}  (bound inside the signature)")
    print("\nUpload BOTH files to the release. A release without its .sig will be")
    print("refused by every launcher that has this verification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
