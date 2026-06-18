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

This tool transfers all three. It never re-renders the page, so the output keeps
its original, selectable text (it is not a "print to PDF"). Use it on documents
you own or are authorized to modify.

For a point-and-click version with full manual editing, run the web UI:

    python pdf_metadata_ui.py

Usage (CLI):
    # Overwrite target's metadata with source's, in place:
    python pdf_metadata_transfer.py --source ref.pdf --target out.pdf

    # Also pad the output to the source's exact byte size:
    python pdf_metadata_transfer.py -s ref.pdf -t out.pdf --match-size

    # Write to a new file instead of overwriting:
    python pdf_metadata_transfer.py -s ref.pdf -t out.pdf -o merged.pdf

    # Show every bit of metadata (and byte size) a PDF carries:
    python pdf_metadata_transfer.py --inspect ref.pdf

    # Confirm two PDFs now carry identical metadata:
    python pdf_metadata_transfer.py --verify ref.pdf out.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pdf_metadata_core as core


def inspect(path: Path) -> None:
    md = core.get_metadata(path)
    print(f"# Metadata in {path}\n")
    print(f"File size: {md['size']} bytes")
    print(f"Selectable text layer: {'yes' if md['has_text'] else 'no'}\n")

    print("## /Info dictionary")
    if md["info"]:
        for key, value in md["info"].items():
            print(f"  {key}: {value}")
    else:
        print("  (none)")

    print("\n## XMP /Metadata stream")
    print("  (none)" if md["xmp"] is None else f"  {md['xmp_bytes']} bytes")

    print("\n## Trailer /ID")
    if md["id"] is None:
        print("  (none)")
    else:
        for i, part in enumerate(md["id"]):
            print(f"  [{i}] {part}")


def verify(path_a: Path, path_b: Path) -> bool:
    report = core.compare(path_a, path_b)
    print(f"Comparing:\n  A: {path_a}\n  B: {path_b}\n")
    for label, ok in report["checks"].items():
        print(f"  [{'MATCH' if ok else 'DIFF '}] {label}")
    print(f"\n  A size: {report['a']['size']} bytes"
          f"   B size: {report['b']['size']} bytes")
    print()
    print("Result: metadata is IDENTICAL" if report["metadata_identical"]
          else "Result: metadata DIFFERS")
    if report["metadata_identical"] and not report["byte_identical_size"]:
        print("Note: metadata matches but byte sizes differ — use --match-size "
              "or the UI's pad control to equalize them.")
    return report["metadata_identical"]


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
    p.add_argument("--match-size", action="store_true",
                   help="Also pad the output to the source file's exact byte size.")
    p.add_argument("--inspect", type=Path, metavar="PDF",
                   help="Print all metadata (and byte size) found in PDF and exit.")
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
        build_parser().error(
            "either --source and --target, or --inspect, or --verify is required")

    for label, path in (("source", args.source), ("target", args.target)):
        if not path.is_file():
            sys.exit(f"error: {label} file not found: {path}")

    output = args.output or args.target
    core.transfer_metadata(args.source, args.target, output,
                           match_size=args.match_size)

    print(f"Transferred metadata from {args.source} onto {output}")
    print("Verifying...\n")
    ok = verify(args.source, output)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
