# pdf_metadata_transfer

A command-line tool that overwrites **all** of one PDF's metadata with
another's, so the two become indistinguishable at the metadata level.

A PDF stores metadata in three independent places, and a tool that only touches
one of them leaves the files distinguishable. This transfers all three:

| Where | What | Why it matters |
|-------|------|----------------|
| `/Info` dictionary (trailer) | Title, Author, Subject, Keywords, Creator, Producer, CreationDate, ModDate, and any custom keys | The classic "Document Properties" fields. |
| XMP `/Metadata` stream (catalog) | An RDF/XML packet | Acrobat, Preview, and search indexers often trust this over `/Info`. |
| `/ID` (trailer) | A two-element array of byte strings | The file's fingerprint; tools use it to tell two PDFs apart. |

Only one of these matching is not enough — the tool copies every one, byte for
byte, and verifies the result.

## Install

```bash
pip install -r tools/requirements.txt
```

## Use

```bash
# Overwrite target.pdf's metadata with reference.pdf's, in place:
python tools/pdf_metadata_transfer.py --source reference.pdf --target target.pdf

# Write the result to a new file, leaving the target untouched:
python tools/pdf_metadata_transfer.py -s reference.pdf -t target.pdf -o merged.pdf

# See every piece of metadata a PDF carries:
python tools/pdf_metadata_transfer.py --inspect reference.pdf

# Confirm two PDFs now carry identical metadata (exit code 0 = identical):
python tools/pdf_metadata_transfer.py --verify reference.pdf merged.pdf
```

After a transfer the tool automatically runs `--verify` and prints a per-field
`MATCH` / `DIFF` report.

## How the `/ID` is forced

qpdf (the engine behind `pikepdf`) regenerates the second `/ID` element on every
write, so simply setting it isn't enough. The tool saves with object streams
disabled — which leaves the trailer as plaintext — then patches the `/ID` array
in the written bytes to the source's exact value, and re-verifies.

## Scope

Intended for documents you own or are authorized to modify — for example,
preserving a document's metadata when it is re-rendered, re-exported, or rebuilt
by a pipeline. It changes only metadata; page content is left untouched.
