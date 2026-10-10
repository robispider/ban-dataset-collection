#!/usr/bin/env python3
"""Extract chunk ZIPs and assemble ready-to-use Bangla dataset files.

All generated files go under the Git-ignored ``use-data`` directory. Uses only
the Python standard library.
"""
from __future__ import annotations

import argparse
import gzip
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "use-data"
PACKAGES = ("wikipedia", "newspaper", "bard")


def archive_sets(root: Path, package: str) -> list[tuple[Path, Path]]:
    """Return (archive, relative package path) pairs in deterministic order."""
    if package == "wikipedia":
        base = root / "wikidata"
        return [(p, Path()) for p in sorted(base.glob("*.zip"))]
    if package == "newspaper":
        base = root / "bangla-newspaper"
        return [(p, Path()) for p in sorted(base.glob("*.zip"))]
    base = root / "bard"
    return [(p, p.parent.relative_to(base).joinpath(""))
            for p in sorted(base.rglob("*.zip"))]


def safe_member_name(name: str) -> Path:
    member = PurePosixPath(name)
    windows_member = PureWindowsPath(name)
    if (member.is_absolute() or windows_member.drive or "\\" in name or not member.parts
            or any(part in ("", ".", "..") for part in member.parts)):
        raise ValueError(f"Unsafe path in ZIP archive: {name!r}")
    return Path(*member.parts)


def extract_package(root: Path, output: Path, package: str) -> list[Path]:
    extracted_root = output / "extracted" / package
    extracted: list[Path] = []
    archives = archive_sets(root, package)
    if not archives:
        raise FileNotFoundError(f"No ZIP chunks found for {package} under {root}")

    for archive_path, relative_dir in archives:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative_member = safe_member_name(info.filename)
                if relative_member.suffix not in (".jsonl", ".gz"):
                    continue
                target = extracted_root / relative_dir / relative_member
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=target.parent, prefix=target.name + ".", suffix=".tmp", delete=False
                ) as temporary:
                    temp_path = Path(temporary.name)
                    try:
                        with archive.open(info, "r") as source:
                            shutil.copyfileobj(source, temporary, length=1 << 20)
                    except BaseException:
                        temp_path.unlink(missing_ok=True)
                        raise
                temp_path.replace(target)
                extracted.append(target)
        print(f"Extracted {archive_path.relative_to(root)}")
    return extracted


def concatenate_jsonl(sources: list[Path], destination: Path, *, compressed: bool = True) -> int:
    if not sources:
        raise FileNotFoundError(f"No extracted JSONL chunks found for {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")
    records = 0
    try:
        opener = gzip.open if compressed else open
        if compressed:
            output_stream = opener(temp_path, "wb", compresslevel=6)
        else:
            output_stream = opener(temp_path, "wb")
        with output_stream as output:
            for source_path in sorted(sources):
                with source_path.open("rb") as source:
                    shutil.copyfileobj(source, output, length=1 << 20)
                with source_path.open("rb") as source:
                    records += sum(1 for line in source if line.strip())
        temp_path.replace(destination)
    finally:
        temp_path.unlink(missing_ok=True)
    return records


def assemble_package(root: Path, output: Path, package: str) -> None:
    extracted_root = output / "extracted" / package
    assembled_root = output / "assembled"
    if package == "wikipedia":
        for split in ("train", "heldout"):
            sources = sorted(extracted_root.glob(f"bnwiki-{split}-*.jsonl"))
            count = concatenate_jsonl(sources, assembled_root / "wikidata" / f"{split}.jsonl.gz")
            print(f"Assembled Wikipedia {split}: {count:,} records")
        # Token-count sidecars are already gzip-compressed by the source package.
        for split in ("train", "heldout"):
            source = extracted_root / f"bnwiki-{split}.tokens.tsv.gz"
            if source.exists():
                destination = assembled_root / "wikidata" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        return

    if package == "newspaper":
        sources = sorted(extracted_root.glob("*.jsonl"))
        count = concatenate_jsonl(sources, assembled_root / "bangla-newspaper.jsonl.gz")
        plain_count = concatenate_jsonl(
            sources, assembled_root / "bangla-newspaper.jsonl", compressed=False
        )
        if plain_count != count:
            raise ValueError(f"Newspaper output record counts differ: gzip={count}, plain={plain_count}")
        print(f"Assembled newspaper corpus: {count:,} records (gzip and plain UTF-8 JSONL)")
        return

    categories = sorted(path for path in extracted_root.iterdir() if path.is_dir())
    if not categories:
        raise FileNotFoundError(f"No extracted BARD category folders in {extracted_root}")
    for category_dir in categories:
        sources = sorted(category_dir.glob("*.jsonl"))
        count = concatenate_jsonl(sources, assembled_root / "bard" / f"{category_dir.name}.jsonl.gz")
        print(f"Assembled BARD {category_dir.name}: {count:,} records")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("extract", "assemble", "all"),
                        help="Extract chunks, assemble files, or do both in order")
    parser.add_argument("--package", choices=(*PACKAGES, "all"), default="all",
                        help="Dataset package to process (default: all)")
    parser.add_argument("--input-root", type=Path, default=ROOT,
                        help="Folder containing wikidata, bangla-newspaper, and bard")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT,
                        help="Generated files folder (default: Git-ignored use-data)")
    args = parser.parse_args()
    root = args.input_root.resolve()
    output = args.output_root.resolve()
    packages = PACKAGES if args.package == "all" else (args.package,)

    if args.action in ("extract", "all"):
        for package in packages:
            extract_package(root, output, package)
    if args.action in ("assemble", "all"):
        for package in packages:
            assemble_package(root, output, package)

    print(f"Working files are under {output}; this directory is Git-ignored.")


if __name__ == "__main__":
    main()
