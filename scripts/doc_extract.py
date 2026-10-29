"""
Document extraction pipeline.
Scans inbox for new documents, classifies them, extracts price + sentiment
data using Claude AI, and stores results in Parquet files.

Usage:
    python scripts/doc_extract.py              # process new documents
    python scripts/doc_extract.py --dry-run     # preview without processing
"""

import argparse
import json
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
INBOX_DIR = ROOT / "data" / "documents" / "inbox"
DOC_DIRS = {
    "broker_commentary": ROOT / "data" / "documents" / "broker_commentary",
    "oil_world": ROOT / "data" / "documents" / "oil_world",
    "energy": ROOT / "data" / "documents" / "energy",
}
INTEL_DIR = ROOT / "data" / "doc_intel"

PRICES_FILE = INTEL_DIR / "prices.parquet"
SENTIMENT_FILE = INTEL_DIR / "sentiment.parquet"
DOCUMENTS_FILE = INTEL_DIR / "documents.parquet"

# Ensure directories exist
INBOX_DIR.mkdir(parents=True, exist_ok=True)
for d in DOC_DIRS.values():
    d.mkdir(parents=True, exist_ok=True)
INTEL_DIR.mkdir(parents=True, exist_ok=True)


# ── Text extraction ──────────────────────────────────────────────────────────

def extract_text_pdf(path: Path) -> str:
    """Extract text from PDF with page markers."""
    import fitz  # pymupdf
    doc = fitz.open(str(path))
    pages = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text()
        if text.strip():
            pages.append(f"[Page {i}]\n{text}")
    doc.close()
    return "\n\n".join(pages)


def extract_text_docx(path: Path) -> str:
    """Extract text from Word document."""
    from docx import Document
    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def extract_text_xlsx(path: Path) -> str:
    """Extract text from Excel file — all sheets as text tables."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True)
    sections = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(cells):
                rows.append("\t".join(cells))
        if rows:
            sections.append(f"[Sheet: {sheet_name}]\n" + "\n".join(rows))
    wb.close()
    return "\n\n".join(sections)


def extract_text(path: Path) -> str:
    """Extract text from any supported file type."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_pdf(path)
    elif suffix in (".docx", ".doc"):
        return extract_text_docx(path)
    elif suffix in (".xlsx", ".xls"):
        return extract_text_xlsx(path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


# ── Classification ───────────────────────────────────────────────────────────

# Filename patterns for quick classification (case-insensitive)
_FILENAME_PATTERNS = {
    "broker_commentary": [r"comment", r"broker", r"market\s*view"],
    "oil_world":         [r"oil\s*world", r"oilworld", r"ow_", r"weekly", r"monthly"],
    "energy":            [r"argus", r"biofuel", r"biodiesel", r"energy", r"fame", r"ucome"],
}


def classify_by_filename(filename: str) -> str | None:
    """Try to classify document by filename pattern. Returns None if ambiguous."""
    name_lower = filename.lower()
    for doc_type, patterns in _FILENAME_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, name_lower):
                return doc_type
    return None


def classify_by_content(client, first_page: str) -> str:
    """Use Claude to classify document by content."""
    from doc_prompts import CLASSIFY_DOCUMENT

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=50,
        messages=[{
            "role": "user",
            "content": CLASSIFY_DOCUMENT.format(text=first_page[:3000]),
        }],
    )
    result = response.content[0].text.strip().lower()

    # Map to known types
    for doc_type in ("broker_commentary", "oil_world", "energy"):
        if doc_type in result:
            return doc_type
    return "broker_commentary"  # default fallback


def classify_document(filename: str, first_page: str, client) -> str:
    """Classify document: try filename first, fall back to Claude."""
    doc_type = classify_by_filename(filename)
    if doc_type:
        return doc_type
    return classify_by_content(client, first_page)


# ── Date parsing ─────────────────────────────────────────────────────────────

_DATE_PATTERNS = [
    r"(\d{4}[-_]\d{2}[-_]\d{2})",      # 2026-04-07 or 2026_04_07
    r"(\d{2}[-_]\d{2}[-_]\d{4})",      # 07-04-2026
    r"(\d{1,2}[-_]\d{1,2}[-_]\d{2,4})", # 7_4_26
]


def parse_date_from_filename(filename: str) -> str | None:
    """Try to extract a date from the filename."""
    for pattern in _DATE_PATTERNS:
        match = re.search(pattern, filename)
        if match:
            date_str = match.group(1).replace("_", "-")
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y"):
                try:
                    dt = datetime.strptime(date_str, fmt)
                    if 2020 <= dt.year <= 2030:
                        return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue
    return None


# ── Claude extraction ────────────────────────────────────────────────────────

def extract_with_claude(client, filename: str, text: str, doc_type: str) -> dict:
    """Send document text to Claude for structured extraction."""
    from doc_prompts import PROMPTS

    system_prompt = PROMPTS.get(doc_type, PROMPTS["broker_commentary"])

    user_message = (
        f"Extract all commodity market data from this document.\n\n"
        f"Filename: {filename}\n\n"
        f"Document text:\n{text}"
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # Fix common JSON issues: trailing commas before ] or }
    raw = re.sub(r",\s*([}\]])", r"\1", raw)

    return json.loads(raw)


# ── Parquet I/O ──────────────────────────────────────────────────────────────

_PRICES_COLS = [
    "document_date", "source_document", "document_type",
    "product", "geography", "sub_geography",
    "price_type", "value", "unit", "change", "tenor", "broker", "raw_text",
]

_SENTIMENT_COLS = [
    "document_date", "source_document", "document_type",
    "product", "geography",
    "sentiment", "confidence", "theme", "timeframe", "summary", "raw_text",
]

_DOCUMENTS_COLS = [
    "filename", "document_type", "document_date", "processed_date",
    "num_prices", "num_sentiment", "status",
]


def _load_parquet(path: Path, columns: list[str]) -> pd.DataFrame:
    """Load existing Parquet or return empty DataFrame with correct schema."""
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=columns)


def _save_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    """Write Parquet safely. On Windows, atomic rename can fail due to file locking,
    so we fall back to direct overwrite."""
    try:
        # Try atomic: write temp then rename
        tmp_path = path.with_suffix(".parquet.tmp")
        df.to_parquet(str(tmp_path), index=False)
        if path.exists():
            path.unlink()
        tmp_path.rename(path)
    except PermissionError:
        # Fallback: direct overwrite (Windows file locking)
        df.to_parquet(str(path), index=False)


def get_processed_files() -> set[str]:
    """Return set of already-processed filenames."""
    docs_df = _load_parquet(DOCUMENTS_FILE, _DOCUMENTS_COLS)
    return set(docs_df["filename"].tolist()) if not docs_df.empty else set()


def save_extraction(
    filename: str,
    doc_type: str,
    doc_date: str,
    result: dict,
) -> None:
    """Append extraction results to Parquet files."""
    now = datetime.now().isoformat()

    # Build prices rows
    price_rows = []
    for p in result.get("prices", []):
        price_rows.append({
            "document_date": doc_date,
            "source_document": filename,
            "document_type": doc_type,
            "product": p.get("product", ""),
            "geography": p.get("geography", ""),
            "sub_geography": p.get("sub_geography", ""),
            "price_type": p.get("price_type", ""),
            "value": p.get("value"),
            "unit": p.get("unit", ""),
            "change": p.get("change"),
            "tenor": p.get("tenor"),
            "broker": p.get("broker"),
            "raw_text": p.get("raw_text", ""),
        })

    # Build sentiment rows
    sent_rows = []
    for s in result.get("sentiment", []):
        sent_rows.append({
            "document_date": doc_date,
            "source_document": filename,
            "document_type": doc_type,
            "product": s.get("product", ""),
            "geography": s.get("geography", ""),
            "sentiment": s.get("sentiment", ""),
            "confidence": s.get("confidence", 0.5),
            "theme": s.get("theme", ""),
            "timeframe": s.get("timeframe", ""),
            "summary": s.get("summary", ""),
            "raw_text": s.get("raw_text", ""),
        })

    # Append prices
    if price_rows:
        existing = _load_parquet(PRICES_FILE, _PRICES_COLS)
        new_df = pd.DataFrame(price_rows)
        combined = pd.concat([existing, new_df], ignore_index=True)
        _save_parquet_atomic(combined, PRICES_FILE)

    # Append sentiment
    if sent_rows:
        existing = _load_parquet(SENTIMENT_FILE, _SENTIMENT_COLS)
        new_df = pd.DataFrame(sent_rows)
        combined = pd.concat([existing, new_df], ignore_index=True)
        _save_parquet_atomic(combined, SENTIMENT_FILE)

    # Append document record
    doc_row = pd.DataFrame([{
        "filename": filename,
        "document_type": doc_type,
        "document_date": doc_date,
        "processed_date": now,
        "num_prices": len(price_rows),
        "num_sentiment": len(sent_rows),
        "status": "success",
    }])
    existing_docs = _load_parquet(DOCUMENTS_FILE, _DOCUMENTS_COLS)
    combined_docs = pd.concat([existing_docs, doc_row], ignore_index=True)
    _save_parquet_atomic(combined_docs, DOCUMENTS_FILE)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract commodity data from local documents.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without processing.")
    args = parser.parse_args()

    # Collect files from inbox + typed subfolders
    all_files: list[tuple[Path, str | None]] = []  # (path, known_type_or_None)

    # Inbox files (type unknown)
    for f in sorted(INBOX_DIR.iterdir()):
        if f.is_file() and f.suffix.lower() in (".pdf", ".docx", ".doc", ".xlsx", ".xls"):
            all_files.append((f, None))

    # Typed subfolder files (type known)
    for doc_type, dir_path in DOC_DIRS.items():
        for f in sorted(dir_path.iterdir()):
            if f.is_file() and f.suffix.lower() in (".pdf", ".docx", ".doc", ".xlsx", ".xls"):
                all_files.append((f, doc_type))

    if not all_files:
        print("No documents found in inbox or document folders.")
        return

    # Filter already-processed
    processed = get_processed_files()
    to_process = [(p, t) for p, t in all_files if p.name not in processed]

    print(f"Found {len(all_files)} document(s), {len(to_process)} new to process.")

    if not to_process:
        print("Nothing new to process.")
        return

    if args.dry_run:
        print("\n*** DRY RUN — no files will be processed ***\n")
        for path, known_type in to_process:
            type_str = known_type or "(needs classification)"
            print(f"  {path.name}  [{type_str}]")
        return

    # Initialize Claude client
    import anthropic
    client = anthropic.Anthropic()

    for path, known_type in to_process:
        filename = path.name
        print(f"\n{'='*60}")
        print(f"Processing: {filename}")

        # Extract text
        try:
            text = extract_text(path)
        except Exception as e:
            print(f"  ERROR reading file: {e}")
            continue
        print(f"  Extracted {len(text):,} chars.")

        # Classify
        if known_type:
            doc_type = known_type
            print(f"  Type: {doc_type} (from folder)")
        else:
            doc_type = classify_document(filename, text[:3000], client)
            print(f"  Type: {doc_type} (classified)")

            # Move from inbox to typed folder
            dest = DOC_DIRS[doc_type] / filename
            if not dest.exists():
                shutil.move(str(path), str(dest))
                print(f"  Moved to documents/{doc_type}/")

        # Parse date
        doc_date = parse_date_from_filename(filename)
        if not doc_date:
            doc_date = datetime.now().strftime("%Y-%m-%d")
            print(f"  Date: {doc_date} (defaulted to today)")
        else:
            print(f"  Date: {doc_date} (from filename)")

        # Extract with Claude
        print(f"  Sending to Claude...", end=" ", flush=True)
        try:
            result = extract_with_claude(client, filename, text, doc_type)
        except json.JSONDecodeError as e:
            print(f"FAILED (invalid JSON: {e})")
            continue
        except Exception as e:
            print(f"FAILED ({e})")
            continue

        n_prices = len(result.get("prices", []))
        n_sentiment = len(result.get("sentiment", []))
        print(f"Done. {n_prices} price(s), {n_sentiment} sentiment(s).")

        # Save
        save_extraction(filename, doc_type, doc_date, result)
        print(f"  Saved to Parquet.")

    print(f"\n{'='*60}")
    print("All done.")


if __name__ == "__main__":
    main()
