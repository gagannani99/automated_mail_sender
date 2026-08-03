"""
parser.py
---------
Extracts HR contact records (Serial Number, Name, Email, Title, Company)
from the source PDF using pdfplumber, normalises them into a pandas
DataFrame, and cleans the result (trimming, dedup, validation).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pdfplumber

from logger import LOGGER_NAME
from utils import clean_text, validate_email

logger = logging.getLogger(LOGGER_NAME)


@dataclass
class ParseStats:
    """Summary counts produced while cleaning the raw extracted contacts."""

    total_contacts_found: int
    duplicates_removed: int
    invalid_removed: int
    remaining: int


# Canonical output columns, in this exact order.
COLUMNS = ["SNo", "Name", "Email", "Title", "Company"]

# Keywords used to fuzzy-match each source column header to a canonical one.
_HEADER_KEYWORDS: Dict[str, List[str]] = {
    "SNo": ["s.no", "sno", "serial", "sl.no", "sl no", "#", "no."],
    "Name": ["name", "hr name", "contact name", "hr contact"],
    "Email": ["email", "e-mail", "mail id", "mail"],
    "Title": ["title", "designation", "position", "role", "job title"],
    "Company": ["company", "organisation", "organization", "company name"],
}


def _map_header(raw_header: List[Optional[str]]) -> Dict[int, str]:
    """
    Map each column index in a raw table header row to one of the
    canonical COLUMNS names, based on keyword matching.

    Args:
        raw_header: The first row of an extracted table (list of cell
            strings, possibly containing None).

    Returns:
        A dict mapping column index -> canonical column name, for every
        column that could be confidently identified.
    """
    mapping: Dict[int, str] = {}
    for idx, cell in enumerate(raw_header):
        cell_clean = clean_text(cell).lower()
        if not cell_clean:
            continue
        for canonical, keywords in _HEADER_KEYWORDS.items():
            if canonical in mapping.values():
                continue
            if any(keyword in cell_clean for keyword in keywords):
                mapping[idx] = canonical
                break
    return mapping


def _row_looks_like_header(row: List[Optional[str]]) -> bool:
    """
    Heuristic: a row is treated as a header row if it matches at least
    two canonical column keywords (Name, Email, Title, Company, SNo).

    Args:
        row: A single raw table row.

    Returns:
        True if the row appears to be a header row.
    """
    mapping = _map_header(row)
    return len(mapping) >= 2


def _extract_tables_from_pdf(pdf_path: Path) -> List[List[List[Optional[str]]]]:
    """
    Open the PDF and extract every table from every page.

    Args:
        pdf_path: Path to the source PDF file.

    Returns:
        A list of tables, where each table is a list of rows, and each
        row is a list of cell values (str or None).
    """
    all_tables: List[List[List[Optional[str]]]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            try:
                tables = page.extract_tables()
            except Exception as exc:  # noqa: BLE001 - log and continue
                logger.warning("Failed to extract tables on page %s: %s", page_number, exc)
                continue
            for table in tables:
                if table:
                    all_tables.append(table)
    return all_tables


def _normalise_table(
    table: List[List[Optional[str]]],
    active_mapping: Optional[Dict[int, str]],
) -> tuple[List[Dict[str, str]], Optional[Dict[int, str]]]:
    """
    Convert a single raw table into a list of canonical dict rows,
    using (and possibly updating) a column mapping.

    Args:
        table: Raw table rows as extracted by pdfplumber.
        active_mapping: The column mapping currently in effect (carried
            over from a previous table on a prior page, in case a table
            continues across pages without repeating its header).

    Returns:
        A tuple of (list of normalised row dicts, the mapping in effect
        after processing this table, to carry forward to the next table).
    """
    records: List[Dict[str, str]] = []
    mapping = active_mapping

    for row in table:
        if not row or all(clean_text(cell) == "" for cell in row):
            continue  # blank row

        if _row_looks_like_header(row):
            mapping = _map_header(row)
            continue  # header row itself is not data

        if not mapping:
            # No header identified yet and this row isn't a header either;
            # skip until we find a usable header.
            continue

        record: Dict[str, str] = {col: "" for col in COLUMNS}
        for idx, canonical in mapping.items():
            if idx < len(row):
                record[canonical] = clean_text(row[idx])

        # A row with no name, no email, and no company is not usable data.
        if not record["Name"] and not record["Email"] and not record["Company"]:
            continue

        records.append(record)

    return records, mapping


def parse_hr_contacts(pdf_path: Path) -> Tuple[pd.DataFrame, ParseStats]:
    """
    Parse the HR contacts PDF into a cleaned pandas DataFrame.

    Steps performed:
        1. Extract every table from every page via pdfplumber.
        2. Identify header rows and map columns to the canonical schema.
        3. Merge all rows into a single ordered list.
        4. Trim whitespace on every field.
        5. Drop rows with a missing or malformed email.
        6. Drop duplicate email addresses (keeping the first occurrence,
           which preserves original ordering).

    Args:
        pdf_path: Path to the source HR contacts PDF.

    Returns:
        A tuple of:
          - A pandas DataFrame with columns SNo, Name, Email, Title,
            Company, sorted according to the original order encountered
            in the PDF, with SNo renumbered 1..N to match the "Row"
            numbering used for resuming and duplicate-skip display.
          - A ParseStats summary (total found, duplicates removed,
            invalid removed, remaining) for the startup summary display.

    Raises:
        FileNotFoundError: If the PDF does not exist.
        ValueError: If no usable contact rows could be extracted.
    """
    if not pdf_path.is_file():
        raise FileNotFoundError(f"HR contacts PDF not found at: {pdf_path}")

    if pdf_path.stat().st_size == 0:
        raise ValueError(f"HR contacts PDF is empty (0 bytes): {pdf_path}")

    logger.info("Parsing HR contacts from: %s", pdf_path)

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            page_count = len(pdf.pages)
    except Exception as exc:  # noqa: BLE001 - any pdfplumber/pdfminer failure
        raise ValueError(f"Could not open HR contacts PDF (file may be corrupt): {exc}") from exc

    if page_count == 0:
        raise ValueError(f"HR contacts PDF contains no pages: {pdf_path}")

    tables = _extract_tables_from_pdf(pdf_path)
    if not tables:
        raise ValueError(
            "No tables could be extracted from the PDF. "
            "Verify the file contains a proper tabular structure "
            "(not a scanned image or plain text layout)."
        )

    all_records: List[Dict[str, str]] = []
    running_mapping: Optional[Dict[int, str]] = None

    for table in tables:
        records, running_mapping = _normalise_table(table, running_mapping)
        all_records.extend(records)

    if not all_records:
        raise ValueError(
            "No valid contact rows could be extracted from the PDF. "
            "Check that it contains recognisable Name/Email/Company columns."
        )

    df = pd.DataFrame(all_records, columns=COLUMNS)
    total_contacts_found = len(df)

    # Preserve original order via a stable index before any filtering/dedup.
    df.insert(0, "_original_order", range(len(df)))

    # Trim all string fields (already trimmed, but defensive against blanks).
    for col in COLUMNS:
        df[col] = df[col].apply(clean_text)

    # Drop rows without a usable email at all (counted as "invalid").
    empty_email_count = int((df["Email"] == "").sum())
    df = df[df["Email"] != ""].copy()

    # Validate email format (also counted as "invalid").
    df["_valid_email"] = df["Email"].apply(validate_email)
    malformed_email_count = int((~df["_valid_email"]).sum())
    if malformed_email_count:
        logger.warning(
            "Found %d rows with malformed email addresses; they will be skipped.",
            malformed_email_count,
        )
    df = df[df["_valid_email"]].copy()
    df.drop(columns=["_valid_email"], inplace=True)

    invalid_removed = empty_email_count + malformed_email_count
    logger.info("Invalid emails removed: %d", invalid_removed)

    # Deduplicate by lower-cased email, keeping first occurrence (original order).
    before = len(df)
    df["_email_key"] = df["Email"].str.lower()
    df = df.drop_duplicates(subset="_email_key", keep="first").copy()
    df.drop(columns=["_email_key"], inplace=True)
    duplicates_removed = before - len(df)
    logger.info("Duplicate emails removed: %d", duplicates_removed)

    # Restore original encounter order, then renumber SNo sequentially
    # only if the source SNo column is missing/unreliable; otherwise keep
    # the SNo values as extracted from the PDF.
    df.sort_values(by="_original_order", inplace=True)
    df.drop(columns=["_original_order"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Renumber SNo sequentially (1..N) to match the "Row" numbering used
    # throughout the app for resuming (START_ROW) and progress tracking,
    # regardless of whatever serial numbers appeared in the source PDF.
    df["SNo"] = range(1, len(df) + 1)

    stats = ParseStats(
        total_contacts_found=total_contacts_found,
        duplicates_removed=duplicates_removed,
        invalid_removed=invalid_removed,
        remaining=len(df),
    )

    logger.info("Successfully parsed %d valid, unique HR contacts.", len(df))
    return df[COLUMNS], stats