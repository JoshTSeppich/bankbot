"""Rewriting a Playwright trace.zip so nothing known survives inside it.

Owns: redact_trace. It reads a trace archive member by member, masks the
values the redactor knows, writes the home directory as `~`, and renames a
resource whose body changed to the hash of what that body now holds.

Does not own: what counts as a secret (bankbot.policy.Redactor) or when a
trace is worth keeping (EvidenceWriter.keep_trace). It rewrites; it never
decides.

Governed by ADR-0005 (policy model).
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Typing only. writer.py calls in here, so importing it back at run time
    # would be a cycle.
    from bankbot.evidence.writer import Redacting

IMAGE_SUFFIXES = (".jpeg", ".jpg", ".png")
RESOURCE_PREFIX = "resources/"
SHA1_HEX_LENGTH = 40
HOME_MARKER = b"~"
REWRITE_SUFFIX = ".redacting"


def redact_trace(path: Path, redactor: Redacting, home: Path) -> None:
    """Put the one file in a run directory that this codebase did not write through the redactor.

    Playwright writes trace.zip itself, and a typed password reaches it five
    ways at once: the fill parameters, Playwright's own log line, the DOM
    snapshot, the network postData and the POST-body resource. Rewriting the
    archive is the only way the evidence boundary covers all five.
    """
    home_path = str(home).encode()
    members: list[tuple[zipfile.ZipInfo, str, bytes]] = []
    renamed_resources: dict[bytes, bytes] = {}

    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            original = archive.read(info.filename)
            if _is_image(info.filename):
                members.append((info, info.filename, original))
                continue
            data = redactor.bytes(original).replace(home_path, HOME_MARKER)
            name = info.filename
            old_hash = _resource_hash(name)
            if data != original and old_hash is not None:
                # The old name is the sha1 of a body that held the credential,
                # which makes it an offline-guessable fingerprint of it. The
                # member gets named for what it holds now instead.
                new_hash = hashlib.sha1(data).hexdigest()
                renamed_resources[old_hash.encode()] = new_hash.encode()
                name = name.replace(old_hash, new_hash, 1)
            members.append((info, name, data))

    rewritten = path.with_name(path.name + REWRITE_SUFFIX)
    with zipfile.ZipFile(rewritten, "w") as archive:
        for info, name, data in members:
            body = data
            if not _is_image(name):
                for old, fresh in renamed_resources.items():
                    body = body.replace(old, fresh)
            archive.writestr(_same_shape(info, name), body)
    # Written beside the trace and swapped in, so a crash mid-write leaves the
    # original archive rather than a truncated one.
    rewritten.replace(path)


def _is_image(name: str) -> bool:
    return name.lower().endswith(IMAGE_SUFFIXES)


def _resource_hash(name: str) -> str | None:
    """The sha1 a resource member is named after, or None when the name is not one."""
    if not name.startswith(RESOURCE_PREFIX):
        return None
    stem, _, _ = name[len(RESOURCE_PREFIX) :].partition(".")
    if len(stem) != SHA1_HEX_LENGTH:
        return None
    return stem


def _same_shape(info: zipfile.ZipInfo, name: str) -> zipfile.ZipInfo:
    # Same timestamp, permissions and compression as the member it replaces, so
    # redacting an already redacted trace writes byte-identical output.
    kept = zipfile.ZipInfo(name, date_time=info.date_time)
    kept.compress_type = zipfile.ZIP_DEFLATED
    kept.external_attr = info.external_attr
    return kept
