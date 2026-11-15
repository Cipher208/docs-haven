"""PyPI package documentation indexing (card P2-4, FUTURE_PLAN item 11).

Stdlib only by design — deliberately NOT pip (uv venvs ship no pip) and NOT a
new dependency: urllib.request for HTTP, tarfile/zipfile for unpacking. The
download/extract layer mirrors fetcher.py's seam: ALL I/O lives here and
Storage.add_pypi stays pure (it takes already-extracted docs).

Scope (decided): LATEST RELEASE ONLY — `pypi://<name>` indexes the newest
release's sdist; version pins (`pypi://pkg==1.2`) are OUT. An sdist is taken
over a wheel because sdists carry README and docs/; wheels usually do not.

Collected docs: README* (any case, .md/.rst/.txt) anywhere in the source
tree, every .md/.rst/.txt under docs/, and top-level *.md/*.rst. Text is kept
nearly raw (no rst/markdown rendering); the title is the first heading line
(markdown '#'-heading or an rst underline heading) or, failing that, the
file name.

SECURITY — unpack safety: tar members go through tarfile's filter="data"
(PEP 706 — tar-slip '../', links and special files hard-rejected; a
POSIX-absolute member is sanitized inside the destination on Windows but
hard-rejected on Linux); the guarantee is that nothing ever lands outside
the scratch dir and an unsafe member is an Err; zip
members are validated manually (no '..', no absolute/drive paths) before
extraction since zipfile has no filter. Extraction happens only inside a
TemporaryDirectory. Residual, documented honestly: the DEcompressed size is
not capped — the artifact cap bounds the download, not the unpack.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from result import Err, Ok
from validation import _MAX_CONTENT_LENGTH, validate_http_url, validate_pypi_name

# Derived (NOT measured) limit: 4 x the repo's 10MB content precedent
# (validation._MAX_CONTENT_LENGTH, the kb_update cap) because an sdist is a
# container — a whole docs tree — not a single document. Documented as a
# derived limit in CHANGELOG.
_MAX_SDIST_BYTES = 4 * _MAX_CONTENT_LENGTH
_MAX_JSON_BYTES = _MAX_CONTENT_LENGTH  # the JSON metadata envelope, bounded by the same precedent
_TIMEOUT = 30  # download timeout in seconds — sdists approach the 40MB cap on slow links
_USER_AGENT = "DocsHaven"  # some hosts 403 requests without a User-Agent
_DOC_SUFFIXES = {".md", ".rst", ".txt"}
_TOP_LEVEL_SUFFIXES = {".md", ".rst"}  # top-level *.txt (requirements.txt etc.) is not documentation
_UNDERLINE_CHARS = set("=-~^\"'+*_#")


def _http_get(url: str, cap: int) -> Ok[bytes] | Err:
    """GET a URL and return the body, size-capped at cap bytes (Ok/Err)."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = response.read(cap + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return Err(error=f"not found (HTTP 404): {url}")
        return Err(error=f"HTTP {e.code} fetching {url}")
    except (urllib.error.URLError, OSError) as e:
        return Err(error=f"Fetch failed for {url}: {e}")
    if len(data) > cap:
        return Err(error=f"too large (max {cap} bytes)")
    return Ok(value=data)


def _member_error(name: str) -> str | None:
    """Reject traversal/absolute/drive-letter zip member names (zipfile has no
    extract filter — this IS the zip-side unpack-safety gate)."""
    if not name or name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
        return f"Unsafe path in sdist archive: {name}"
    if ".." in re.split(r"[\\/]", name):
        return f"Unsafe path in sdist archive: {name}"
    return None


_BAD_ARCHIVE_ERRORS = (
    tarfile.TarError,
    zipfile.BadZipFile,
    EOFError,
    OSError,
    TypeError,  # filter= missing on an unpatched 3.10
    RuntimeError,  # encrypted zip
)


def _unpack(archive: bytes, dest: Path) -> Err | None:
    """Unpack the sdist into dest; Err on a bad or malicious archive.

    Tar: filter="data" does the tar-slip rejection (OutsideDestinationError
    and friends, all tarfile.TarError) -> Err. Zip: members are validated
    first (no '..', no absolute paths) and a bad member is Err BEFORE
    anything is extracted. A corrupted-but-recognized archive is an Err too,
    never an exception: truncated tar.gz (EOFError), bad-CRC zip
    (BadZipFile), name-collision/extraction failures (OSError), encrypted
    zip (RuntimeError), filter= missing on an unpatched 3.10 (TypeError).
    """
    bio = io.BytesIO(archive)
    try:
        if tarfile.is_tarfile(bio):
            bio.seek(0)
            with tarfile.open(fileobj=bio, mode="r:*") as tar:
                tar.extractall(path=dest, filter="data")
            return None
        bio.seek(0)
        if zipfile.is_zipfile(bio):
            with zipfile.ZipFile(bio) as zf:
                for member in zf.namelist():
                    error = _member_error(member)
                    if error:
                        return Err(error=error)
                zf.extractall(path=dest)
            return None
    except _BAD_ARCHIVE_ERRORS as e:
        # One contract for every archive-machinery failure — including the
        # sniffing calls (a truncated gzip raises EOFError inside
        # is_tarfile). Corrupted-but-recognized archives are Err, never
        # exceptions (blind review B1).
        return Err(error=f"Bad or unsafe sdist archive: {e}")
    return Err(error="Unrecognized sdist archive (not a tar or zip)")


def _source_root(dest: Path) -> Path:
    """The sdist source tree root: sdists wrap everything in <name>-<version>/."""
    entries = list(dest.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest


def _collect_doc_files(root: Path) -> dict[str, Path]:
    """README* (any case, .md/.rst/.txt) anywhere + docs/**/*.{md,rst,txt} +
    top-level *.md/*.rst — keyed by sdist-relative path (deduped)."""
    docs: dict[str, Path] = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in _DOC_SUFFIXES:
            continue
        rel = f.relative_to(root).as_posix()
        if f.name.lower().startswith("readme") or rel.startswith("docs/") or ("/" not in rel and f.suffix.lower() in _TOP_LEVEL_SUFFIXES):
            docs[rel] = f
    return docs


def _doc_title(text: str, fallback: str) -> str:
    """First heading line (markdown '#'-heading or an rst underline heading);
    failing that the file name."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            return heading.group(1)
        stripped = line.strip()
        if stripped and i + 1 < len(lines):
            underline = lines[i + 1].strip()
            if underline and len(underline) >= len(stripped) and set(underline) <= _UNDERLINE_CHARS:
                return stripped
    return fallback


def fetch_pypi_docs(package_name: str) -> Ok[list[dict]] | Err:
    """Fetch a package's latest-release sdist and extract its documentation.

    Returns Ok([{"path": sdist-relative path, "title": ..., "text": ...}]).
    Errors: invalid package name (PEP 508), unknown package (404 "not
    found"), no sdist in the release, too-large artifact (the derived
    _MAX_SDIST_BYTES cap), bad or malicious archive, and HTTP failures.
    A package with no docs is Ok([]) — nothing to index is not an error.
    """
    name_error = validate_pypi_name(package_name)
    if name_error:
        return Err(error=name_error)
    name = package_name.strip()

    meta = _http_get(f"https://pypi.org/pypi/{name}/json", _MAX_JSON_BYTES)
    if isinstance(meta, Err):
        return Err(error=f"PyPI package lookup failed for {name}: {meta.error}")
    try:
        urls = json.loads(meta.value.decode("utf-8", errors="replace"))["urls"]
    except (ValueError, KeyError, TypeError):
        return Err(error=f"Unexpected PyPI JSON response for {name}")
    if not isinstance(urls, list):
        return Err(error=f"Unexpected PyPI JSON response for {name}")
    sdists = [u for u in urls if isinstance(u, dict) and u.get("packagetype") == "sdist"]
    if not sdists:
        return Err(error=f"No sdist in the latest release of {name} (sdists carry README/docs; wheels are not indexed)")
    artifact_url = str(sdists[0].get("url", ""))
    url_error = validate_http_url(artifact_url)  # the URL comes from JSON — still must be plain http(s)
    if url_error:
        return Err(error=f"Bad sdist URL for {name}: {url_error}")

    artifact = _http_get(artifact_url, _MAX_SDIST_BYTES)
    if isinstance(artifact, Err):
        return Err(error=f"Failed to download sdist for {name}: {artifact.error}")

    docs: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        unpack_error = _unpack(artifact.value, dest)
        if unpack_error:
            return unpack_error
        root = _source_root(dest)
        for rel, f in _collect_doc_files(root).items():
            text = f.read_text(encoding="utf-8", errors="replace")
            if not text.strip():
                continue  # an empty file is not documentation — skip, don't fail the package
            docs.append({"path": rel, "title": _doc_title(text, f.name), "text": text})
    return Ok(value=docs)
