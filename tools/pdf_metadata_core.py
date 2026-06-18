#!/usr/bin/env python3
"""
pdf_metadata_core — read, edit, transfer and verify all of a PDF's metadata.

Shared engine used by both the command-line tool (pdf_metadata_transfer.py) and
the web UI (pdf_metadata_ui.py).

A PDF stores metadata in three independent places, and all three must match for
two files to be indistinguishable at the metadata level:

  1. /Info dictionary (trailer): Title, Author, Subject, Keywords, Creator,
     Producer, CreationDate, ModDate, plus any custom keys.
  2. XMP /Metadata stream (catalog): an RDF/XML packet many readers trust over
     /Info.
  3. /ID (trailer): a two-element array of byte strings that fingerprints a file.

None of these operations re-render or rasterize page content. qpdf (via pikepdf)
only rewrites the PDF's object structure, so the original text layer — and its
selectability — is preserved exactly. This is *not* a "print to PDF": the output
keeps real, selectable text and does not gain any print-driver Producer/Creator
of our own (the only Producer/Creator present are the ones you set or transfer).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import pikepdf
except ImportError:  # pragma: no cover
    sys.exit(
        "This tool needs pikepdf. Install it with:\n"
        "    pip install -r tools/requirements.txt\n"
        "  (or:  pip install pikepdf)"
    )

from pikepdf import String


# --------------------------------------------------------------------------- #
# Low-level readers
# --------------------------------------------------------------------------- #
def read_info(pdf: pikepdf.Pdf) -> dict[str, bytes]:
    """Return the /Info dictionary as {key: raw bytes}."""
    info: dict[str, bytes] = {}
    if pdf.trailer.get("/Info") is not None:
        for key, value in pdf.trailer.Info.items():
            info[str(key)] = bytes(value) if isinstance(value, String) else \
                str(value).encode("utf-8", "replace")
    return info


def read_xmp(pdf: pikepdf.Pdf) -> bytes | None:
    """Return the raw XMP /Metadata stream bytes, or None if absent."""
    meta = pdf.Root.get("/Metadata")
    if meta is None:
        return None
    return bytes(meta.read_bytes())


def read_id(pdf: pikepdf.Pdf) -> list[bytes] | None:
    """Return the trailer /ID as a list of byte strings, or None if absent."""
    doc_id = pdf.trailer.get("/ID")
    if doc_id is None:
        return None
    return [bytes(part) for part in doc_id]


# Text-showing operators in a PDF content stream. Their presence (with a font
# resource) means the page carries real, selectable text rather than an image.
_TEXT_OPS = re.compile(rb"BT\b|\bTj\b|\bTJ\b")


def has_selectable_text(pdf: pikepdf.Pdf) -> bool:
    """Best-effort check: does any page carry a selectable text layer?"""
    for page in pdf.pages:
        resources = page.get("/Resources")
        has_font = resources is not None and "/Font" in resources
        if not has_font:
            continue
        try:
            content = page.obj.get("/Contents")
            if content is None:
                continue
            streams = content if isinstance(content, pikepdf.Array) else [content]
            for stream in streams:
                if _TEXT_OPS.search(bytes(stream.read_bytes())):
                    return True
        except Exception:
            continue
    return False


# --------------------------------------------------------------------------- #
# High-level snapshot (for display / editing)
# --------------------------------------------------------------------------- #
def get_metadata(path: str | Path) -> dict:
    """A complete, JSON-friendly snapshot of a PDF's metadata and size."""
    path = Path(path)
    with pikepdf.open(path) as pdf:
        info = {k: v.decode("utf-8", "replace") for k, v in read_info(pdf).items()}
        xmp = read_xmp(pdf)
        doc_id = read_id(pdf)
        text = has_selectable_text(pdf)
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "info": dict(sorted(info.items())),
        "xmp": None if xmp is None else xmp.decode("utf-8", "replace"),
        "xmp_bytes": None if xmp is None else len(xmp),
        "id": None if doc_id is None else [part.hex() for part in doc_id],
        "has_text": text,
    }


# --------------------------------------------------------------------------- #
# Writing edited / transferred metadata
# --------------------------------------------------------------------------- #
def apply_metadata(
    target_path: str | Path,
    output_path: str | Path,
    *,
    info: dict[str, str] | None,
    xmp: str | None,
    doc_id: list[bytes] | None,
    pad_to: int | None = None,
) -> dict:
    """Write the given metadata onto target_path, saving to output_path.

    - info: full replacement /Info ({key: value}); None leaves /Info untouched,
      {} removes it.
    - xmp:  full XMP text; None leaves the stream untouched, "" removes it.
    - doc_id: list of byte strings for /ID; None leaves it untouched,
      [] removes it.
    - pad_to: if set, pad the written file to exactly this many bytes.

    Returns the resulting metadata snapshot. Never re-renders page content, so
    text stays selectable.
    """
    target_path, output_path = Path(target_path), Path(output_path)

    with pikepdf.open(target_path, allow_overwriting_input=True) as pdf:
        if info is not None:
            if "/Info" in pdf.trailer:
                del pdf.trailer.Info
            if info:
                info_dict = pdf.make_indirect(pikepdf.Dictionary())
                for key, value in info.items():
                    key = key if key.startswith("/") else "/" + key
                    info_dict[key] = String(value)
                pdf.trailer.Info = info_dict

        if xmp is not None:
            if "/Metadata" in pdf.Root:
                del pdf.Root.Metadata
            if xmp != "":
                meta = pdf.make_stream(xmp.encode("utf-8"))
                meta[pikepdf.Name.Type] = pikepdf.Name.Metadata
                meta[pikepdf.Name.Subtype] = pikepdf.Name.XML
                pdf.Root.Metadata = meta

        if doc_id is not None:
            if doc_id:
                pdf.trailer.ID = pikepdf.Array([String(p) for p in doc_id])
            elif "/ID" in pdf.trailer:
                del pdf.trailer.ID

        # Object streams disabled => plaintext trailer we can patch for /ID;
        # fix_metadata_version=False leaves the XMP we wrote exactly as-is.
        pdf.save(
            output_path,
            fix_metadata_version=False,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
        )

    # qpdf regenerates /ID on every write, so force the exact bytes afterwards.
    if doc_id:
        patch_trailer_id(output_path, doc_id)

    if pad_to is not None:
        pad_file_to(output_path, pad_to)

    return get_metadata(output_path)


def transfer_metadata(
    source_path: str | Path,
    target_path: str | Path,
    output_path: str | Path,
    *,
    match_size: bool = False,
) -> dict:
    """Copy every piece of metadata from source onto target -> output.

    If match_size is True, also pad the output to the source file's byte size.
    """
    src = get_metadata(source_path)
    pad_to = Path(source_path).stat().st_size if match_size else None
    return apply_metadata(
        target_path,
        output_path,
        info=src["info"],
        xmp=src["xmp"],
        doc_id=None if src["id"] is None else [bytes.fromhex(h) for h in src["id"]],
        pad_to=pad_to,
    )


def patch_trailer_id(path: str | Path, doc_id: list[bytes]) -> None:
    """Rewrite the /ID array in a saved PDF's trailer to exactly doc_id."""
    path = Path(path)
    data = path.read_bytes()
    replacement = b"/ID[" + b"".join(b"<" + part.hex().encode("ascii") + b">"
                                     for part in doc_id) + b"]"

    pattern = re.compile(rb"/ID\s*\[\s*<[0-9A-Fa-f]*>\s*<[0-9A-Fa-f]*>\s*\]")
    trailer_at = data.rfind(b"trailer")
    if trailer_at != -1 and pattern.search(data, trailer_at):
        patched = pattern.sub(replacement, data[trailer_at:], count=1)
        data = data[:trailer_at] + patched
    else:
        matches = list(pattern.finditer(data))
        if not matches:
            raise RuntimeError("could not locate /ID array to patch in output")
        last = matches[-1]
        data = data[:last.start()] + replacement + data[last.end():]

    path.write_bytes(data)


def pad_file_to(path: str | Path, target_size: int) -> None:
    """Pad a PDF to exactly target_size bytes by appending a trailing comment.

    Padding is added after the final %%EOF as a PDF comment, which readers
    ignore — it does not touch page content, so text stays selectable. Shrinking
    is refused, since that would require altering real content.
    """
    path = Path(path)
    current = path.stat().st_size
    if target_size == current:
        return
    if target_size < current:
        raise ValueError(
            f"cannot pad down: file is already {current} bytes, "
            f"target is {target_size}. Reduce content or pick a larger size."
        )
    needed = target_size - current
    # Build exactly `needed` trailing bytes: a comment marker + filler newline.
    if needed == 1:
        pad = b" "
    else:
        pad = b"\n%" + b" " * (needed - 3) + b"\n"  # "\n" + "%" + fill + "\n"
    with path.open("ab") as fh:
        fh.write(pad)
    # Guard against off-by-one from the formatting above.
    final = path.stat().st_size
    if final != target_size:
        with path.open("ab") as fh:
            fh.write(b" " * (target_size - final))


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def compare(path_a: str | Path, path_b: str | Path) -> dict:
    """Compare two PDFs' metadata (and size). Returns a structured report."""
    a, b = get_metadata(path_a), get_metadata(path_b)
    checks = {
        "/Info dictionary": a["info"] == b["info"],
        "XMP /Metadata stream": a["xmp"] == b["xmp"],
        "Trailer /ID": a["id"] == b["id"],
        "File size (bytes)": a["size"] == b["size"],
    }
    return {
        "a": a,
        "b": b,
        "checks": checks,
        # Metadata identity does not require equal byte size, so report it apart.
        "metadata_identical": all(v for k, v in checks.items()
                                  if k != "File size (bytes)"),
        "byte_identical_size": checks["File size (bytes)"],
    }
