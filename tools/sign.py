#!/usr/bin/env python3
"""Sign a release artefact or live catalog so the launcher will accept it.

Usage:
    python tools/sign.py --key keys/ichalaunch-key1.pem dist/IchaLaunch.exe
    python tools/sign.py ichalaunch/data/mods.json
    python tools/sign.py --interactive

Writes ``<target>.sig`` beside the file. Catalog JSON sidecars go on public
``brutaliccus/IchaLaunch`` next to the JSON. The launcher EXE sidecar goes on
the GitHub Release, not under ``ichalaunch/data/``.

Interactive sign + upload (prompts per file, Enter skips):

    python tools/sign_live.py
    python tools/sign.py --interactive

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
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_KEY_NAME = "ichalaunch-key1.pem"


def default_key_path() -> Path:
    """Maintainer key on this machine. Never a repo path, never CI."""
    if sys.platform == "win32":
        base = (os.environ.get("LOCALAPPDATA") or "").strip()
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "IchaLaunch" / "signing" / DEFAULT_KEY_NAME
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "IchaLaunch" / "signing" / DEFAULT_KEY_NAME


def sidecar_path_for(target: Path) -> Path:
    return target.with_name(target.name + ".sig")


def resolve_purpose_and_version(target: Path, version: str | None = None) -> tuple[str, str]:
    from ichalaunch import __version__
    from ichalaunch.core.signing import ATTESTATION_PURPOSE_CATALOG, purpose_for_signed_path

    purpose = purpose_for_signed_path(target)
    if version is not None and str(version).strip():
        return purpose, str(version).strip()
    if purpose == ATTESTATION_PURPOSE_CATALOG:
        return purpose, "catalog"
    return purpose, str(__version__).strip()


def load_private_key(key: Path, password: bytes | None = None, *, prompt: bool = True):
    """Unlock a PEM. Prompts once when the key is encrypted and no password was given."""
    from cryptography.hazmat.primitives import serialization

    pem = key.read_bytes()
    if password is not None:
        return serialization.load_pem_private_key(pem, password=password)
    try:
        return serialization.load_pem_private_key(pem, password=None)
    except TypeError:
        if not prompt:
            raise
        pw = getpass.getpass(f"password for {key.name}: ").encode()
        return serialization.load_pem_private_key(pem, password=pw)


def build_sidecar_text(payload: bytes, priv, *, purpose: str, version: str) -> str:
    """Build sidecar JSON from the exact payload bytes. Does not write or parse JSON files."""
    from cryptography.hazmat.primitives import serialization

    from ichalaunch.core.signing import Attestation

    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = base64.b64encode(pub_raw).decode()
    sig = priv.sign(payload)
    attestation = Attestation(
        purpose=purpose,
        version=version,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    attestation_sig = priv.sign(attestation.canonical())
    return (
        json.dumps(
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
        )
        + "\n"
    )


def verify_sidecar_text(payload: bytes, sidecar: str, *, purpose: str, version: str) -> str:
    """Return the pinned key id, or raise if the launcher would reject this sidecar."""
    from ichalaunch.core.signing import (
        Signature,
        SignatureError,
        signing_is_configured,
        verify_attestation,
        verify_bytes,
    )

    if not signing_is_configured():
        raise SignatureError(
            "This checkout pins no keys in ichalaunch/core/signing.py, so the "
            "signature cannot be checked and no launcher would accept it. "
            "Run tools/keygen.py and paste PINNED_KEYS in first."
        )
    parsed = Signature.parse(sidecar)
    used = verify_bytes(payload, parsed)
    verify_attestation(
        payload,
        parsed,
        expected_purpose=purpose,
        expected_version=version,
    )
    return used


@dataclass(frozen=True)
class SignResult:
    target: Path
    sidecar: Path
    purpose: str
    version: str
    key_id: str
    sidecar_text: str


def sign_file(
    target: Path,
    key: Path,
    *,
    password: bytes | None = None,
    version: str | None = None,
    out: Path | None = None,
    priv=None,
    write: bool = True,
    prompt_password: bool = True,
) -> SignResult:
    """Sign *target* bytes as-is (no JSON re-serialize). Re-verify, then write ``.sig``."""
    from ichalaunch.core.signing import SignatureError

    if not target.is_file():
        raise FileNotFoundError(f"No such file: {target}")
    if priv is None and not key.is_file():
        raise FileNotFoundError(f"No such key: {key}")

    payload = target.read_bytes()
    purpose, bound_version = resolve_purpose_and_version(target, version)
    if priv is None:
        try:
            priv = load_private_key(key, password, prompt=prompt_password)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Could not unlock the key: {exc}") from exc

    sidecar = build_sidecar_text(payload, priv, purpose=purpose, version=bound_version)
    try:
        used = verify_sidecar_text(payload, sidecar, purpose=purpose, version=bound_version)
    except SignatureError:
        raise

    dest = out or sidecar_path_for(target)
    if write:
        dest.write_bytes(sidecar.encode("utf-8"))
        on_disk = dest.read_bytes()
        if on_disk != sidecar.encode("utf-8"):
            raise SignatureError(f"Wrote {dest} but the bytes on disk do not match the verified sidecar.")
        if target.read_bytes() != payload:
            raise SignatureError(f"{target} changed on disk while signing.")
        verify_sidecar_text(payload, on_disk.decode("utf-8"), purpose=purpose, version=bound_version)

    return SignResult(
        target=target,
        sidecar=dest,
        purpose=purpose,
        version=bound_version,
        key_id=used,
        sidecar_text=sidecar,
    )


def _print_sign_result(result: SignResult) -> None:
    from ichalaunch.core.signing import ATTESTATION_PURPOSE_CATALOG

    print(f"  signed  {result.target}")
    print(f"  wrote   {result.sidecar}")
    print(f"  key     {result.key_id[:16]}...  (verified against the pinned set)")
    print(f"  version {result.version}  (bound inside the signature)")
    print(f"  purpose {result.purpose}")
    if result.purpose == ATTESTATION_PURPOSE_CATALOG:
        print("\nCommit this .sig beside the JSON on public brutaliccus/IchaLaunch")
        print("(ichalaunch/data/). Clients never read IchaLaunch-dev for live catalogs.")
        print("Or run: python tools/sign_live.py")
    else:
        print("\nUpload BOTH files to the release. A release without its .sig will be")
        print("refused by every launcher that has this verification.")
        print("  python tools/publish_public_release.py --tag vX.Y.Z --exe <exe> --sig <sig>")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--interactive" in argv:
        rest = [a for a in argv if a != "--interactive"]
        import importlib.util

        path = Path(__file__).with_name("sign_live.py")
        name = "ichalaunch_sign_live"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            print(f"Could not load {path}", file=sys.stderr)
            return 1
        mod = sys.modules.get(name)
        if mod is None:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        return int(mod.main(rest))

    from ichalaunch.core.signing import SignatureError

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", type=Path, help="file to sign")
    ap.add_argument(
        "--key",
        type=Path,
        default=None,
        help=f"private key PEM (default: {default_key_path()})",
    )
    ap.add_argument("--out", type=Path, default=None, help="signature path (default: <target>.sig)")
    ap.add_argument(
        "--version",
        default=None,
        help="version this artefact IS (default: catalog files -> 'catalog', "
        "else ichalaunch.__version__). Binding it inside the signature is what "
        "stops a genuine build being republished under a different tag.",
    )
    ap.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt per live catalog / EXE and upload (same as tools/sign_live.py)",
    )
    args = ap.parse_args(argv)

    key = args.key if args.key is not None else default_key_path()
    if not args.target.is_file():
        print(f"No such file: {args.target}", file=sys.stderr)
        return 1
    if not key.is_file():
        print(f"No such key: {key}", file=sys.stderr)
        return 1

    try:
        result = sign_file(args.target, key, version=args.version, out=args.out)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SignatureError as exc:
        print(f"Refusing to write a signature the launcher would reject: {exc}", file=sys.stderr)
        return 1

    _print_sign_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
