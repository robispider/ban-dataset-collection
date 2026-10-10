#!/usr/bin/env python3
"""Prepare attributed Bangla corpora using Kala's bnwiki-prose-v1 and bangla-dh-text-v1 rules.

Standard library only. Outputs category or corpus JSONL chunks as individual ZIP files below 100 MB.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import unicodedata
import zipfile
import gzip
import codecs
from types import SimpleNamespace
from datetime import datetime, timezone

WIKI_SOURCE = Path(os.environ.get("KALA_WIKI_SOURCE", "source-data/wikidata"))
NEWSPAPER_SOURCE = Path(os.environ.get("BANGLA_NEWSPAPER_SOURCE", "source-data/newspaper"))
BARD_SOURCE = Path(os.environ.get("BARD_SOURCE", "source-data/bard"))
COLLECTION = Path(__file__).resolve().parent

# Exact text normalization mappings from Kala's bangla-dh-text-v1.
CHARACTER_MAP = {
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "‐": "-", "‑": "-", "৷": "।",
}
REMOVED = "­​‎‏⁠﻿‪‫‬‭‮"
SPACES = "\t               　"
_NORMALIZATION_TABLE = str.maketrans({**CHARACTER_MAP, **{c: None for c in REMOVED}, **{c: " " for c in SPACES}})
_SPACE_RUN = re.compile(" {2,}")

# Text extraction and paragraph quality rules from Kala's bnwiki-prose-v1.
_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.S)
_REF_SELF = re.compile(r"<ref\b[^>]*/\s*>", re.I)
_REF = re.compile(r"<ref\b[^>]*>.*?(?:</ref\s*>|$)", re.I | re.S)
_BLOCK = re.compile(r"<(math|chem|ce|gallery|timeline|syntaxhighlight|source|pre|code|score|graph|mapframe|"
                    r"maplink|imagemap|templatedata|templatestyles|references|hiero|inputbox|categorytree|"
                    r"charinsert|indicator|table)\b[^>]*?(?:/>|>.*?(?:</\1\s*>|$))", re.I | re.S)
_PARAM = re.compile(r"\{\{\{[^{}]*\}\}\}")
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_WIKITEXT_TABLE = re.compile(r"\{\|(?:(?!\{\|)[^\0])*?\|\}")
_LINK = re.compile(r"\[\[([^\[\]|]*)(?:\|([^\[\]]*))?\]\]")
_EXT_LINK = re.compile(r"\[(?:(?:https?|ftp):)?//[^\s\]]*\s*([^\]]*)\]")
_URL = re.compile(r"(?:https?|ftp)://\S+")
_TAG = re.compile(r"</?[a-zA-Z][^<>]*>")
_QUOTES = re.compile(r"'{2,}")
_MAGIC = re.compile(r"__[A-Z]+__")
_EMPTY_BRACKETS = re.compile(r"\(\s*[,;:।]?\s*\)")
_OPEN_GAP = re.compile(r"\(\s*[,;]\s*")
_SPACE_BEFORE = re.compile(r"\s+([,;:।?!)])")
_SPACE_AFTER_OPEN = re.compile(r"\(\s+")
_SPACES_TEXT = re.compile(r"[ \t]{2,}")
_SKIP_PREFIXES = ("=", "*", "#", ":", ";", "|", "!", "{", "}", "----", "<")
_MARKUP_RESIDUE = ("{{", "}}", "[[", "]]", "{|", "|}", "|", "<", ">", "{", "}", "&lt;", "&gt;")
_DROPPED_NAMES = {"file", "image", "media", "category", "চিত্র", "ফাইল", "মিডিয়া", "বিষয়শ্রেণী",
                  "উইকিপিডিয়া", "wikipedia", "wp", "টেমপ্লেট", "template", "special", "বিশেষ",
                  "ব্যবহারকারী", "user", "পোর্টাল", "portal", "সাহায্য", "help", "মডিউল", "module"}
_INTERWIKI = re.compile(r"^:?[a-z]{2,3}(?:-[a-z]+)*:")


def normalize_line(line: str) -> str:
    text = unicodedata.normalize("NFC", line).translate(_NORMALIZATION_TABLE)
    text = unicodedata.normalize("NFC", text)
    return _SPACE_RUN.sub(" ", text).strip()


def replace_link(match: re.Match) -> str:
    target, label = match.group(1).strip(), match.group(2)
    lowered = unicodedata.normalize("NFC", target.lstrip(":").lower())
    prefix = lowered.split(":", 1)[0].strip() if ":" in lowered else None
    if prefix is not None and (prefix in _DROPPED_NAMES or _INTERWIKI.match(lowered)):
        return "" if not target.startswith(":") or label is None else label
    if label is None or not label.strip():
        return target.split("#", 1)[0]
    return label


def repeat_sub(pattern: re.Pattern, text: str, replacement="") -> str:
    for _ in range(20):
        text, n = pattern.subn(replacement, text)
        if not n:
            break
    return text


def strip_markup(text: str) -> str:
    text = _COMMENT.sub("", text)
    text = _REF_SELF.sub("", text)
    text = _REF.sub("", text)
    text = _BLOCK.sub("", text)
    text = repeat_sub(_PARAM, text)
    text = repeat_sub(_TEMPLATE, text)
    text = repeat_sub(_WIKITEXT_TABLE, text)
    text = repeat_sub(_LINK, text, replace_link)
    text = _EXT_LINK.sub(lambda m: m.group(1), text)
    text = _URL.sub("", text)
    text = _TAG.sub("", text)
    text = _QUOTES.sub("", text)
    text = _MAGIC.sub("", text)
    return text


def tidy(line: str) -> str:
    line = _EMPTY_BRACKETS.sub("", line)
    line = _OPEN_GAP.sub("(", line)
    line = _SPACE_AFTER_OPEN.sub("(", line)
    line = _SPACE_BEFORE.sub(r"\1", line)
    return _SPACES_TEXT.sub(" ", line).strip()


def bengali_share(line: str) -> float:
    letters = bengali = 0
    for ch in line:
        if ch.isalpha():
            letters += 1
            if "\u0980" <= ch <= "\u09ff":
                bengali += 1
    return bengali / letters if letters else 0.0


def clean_paragraphs(text: str) -> list[str]:
    text = html.unescape(strip_markup(text))
    output = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith(_SKIP_PREFIXES):
            continue
        if any(mark in line for mark in _MARKUP_RESIDUE):
            continue
        line = tidy(line)
        if len(line.split()) < 3 or bengali_share(line) < 0.75:
            continue
        line = normalize_line(line)
        if line:
            output.append(line)
    return output


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def iter_json_array(path: Path):
    """Incrementally decode a top-level JSON array without loading the 4.6 GB source."""
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8-sig") as f:
        buf = ""
        pos = 0
        eof = False
        started = False
        first = True
        while True:
            if pos > (1 << 20):
                buf = buf[pos:]
                pos = 0
            while pos >= len(buf) and not eof:
                part = f.read(1 << 20)
                if part:
                    buf += part
                else:
                    eof = True
            while pos < len(buf) and buf[pos].isspace():
                pos += 1
            if not started:
                if pos >= len(buf):
                    if eof:
                        raise ValueError(f"{path}: empty JSON")
                    continue
                if buf[pos] != "[":
                    raise ValueError(f"{path}: expected a JSON array at top level")
                pos += 1
                started = True
                continue
            while pos < len(buf) and (buf[pos].isspace() or buf[pos] == ","):
                pos += 1
            if pos < len(buf) and buf[pos] == "]":
                return
            if pos >= len(buf):
                if eof:
                    raise ValueError(f"{path}: truncated JSON array")
                continue
            try:
                value, end = decoder.raw_decode(buf, pos)
            except json.JSONDecodeError:
                if eof:
                    raise
                part = f.read(1 << 20)
                if part:
                    buf += part
                else:
                    eof = True
                continue
            if not isinstance(value, dict):
                raise ValueError(f"{path}: expected object records in array")
            yield value
            pos = end
            first = False


class ChunkWriter:
    def __init__(self, output: Path, name: str, chunk_bytes: int):
        self.output, self.name, self.chunk_bytes = output, name, chunk_bytes
        self.records = 0
        self.documents = 0
        self.paragraphs = 0
        self.characters = 0
        self.duplicates = 0
        self.empty = 0
        self.seen: set[bytes] = set()
        self.shards: list[dict] = []
        self.stream = None
        self.uncompressed = 0
        self.shard_records = 0
        self._open_shard()

    def _open_shard(self):
        self.raw_path = self.output / f"{self.name}-{len(self.shards)+1:05d}.jsonl"
        self.stream = self.raw_path.open("w", encoding="utf-8", newline="\n")
        self.uncompressed = 0
        self.shard_records = 0

    def add(self, record: dict):
        kept = []
        for paragraph in record.pop("_paragraphs", []):
            key = hashlib.blake2b(paragraph.encode("utf-8"), digest_size=16).digest()
            if key in self.seen:
                self.duplicates += 1
            else:
                self.seen.add(key)
                kept.append(paragraph)
        if not kept:
            self.empty += 1
            return
        record["paragraphs"] = kept
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        size = len(line.encode("utf-8"))
        if self.shard_records and self.uncompressed + size > self.chunk_bytes:
            self._close_shard()
            self._open_shard()
        self.stream.write(line)
        self.uncompressed += size
        self.shard_records += 1
        self.documents += 1
        self.paragraphs += len(kept)
        self.characters += sum(map(len, kept))

    def _close_shard(self):
        self.stream.close()
        archive_path = self.raw_path.with_suffix(".zip")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as archive:
            archive.write(self.raw_path, arcname=self.raw_path.name)
        self.raw_path.unlink()
        size = archive_path.stat().st_size
        if size >= 100_000_000:
            archive_path.unlink()
            raise ValueError(f"Chunk exceeds 100 MB: {archive_path} ({size} bytes)")
        self.shards.append({
            "path": archive_path.name,
            "inner_path": self.raw_path.name,
            "documents": self.shard_records,
            "uncompressed_bytes": self.uncompressed,
            "compressed_bytes": size,
            "sha256": sha256_file(archive_path),
        })

    def close(self):
        if self.stream is not None:
            self._close_shard()
            self.stream = None


def metadata(record: dict, doc_id: str, source: str, category: str | None = None) -> dict:
    out = {"id": doc_id, "source": source}
    for key in ("title", "url", "category", "category_bn", "author", "published_date"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = normalize_line(value)[:2000]
    if category and "category" not in out:
        out["category"] = category
    return out


def process_newspaper(source: Path, output: Path, chunk_bytes: int):
    output.mkdir(parents=True, exist_ok=True)
    writer = ChunkWriter(output, "newspaper", chunk_bytes)
    input_files = sorted(source.rglob("*.json"))
    for file in input_files:
        i = 0
        for record in iter_json_array(file):
            i += 1
            writer.records += 1
            content = record.get("content") or record.get("article") or record.get("text") or ""
            paragraphs = clean_paragraphs(str(content))
            item = metadata(record, f"{file.relative_to(source).as_posix()}#{i}", file.relative_to(source).as_posix())
            item["_paragraphs"] = paragraphs
            writer.add(item)
            if i % 50000 == 0:
                print(f"{file.name}: read {i:,} records; kept {writer.documents:,}", flush=True)
    writer.close()
    return writer, [{"path": str(p.relative_to(source)), "bytes": p.stat().st_size, "sha256": sha256_file(p)}
                    for p in input_files]


def process_bard(source: Path, output: Path, chunk_bytes: int):
    output.mkdir(parents=True, exist_ok=True)
    inputs = []
    seen = set()
    category_counts = {}
    chunks = []
    totals = SimpleNamespace(records=0, documents=0, paragraphs=0, characters=0, duplicates=0, empty=0)
    total_read = 0
    for category_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        category = category_dir.name
        category_output = output / category
        category_output.mkdir(parents=True, exist_ok=True)
        writer = ChunkWriter(category_output, category, chunk_bytes)
        writer.seen = seen
        category_read = 0
        for folder, dirs, names in os.walk(category_dir):
            dirs.sort()
            for name in sorted(names):
                if not name.lower().endswith(".txt"):
                    continue
                file = Path(folder) / name
                relative = file.relative_to(source).as_posix()
                raw = file.read_bytes()
                text = raw.decode("utf-8-sig", errors="replace")
                item = {"id": file.relative_to(source).with_suffix("").as_posix(),
                        "source": relative, "category": category}
                item["_paragraphs"] = clean_paragraphs(text)
                writer.add(item)
                writer.records += 1
                inputs.append({"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                category_read += 1
                total_read += 1
                if total_read % 50000 == 0:
                    print(f"BARD: read {total_read:,} files; current category {category}; kept {totals.documents + writer.documents:,}", flush=True)
        writer.close()
        category_counts[category] = {
            "records_read": writer.records,
            "documents_kept": writer.documents,
            "documents_empty_or_filtered": writer.empty,
            "paragraphs_kept": writer.paragraphs,
            "characters_kept": writer.characters,
            "duplicate_paragraphs_removed": writer.duplicates,
            "chunks": len(writer.shards),
        }
        chunks.extend({**chunk, "category": category, "path": f"{category}/{chunk['path']}"}
                      for chunk in writer.shards)
        for field in ("records", "documents", "paragraphs", "characters", "duplicates", "empty"):
            setattr(totals, field, getattr(totals, field) + getattr(writer, field))
        print(f"BARD {category}: read {category_read:,}; kept {writer.documents:,} in {len(writer.shards)} ZIP chunks", flush=True)
    totals.shards = chunks
    totals.category_counts = category_counts
    return totals, inputs


def write_package(output: Path, name: str, attribution: str, writer: ChunkWriter, inputs: list[dict], params: dict):
    readme = output / "ATTRIBUTION.txt"
    readme.write_text(attribution.strip() + "\n\nProcessing:\n"
        "- Kala bangla-dh-text-v1 normalization (NFC, punctuation mappings, invisible formatting removal, whitespace collapse).\n"
        "- Kala bnwiki-prose-v1 text cleanup (markup stripping, paragraphs of at least 3 tokens, at least 75% Bengali letters).\n"
        "- Exact paragraph deduplication in input order; first occurrence retained.\n"
        "- UTF-8 JSONL chunks are individually compressed as ZIP archives, each below 100,000,000 bytes.\n"
        "- See manifest.json for inputs, output hashes, counts and parameters.\n", encoding="utf-8")
    manifest = {
        "dataset": name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "processing": {"normalization": "bangla-dh-text-v1", "cleanup": "bnwiki-prose-v1",
                       "deduplication": "exact paragraph global, first occurrence kept",
                       "record_format": "UTF-8 JSON Lines; each record contains a paragraphs array",
                       **params},
        "input_files": inputs,
        "records_read": writer.records,
        "documents_kept": writer.documents,
        "documents_empty_or_filtered": writer.empty,
        "paragraphs_kept": writer.paragraphs,
        "duplicate_paragraphs_removed": writer.duplicates,
        "characters_kept": writer.characters,
        "chunks": writer.shards,
        "category_counts": getattr(writer, "category_counts", None),
        "attribution_file": "ATTRIBUTION.txt",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {writer.documents:,} documents in {len(writer.shards)} ZIP chunks: {output}")
    print("Each ZIP chunk is below 100,000,000 bytes.")


def split_jsonl_gzip(source: Path, output: Path, prefix: str, chunk_bytes: int):
    """Split a Kala gzip JSONL split on complete lines into independent <100 MB ZIP files."""
    output.mkdir(parents=True, exist_ok=True)
    index = 0
    raw_path = None
    stream = None
    raw_size = records = 0

    def finish():
        nonlocal raw_path, stream, raw_size, records
        if stream is None:
            return
        stream.close()
        archive_path = raw_path.with_suffix(".zip")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as archive:
            archive.write(raw_path, arcname=raw_path.name)
        raw_path.unlink()
        if archive_path.stat().st_size >= 100_000_000:
            archive_path.unlink()
            raise ValueError(f"Chunk exceeds 100 MB: {archive_path}")
        print(f"  {archive_path.name}: {records:,} records, {raw_size:,} bytes")
        raw_path = stream = None
        raw_size = records = 0

    with gzip.open(source, "rt", encoding="utf-8") as src:
        for line in src:
            size = len(line.encode("utf-8"))
            if stream is None or (records and raw_size + size > chunk_bytes):
                finish()
                index += 1
                raw_path = output / f"{prefix}-{index:05d}.jsonl"
                stream = raw_path.open("w", encoding="utf-8", newline="\n")
            stream.write(line)
            raw_size += size
            records += 1
    finish()


def prepare_wikipedia(source: Path, output: Path):
    required = [
        "bnwiki-train.jsonl.gz", "bnwiki-heldout.jsonl.gz",
        "bnwiki-train.tokens.tsv.gz", "bnwiki-heldout.tokens.tsv.gz",
    ]
    missing = [n for n in required if not (source / n).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing Kala corpus files in {source}: {', '.join(missing)}")
    output.mkdir(parents=True, exist_ok=True)
    for name in required:
        if name.endswith(".jsonl.gz"):
            split_jsonl_gzip(source / name, output, name[:-len(".jsonl.gz")], 64 * 1024 * 1024)
        else:
            # Token-frequency sidecars are already compressed and below the per-file limit.
            archive_path = output / (name[:-3] + ".zip")
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6, allowZip64=True) as archive:
                archive.write(source / name, arcname=name)
            if archive_path.stat().st_size >= 100_000_000:
                raise ValueError(f"Chunk exceeds 100 MB: {archive_path}")
    (output / "ATTRIBUTION.txt").write_text(
        "Bangla Wikipedia (bnwiki), prepared by the Kala Bangla DH research workflow.\n"
        "Source/history: https://bn.wikipedia.org/\n"
        "Wikimedia dump archive: https://dumps.wikimedia.org/bnwiki/20261001/\n"
        "Wikipedia text is available under the Creative Commons Attribution-ShareAlike license; "
        "see https://en.wikipedia.org/wiki/Wikipedia:Copyrights and the linked source history.\n\n"
        "Preparation: Kala bnwiki-prose-v1 and bangla-dh-text-v1; paragraph deduplication; "
        "90/10 document-level train/heldout split. The JSONL gzip files contain prepared paragraphs; "
        "the token TSV gzip files contain derived counts. See Kala colemak-bangla results/phase2/REPORT.md.\n", encoding="utf-8")
    chunk_files = sorted(p for p in output.glob("*.zip"))
    manifest = {"dataset":"bnwiki", "source_corpus_directory":str(source),
        "files":[{"path":p.name,"bytes":p.stat().st_size,"sha256":sha256_file(p)} for p in chunk_files],
        "attribution_file":"ATTRIBUTION.txt"}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(f"Copied prepared Wikipedia corpus into {len(chunk_files)} ZIP chunks in {output}")


def merge_chunks(input_dir: Path, output: Path):
    chunks = sorted(input_dir.glob("*.zip"))
    json_chunks = []
    for chunk in chunks:
        with zipfile.ZipFile(chunk) as archive:
            names = [n for n in archive.namelist() if n.endswith(".jsonl")]
            if names:
                json_chunks.extend((chunk, name) for name in names)
    if not json_chunks:
        raise FileNotFoundError(f"No JSONL ZIP chunks in {input_dir}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    try:
        opener = gzip.open if output.name.endswith(".gz") else open
        with opener(temp, "wt", encoding="utf-8", newline="\n") as dst:
            for chunk, member in json_chunks:
                with zipfile.ZipFile(chunk) as archive, archive.open(member) as raw, \
                        __import__("io").TextIOWrapper(raw, encoding="utf-8") as src:
                    shutil.copyfileobj(src, dst, length=1 << 20)
        temp.replace(output)
    finally:
        if temp.exists():
            temp.unlink()
    print(f"Merged {len(json_chunks)} JSONL ZIP chunks into {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-mib", type=int, default=64, help="Maximum uncompressed bytes per JSONL chunk (default 64 MiB)")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("wikipedia", "newspaper", "bard"):
        p = sub.add_parser(command)
        p.add_argument("--source", type=Path, default={"wikipedia":WIKI_SOURCE,"newspaper":NEWSPAPER_SOURCE,"bard":BARD_SOURCE}[command])
        p.add_argument("--output", type=Path, default=COLLECTION / {"wikipedia":"wikidata","newspaper":"bangla-newspaper","bard":"bard"}[command])
    m = sub.add_parser("merge")
    m.add_argument("--input-dir", type=Path, required=True)
    m.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "merge":
        merge_chunks(args.input_dir, args.output)
        return
    if args.chunk_mib < 1:
        raise ValueError("--chunk-mib must be at least 1")
    if args.command == "wikipedia":
        prepare_wikipedia(args.source, args.output)
    else:
        writer, inputs = (process_newspaper if args.command == "newspaper" else process_bard)(
            args.source, args.output, args.chunk_mib * 1024 * 1024)
        if args.command == "newspaper":
            attribution = """Bangla Newspaper Dataset by Kaggle user furcifer.
Source: https://www.kaggle.com/datasets/furcifer/bangla-newspaper-dataset?resource=download
The input folder contains data.json and data_v2.json. Their article source links and available metadata are preserved in each output record.
"""
        else:
            attribution = """BARD (Bangla Article Classification) dataset, associated with Tanvir Alam and Md Mofijul Islam.
Project and dataset information: https://github.com/tanvirfahim15/BARD-Bangla-Article-Classifier
The five source category folders are preserved as each output record's category.
"""
        write_package(args.output, args.command, attribution, writer, inputs,
                      {"chunk_limit_uncompressed_bytes": args.chunk_mib * 1024 * 1024})


if __name__ == "__main__":
    main()


