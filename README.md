# Bangla dataset collection

Prepared corpus packages live beside this file. The preparation utility uses Kala's corpus text rules and only Python's standard library.

## Packages

- `wikidata/`: Kala's existing Bangla Wikipedia train/held-out prepared corpus and token-count files, split into individual ZIP chunks.
- `bangla-newspaper/`: cleaned newspaper article records, in individual ZIP chunks.
- `bard/`: cleaned article records grouped under five category folders (`economy`, `entertainment`, `international`, `sports`, `state`), each split into its own ZIP chunks.

Each folder has an `ATTRIBUTION.txt` and a `manifest.json`. News articles keep available source URL, title, category, author, and date metadata. BARD keeps its category. Every JSONL chunk is its own ZIP file and stays below 100,000,000 bytes. Chunks contain UTF-8 JSONL and can be unzipped independently.

## Merge a chunk set into one usable JSONL file

From this folder, run:

```powershell
python .\prepare_bangla_datasets.py merge --input-dir .\bangla-newspaper --output .\bangla-newspaper-merged.jsonl.gz
```

For BARD, merge one category at a time, such as `--input-dir .\bard\economy --output .\bard-economy.jsonl.gz`; repeat for each category. Use `wikidata` for the Wikipedia corpus. The output remains gzip-compressed JSONL; use `--output .\name.jsonl` for an uncompressed JSONL file. Each line is one UTF-8 JSON record, with article prose in its `paragraphs` array.

## Extract and assemble for local use

`use-data/` is Git-ignored. The materializer extracts each ZIP chunk under `use-data/extracted/` and assembles files under `use-data/assembled/`. Wikipedia stays split into `train.jsonl.gz` and `heldout.jsonl.gz`; its token-count sidecars are extracted and copied into the assembled folder. The newspaper corpus is available both as `bangla-newspaper.jsonl.gz` and as readable, uncompressed UTF-8 `bangla-newspaper.jsonl`. BARD becomes one gzip-compressed file per category.

Run the full workflow from this folder:

```powershell
python .\materialize_datasets.py all
```

To run only one step or package, use `extract` or `assemble` and optionally `--package wikipedia`, `--package newspaper`, or `--package bard`. For example:

```powershell
python .\materialize_datasets.py all --package bard
```

The extraction and assembly script uses Python's standard library and can be rerun to refresh the working files from the ZIP chunks.

## Rebuild packages

```powershell
python .\prepare_bangla_datasets.py newspaper --source .\source-data\newspaper
python .\prepare_bangla_datasets.py bard --source .\source-data\bard
```

The default inputs and outputs are recorded in `prepare_bangla_datasets.py`; override them with `--source`, `--output`, and `--chunk-mib`.

## Source and reuse notes

The newspapers package credits the Kaggle dataset and preserves article-level source links where provided. The underlying news articles are attributed to their original publishers; check the Kaggle dataset terms and article rights before redistribution.

The BARD package credits its source repository. The repository does not include clear dataset redistribution terms in the information inspected for this preparation; check the source terms before redistribution. Local preparation here does not upload or publish these files.

