"""Find a usable CA bundle for the bundled interpreter.

The Linux installers are frozen with PyInstaller on Ubuntu, so the bundled
OpenSSL keeps the build host's compiled-in trust paths (``/usr/lib/ssl``).
Those paths do not exist on Arch/CachyOS, NixOS or Alpine, OpenSSL therefore
trusts nothing, and every HTTPS download fails with
``CERTIFICATE_VERIFY_FAILED`` before the first byte is written.

:func:`configure` probes the common host bundle locations and exports
``SSL_CERT_FILE``, which OpenSSL and child processes (``uv``, ``npm``) read.
When a system genuinely has no trust store, verification is disabled for the
installer's own downloads instead of failing: the pinned SHA-256 checks still
prevent a tampered archive from being installed.
"""

from __future__ import annotations

import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

from .util import is_windows

# Checked in order; the first file that parses and holds at least one CA wins.
# The same bundle is reachable under different names per distribution, and an
# Arch box with only ``ca-certificates-utils`` installed has the file but no
# certificates in it, so existence alone is not enough.
CANDIDATE_FILES = (
    "/etc/ssl/cert.pem",  # Arch/CachyOS, Alpine
    "/etc/ssl/certs/ca-certificates.crt",  # Arch, Debian/Ubuntu
    "/etc/ssl/certs/ca-bundle.crt",  # Arch, older Debian
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora/RHEL
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora (extracted)
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/pki/tls/cert.pem",  # older RHEL
)

#: Set by ``--insecure-tls``; read by :func:`configure` so the flag also works
#: when the wizard (which parses its own arguments) runs the install.
INSECURE_ENV = "DRAMA_LLAMA_INSECURE_TLS"

_path: Path | None = None
_source = "unconfigured"
_configured = False
_insecure = False
_opener: urllib.request.OpenerDirector | None = None


def reset() -> None:
    """Forget the cached decision (tests, and ``--check-tls`` after env edits)."""
    global _path, _source, _configured, _insecure, _opener
    _path = None
    _source = "unconfigured"
    _configured = False
    _insecure = False
    _opener = None


def _ca_count(cafile: Path) -> int:
    """Number of CA certificates in ``cafile`` (0 when it is missing or unusable)."""
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(cafile))
    except (OSError, ssl.SSLError, ValueError):
        return 0
    try:
        return int(context.cert_store_stats().get("x509_ca", 0))
    except (AttributeError, ValueError):
        return 0


def _valid_file(value: str | os.PathLike[str] | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_file() and _ca_count(path) > 0:
        return path
    return None


def discover() -> tuple[Path | None, str]:
    """Return ``(bundle, description)`` for the first usable host trust store."""
    if is_windows():
        # ssl.load_default_certs() uses the Windows certificate store.
        return None, "windows certificate store"
    configured = _valid_file(os.environ.get("SSL_CERT_FILE"))
    if configured is not None:
        return configured, f"{configured} (SSL_CERT_FILE)"
    for candidate in CANDIDATE_FILES:
        path = Path(candidate)
        if not path.is_file():
            continue
        count = _ca_count(path)
        if count:
            return path, f"{candidate} ({count} CAs)"
    return None, "none"


def configure(*, insecure: bool | None = None) -> Path | None:
    """Resolve the trust store once and export it to OpenSSL and children.

    ``insecure=True`` (``--insecure-tls``, or :data:`INSECURE_ENV`) skips
    verification even when a bundle exists. When no bundle can be found at all,
    verification is disabled automatically: without trust anchors the only
    alternative is a hard failure before anything is installed.
    """
    global _path, _source, _configured, _insecure, _opener
    if _configured:
        return _path
    if insecure is None:
        insecure = os.environ.get(INSECURE_ENV, "").strip().lower() not in {
            "",
            "0",
            "false",
            "no",
        }
    path, source = discover()
    _configured = True
    _path = path
    _source = source
    _opener = None
    if is_windows() and not insecure:
        # Leave the OS store alone; ssl.create_default_context() uses it.
        _insecure = False
        return None
    if insecure:
        _insecure = True
        _source = "verification disabled (--insecure-tls)"
        return path
    if path is None:
        _insecure = True
        _source = "no CA bundle found (verification disabled)"
        return None
    os.environ["SSL_CERT_FILE"] = str(path)
    _insecure = False
    return path


def source() -> str:
    """Human-readable description of the trust store decision."""
    return _source


def verified() -> bool:
    """Whether downloads made through :func:`opener` verify certificates."""
    return not _insecure


def ssl_context() -> ssl.SSLContext:
    configure()
    if _insecure:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if _path is not None:
        return ssl.create_default_context(cafile=str(_path))
    return ssl.create_default_context()


def opener() -> urllib.request.OpenerDirector:
    """A urllib opener that uses the resolved trust store (proxies included)."""
    global _opener
    if _opener is None:
        _opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl_context())
        )
    return _opener


def describe() -> str:
    """Diagnostics for the install log and the ``--check-tls`` output."""
    lines = [f"openssl: {ssl.OPENSSL_VERSION}"]
    try:
        paths = ssl.get_default_verify_paths()
        lines.append(f"openssl default cafile: {paths.cafile or '(none)'}")
        lines.append(f"openssl default capath: {paths.capath or '(none)'}")
    except (AttributeError, OSError, ValueError):
        lines.append("openssl default paths: unavailable")
    lines.append(f"SSL_CERT_FILE={os.environ.get('SSL_CERT_FILE') or '(unset)'}")
    lines.append(f"SSL_CERT_DIR={os.environ.get('SSL_CERT_DIR') or '(unset)'}")
    for candidate in CANDIDATE_FILES:
        path = Path(candidate)
        detail = f"{_ca_count(path)} CAs" if path.is_file() else "missing"
        lines.append(f"  {candidate}: {detail}")
    if _configured:
        lines.append(f"selected: {_source}")
        lines.append("verification: " + ("disabled" if _insecure else "enabled"))
    else:
        lines.append("selected: not configured yet")
    return "\n".join(lines)


def check(url: str, *, timeout: float = 30.0) -> tuple[bool, str]:
    """Fetch ``url`` with the resolved trust store; return ``(ok, report)``."""
    configure()
    request = urllib.request.Request(
        url, headers={"User-Agent": "DramaLlama-Installer-TLS-check"}
    )
    lines = [describe()]
    try:
        with opener().open(request, timeout=timeout) as response:
            status = getattr(response, "status", "?")
        lines.append(f"{url}: HTTP {status}")
        return True, "\n".join(lines)
    except (urllib.error.URLError, OSError) as exc:
        lines.append(f"{url}: {exc}")
        return False, "\n".join(lines)
