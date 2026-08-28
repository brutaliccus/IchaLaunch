#!/usr/bin/env python3
"""Generate the launcher's update-signing keys. Run this ONCE, before the first
signed release.

Three keypairs are generated, not one. All three public keys get pinned in the
launcher; only key 1 is ever used to sign. Keys 2 and 3 go offline, in different
people's hands.

Why three, and why now
----------------------
A pinned key cannot revoke itself through the channel it controls. Pin only one
key, and if it leaks the recovery path is every player manually re-downloading
over an unauthenticated channel, which is exactly what an attacker impersonates.
With backups pinned, key 2 signs a release that names key 1 as revoked and
players recover automatically.

**Pinning spare keys costs nothing today and cannot be added afterwards.** Every
launcher already in the wild only trusts the keys it shipped with.

Usage:
    python tools/keygen.py --out ./keys

Then paste the printed PINNED_KEYS block into ichalaunch/core/signing.py.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import stat
import sys
from pathlib import Path

KEY_COUNT = 3
ROLES = {
    1: "ACTIVE SIGNER  - used for every release. Keep on the signing machine.",
    2: "BACKUP         - offline, held by a DIFFERENT person, on removable media.",
    3: "BACKUP         - offline, held by a THIRD person. Paper copy is fine.",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("keys"), help="directory for private keys")
    ap.add_argument("--force", action="store_true", help="overwrite existing key files")
    args = ap.parse_args()

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    existing = [p for p in (out / f"ichalaunch-key{i}.pem" for i in range(1, KEY_COUNT + 1)) if p.exists()]
    if existing and not args.force:
        print("Refusing to overwrite existing keys:", file=sys.stderr)
        for p in existing:
            print(f"  {p}", file=sys.stderr)
        print("\nRe-running would invalidate every signature made so far.", file=sys.stderr)
        print("Pass --force only if you are certain.", file=sys.stderr)
        return 1

    print("Each private key is encrypted with a password. Use a different one per key,")
    print("and store them in a password manager. Losing a password loses that key.\n")

    publics: list[str] = []
    for i in range(1, KEY_COUNT + 1):
        print(f"--- key {i}: {ROLES[i]}")
        while True:
            pw = getpass.getpass(f"    password for key {i}: ")
            if len(pw) < 12:
                print("    too short; use at least 12 characters.")
                continue
            if pw == getpass.getpass(f"    confirm password for key {i}: "):
                break
            print("    passwords did not match.")

        priv = Ed25519PrivateKey.generate()
        pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(pw.encode()),
        )
        path = out / f"ichalaunch-key{i}.pem"
        path.write_bytes(pem)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass

        raw = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        publics.append(base64.b64encode(raw).decode())
        print(f"    wrote {path}\n")

    print("=" * 72)
    print("Paste this into ichalaunch/core/signing.py, replacing PINNED_KEYS:\n")
    print("PINNED_KEYS: tuple[str, ...] = (")
    for i, pub in enumerate(publics, start=1):
        print(f'    # key {i} - {ROLES[i].split("-")[0].strip()}')
        print(f'    "{pub}",')
    print(")")
    print("=" * 72)
    print("\nAlso publish these fingerprints where players can compare them:")
    print("the repository README, the server's website, and a pinned message.")
    print("A key swapped in the repo alone should be visible somewhere else.\n")
    print("NOW, before you forget:")
    print(f"  - move {out}/ichalaunch-key2.pem and key3.pem OFF this machine")
    print("  - give key 2 to someone else, on removable media")
    print("  - never put any of these in CI secrets, a repo, or a cloud drive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
