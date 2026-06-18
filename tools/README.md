# PDF Metadata Studio

Tools to view, **manually edit**, and transfer **all** of a PDF's metadata, so
two files become indistinguishable at the metadata level — with an optional
byte-size match. Comes as a point-and-click web UI and a command-line tool that
share one engine (`pdf_metadata_core.py`).

A PDF stores metadata in three independent places, and a tool that only touches
one of them leaves the files distinguishable. These tools cover all three:

| Where | What | Why it matters |
|-------|------|----------------|
| `/Info` dictionary (trailer) | Title, Author, Subject, Keywords, Creator, Producer, CreationDate, ModDate, and any custom keys | The classic "Document Properties" fields. |
| XMP `/Metadata` stream (catalog) | An RDF/XML packet | Acrobat, Preview, and search indexers often trust this over `/Info`. |
| `/ID` (trailer) | A two-element array of byte strings | The file's fingerprint; tools use it to tell two PDFs apart. |

## Selectable text — never a "print to PDF"

Both tools edit only the PDF's object structure via `pikepdf`/qpdf; they
**never re-render or rasterize the page**. The original text layer is preserved
exactly, so the output keeps real, selectable text and gains no print-driver
`Producer`/`Creator` of its own — the only such values present are the ones you
set or transfer. Every view shows a **"selectable text"** indicator so you can
confirm the layer survived.

## Install

```bash
pip install -r tools/requirements.txt
```

## The web UI

```bash
python tools/pdf_metadata_ui.py     # opens http://127.0.0.1:8765 in your browser
```

It lets you:

- **See every bit of metadata** — all `/Info` fields, the full XMP packet, and
  both `/ID` byte strings (as hex).
- **Edit anything manually** — change/add/remove any `/Info` key, rewrite the
  raw XMP, and edit either `/ID` element directly.
- **Match byte size** — the target's file size is shown, with a "pad to N
  bytes" control (and a one-click "use source size") so you can make the file
  size match a reference while the metadata stays identical.
- **Transfer all metadata** from a source PDF onto the target in one click,
  optionally padding to the source's exact byte size.
- **Verify** that two PDFs are identical (per-field, plus byte size).

The server binds only to `127.0.0.1`.

## The command line

```bash
# Overwrite target.pdf's metadata with reference.pdf's, in place:
python tools/pdf_metadata_transfer.py --source reference.pdf --target target.pdf

# Also pad the output to the source's exact byte size:
python tools/pdf_metadata_transfer.py -s reference.pdf -t target.pdf --match-size

# Write the result to a new file, leaving the target untouched:
python tools/pdf_metadata_transfer.py -s reference.pdf -t target.pdf -o merged.pdf

# See every piece of metadata (and the byte size + text indicator):
python tools/pdf_metadata_transfer.py --inspect reference.pdf

# Confirm two PDFs now carry identical metadata (exit code 0 = identical):
python tools/pdf_metadata_transfer.py --verify reference.pdf merged.pdf
```

After a transfer the CLI automatically verifies and prints a per-field
`MATCH` / `DIFF` report.

## How the `/ID` is forced

qpdf (the engine behind `pikepdf`) regenerates the second `/ID` element on every
write, so simply setting it isn't enough. The tools save with object streams
disabled — which leaves the trailer as plaintext — then patch the `/ID` array in
the written bytes to the exact value, and re-verify.

## How byte-size matching works

Metadata being identical does **not** require equal file size, so size is
reported separately. To equalize it, padding is appended as an ignored PDF
comment **after** the final `%%EOF`; readers skip it and page content is
untouched, so text stays selectable. Padding can only grow a file — shrinking
would require altering real content, so it is refused with a clear error.

## Scope

Intended for documents you own or are authorized to modify — for example,
preserving a document's metadata when it is re-rendered, re-exported, or rebuilt
by a pipeline. The tools change only metadata and (optionally) trailing padding;
page content is left untouched.
