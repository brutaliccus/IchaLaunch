"""IchaLaunch — Ravencraft / Turtle-compatible WoW client launcher."""

from .core.tls import sanitize_tls_ca_env

__version__ = "1.4.6"
__app_name__ = "IchaLaunch"

# Before any HTTPS (catalog, addons, GitHub, updates): drop stale CA env paths.
sanitize_tls_ca_env()

