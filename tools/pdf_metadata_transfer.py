#!/usr/bin/env python3
"""
pdf_metadata_transfer — copy *all* of one PDF's metadata onto another.

A PDF stores metadata in three independent places. To make a target file's
metadata truly indistinguishable from a source's, every one of them has to be
overwritten:

  1. The document information dictionary (the trailer's /Info): the classic
     fields — Title, Author, Subject, Keywords, Creator, Producer,
     CreationDate, ModDate — plus any custom keys an author added.
  2. The XMP metadata stream (/Metadata on the document catalog): an RDF/XML
     packet. Many readers (Acrobat, Preview, search indexers) trust this over
     /Info, so it must match byte-for-byte.
  3. The document /ID in the trailer: a two-element array of byte strings that
     acts as the file's fingerprint. Tools use it to tell two PDFs apart, so
     copying it is what makes the pair genuinely indistinguishable.

This tool transfers all three. Use it on documents you own or are authorized to
modify — e.g. preserving metadata when a PDF is re-rendered or re-exported.

Usage:
    # Overwrite target's metadata with source's, in place:
    python pdf_metadata_transfer.py --source ref.pdf --target out.pdf

    # Write to a new file instead of overwriting:
    python pdf_metadata_transfer.py -s ref.pdf -t out.pdf -o merged.pdf

    # Show what metadata a PDF carries:
    python pdf_metadata_transfer.py --inspect ref.pdf

    # Confirm two PDFs now carry identical metadata:
    python pdf_metadata_transfer.py --verify ref.pdf out.pdf
"""

from __future__ import annotations

import argparse
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
# Extraction helpers
# --------------------------------------------------------------------------- #
def _read_info(pdf: pikepdf.Pdf) -> dict[str, object]:
    """Return the raw /Info dictionary as plain Python keys -> values."""
    info = {}
    if pdf.trailer.get("/Info") is not None:
        for key, value in pdf.trailer.Info.items():
            info[str(key)] = value
    return info


def _read_xmp(pdf: pikepdf.Pdf) -> bytes | None:
    """Return the raw XMP /Metadata stream bytes, or None if absent."""
    meta = pdf.Root.get("/Metadata")
    if meta is None:
        return None
    return bytes(meta.read_bytes())


def _read_id(pdf: pikepdf.Pdf) -> list[bytes] | None:
    """Return the trailer /ID as a list of byte strings, or None if absent."""
    doc_id = pdf.trailer.get("/ID")
    if doc_id is None:
        return None
    return [bytes(part) for part in doc_id]


# --------------------------------------------------------------------------- #
# Transfer
# --------------------------------------------------------------------------- #
def transfer_metadata(source_path: Path, target_path: Path, output_path: Path) -> None:
    """Copy every piece of metadata from source onto target, saved to output."""
    # allow_overwriting_input lets us save back over target_path (in-place use).
    with pikepdf.open(source_path) as source, \
            pikepdf.open(target_path, allow_overwriting_input=True) as target:
        # 1. Document information dictionary (/Info) -------------------------
        # Copy the whole source dict across object spaces; this brings every
        # field (standard and custom) and its exact string bytes with it.
        if source.trailer.get("/Info") is not None:
            target.trailer.Info = target.copy_foreign(source.trailer.Info)
        elif "/Info" in target.trailer:
            del target.trailer.Info

        # 2. XMP metadata stream (/Metadata) --------------------------------
        # Copy the stream object itself so its bytes and /Type//Subtype match.
        if source.Root.get("/Metadata") is not None:
            target.Root.Metadata = target.copy_foreign(source.Root.Metadata)
        elif "/Metadata" in target.Root:
            del target.Root.Metadata

        # 3. Trailer /ID (the file fingerprint) -----------------------------
        # /ID entries are direct strings, so recreate them in the target. qpdf
        # will still regenerate the array on save, so the exact bytes are forced
        # in a post-save patch below; setting them here keeps a sane fallback.
        src_id = _read_id(source)
        if src_id is not None:
            target.trailer.ID = pikepdf.Array([String(part) for part in src_id])
        elif "/ID" in target.trailer:
            del target.trailer.ID

        # Save with object streams disabled so the trailer (and its /ID) is
        # written as plaintext we can patch. fix_metadata_version=False keeps us
        # from rewriting the XMP we just transplanted.
        target.save(
            output_path,
            fix_metadata_version=False,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
        )

    # qpdf recomputes /ID[1] on every write regardless of what we set, so force
    # the source's exact /ID into the written file's trailer.
    if src_id is not None:
        _patch_trailer_id(output_path, src_id)


def _patch_trailer_id(path: Path, doc_id: list[bytes]) -> None:
    """Rewrite the /ID array in a saved PDF's trailer to exactly doc_id."""
    data = path.read_bytes()
    replacement = b"/ID[" + b"".join(b"<" + part.hex().encode("ascii") + b">"
                                     for part in doc_id) + b"]"

    # Patch the /ID array inside the trailer dictionary (object streams were
    # disabled, so it is plaintext near the end of the file).
    pattern = re.compile(rb"/ID\s*\[\s*<[0-9A-Fa-f]*>\s*<[0-9A-Fa-f]*>\s*\]")
    trailer_at = data.rfind(b"trailer")
    if trailer_at != -1 and pattern.search(data, trailer_at):
        patched = pattern.sub(replacement, data[trailer_at:], count=1)
        data = data[:trailer_at] + patched
    else:
        # No traditional trailer match; patch the last /ID array anywhere.
        matches = list(pattern.finditer(data))
        if not matches:
            raise RuntimeError("could not locate /ID array to patch in output")
        last = matches[-1]
        data = data[:last.start()] + replacement + data[last.end():]

    path.write_bytes(data)


# --------------------------------------------------------------------------- #
# Inspection / verification
# --------------------------------------------------------------------------- #
def _fingerprint(pdf: pikepdf.Pdf) -> dict[str, object]:
    """A comparable snapshot of all metadata in a PDF."""
    info = {k: bytes(v) if isinstance(v, String) else str(v)
            for k, v in _read_info(pdf).items()}
    return {
        "info": dict(sorted(info.items())),
        "xmp": _read_xmp(pdf),
        "id": _read_id(pdf),
    }


def inspect(path: Path) -> None:
    with pikepdf.open(path) as pdf:
        fp = _fingerprint(pdf)
    print(f"# Metadata in {path}\n")

    print("## /Info dictionary")
    if fp["info"]:
        for key, value in fp["info"].items():
            shown = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
            print(f"  {key}: {shown}")
    else:
        print("  (none)")

    print("\n## XMP /Metadata stream")
    if fp["xmp"] is None:
        print("  (none)")
    else:
        print(f"  {len(fp['xmp'])} bytes")

    print("\n## Trailer /ID")
    if fp["id"] is None:
        print("  (none)")
    else:
        for i, part in enumerate(fp["id"]):
            print(f"  [{i}] {part.hex()}")


def verify(path_a: Path, path_b: Path) -> bool:
    with pikepdf.open(path_a) as a, pikepdf.open(path_b) as b:
        fp_a, fp_b = _fingerprint(a), _fingerprint(b)

    checks = {
        "/Info dictionary": fp_a["info"] == fp_b["info"],
        "XMP /Metadata stream": fp_a["xmp"] == fp_b["xmp"],
        "Trailer /ID": fp_a["id"] == fp_b["id"],
    }
    all_match = all(checks.values())

    print(f"Comparing metadata:\n  A: {path_a}\n  B: {path_b}\n")
    for label, ok in checks.items():
        print(f"  [{'MATCH' if ok else 'DIFF '}] {label}")
    print()
    print("Result: metadata is IDENTICAL" if all_match
          else "Result: metadata DIFFERS")
    return all_match


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf_metadata_transfer",
        description="Overwrite and transfer all of one PDF's metadata onto another.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-s", "--source", type=Path,
                   help="PDF whose metadata is the reference (read-only).")
    p.add_argument("-t", "--target", type=Path,
                   help="PDF whose metadata will be overwritten.")
    p.add_argument("-o", "--output", type=Path,
                   help="Where to write the result. Defaults to overwriting --target.")
    p.add_argument("--inspect", type=Path, metavar="PDF",
                   help="Print all metadata found in PDF and exit.")
    p.add_argument("--verify", type=Path, nargs=2, metavar=("PDF_A", "PDF_B"),
                   help="Check whether two PDFs carry identical metadata and exit.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.inspect:
        inspect(args.inspect)
        return 0

    if args.verify:
        return 0 if verify(args.verify[0], args.verify[1]) else 1

    if not args.source or not args.target:
        build_parser().error("either --source and --target, or --inspect, or --verify is required")

    for label, path in (("source", args.source), ("target", args.target)):
        if not path.is_file():
            sys.exit(f"error: {label} file not found: {path}")

    output = args.output or args.target
    transfer_metadata(args.source, args.target, output)

    print(f"Transferred metadata from {args.source} onto {output}")
    print("Verifying...\n")
    ok = verify(args.source, output)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
