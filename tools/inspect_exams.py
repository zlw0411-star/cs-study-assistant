#!/usr/bin/env python
"""Inventory PDF extraction quality without saving or printing source text."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="directory containing local exam PDFs")
    parser.add_argument("--output", type=Path, default=Path("data/private/exam-inventory.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    entries = []
    for path in sorted(root.rglob("*.pdf")):
        raw = path.read_bytes()
        record = {"relative_path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(),
                  "bytes": len(raw), "pages": None, "status": "unreadable", "page_character_counts": [], "low_text_pages": []}
        try:
            reader = PdfReader(path, strict=False)
            record["encrypted"] = reader.is_encrypted
            if reader.is_encrypted:
                try:
                    decrypted = reader.decrypt("")
                    if not decrypted:
                        record["status"] = "encrypted_password_required"
                        entries.append(record)
                        continue
                except Exception as exc:
                    record["status"] = "encrypted_or_dependency_unavailable"
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    entries.append(record)
                    continue
            counts = []
            for index, page in enumerate(reader.pages, 1):
                try:
                    text = page.extract_text() or ""
                    count = len(re.sub(r"\s", "", text))
                except Exception:
                    count = 0
                counts.append(count)
                if count < 40:
                    record["low_text_pages"].append(index)
            record["pages"] = len(counts)
            record["page_character_counts"] = counts
            if counts and all(c == 0 for c in counts):
                record["status"] = "scan_or_no_extractable_text"
            elif any(c < 40 for c in counts):
                record["status"] = "partial_text_or_low_text_pages"
            else:
                record["status"] = "text_extractable"
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        entries.append(record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"source_root": str(root), "pdf_count": len(entries), "files": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Inventoried {len(entries)} PDFs. No extracted text was stored. Output: {args.output}")


if __name__ == "__main__":
    main()
