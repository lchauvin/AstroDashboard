"""Header-only readers for FITS and XISF.

Only the header bytes are read (a few KB), never the image data, so indexing
tens of thousands of subs stays fast. Files are opened read-only.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

FITS_BLOCK = 2880
MAX_FITS_BLOCKS = 64          # 184 KB of header is far beyond anything NINA writes
MAX_XISF_HEADER = 4 * 1024 * 1024

# Bump when the set of extracted keys changes: cached headers are then re-read once.
HEADER_VERSION = "2"

# Keywords the library uses. Everything else is dropped to keep the cache small.
KEEP = {
    "IMAGETYP", "FILTER", "EXPOSURE", "EXPTIME", "DATE-OBS", "DATE-LOC", "MJD-OBS",
    "GAIN", "OFFSET", "READOUTM", "XBINNING", "YBINNING", "SET-TEMP", "CCD-TEMP",
    "INSTRUME", "TELESCOP", "FOCALLEN", "XPIXSZ", "OBJECT", "RA", "DEC",
    "OBJCTRA", "OBJCTDEC", "OBJCTROT", "CENTALT", "CENTAZ", "AIRMASS", "PIERSIDE",
    "FOCPOS", "FOCUSPOS", "FOCTEMP", "HUMIDITY", "DEWPOINT", "AMBTEMP", "WINDSPD",
    "CLOUDCVR", "SITELAT", "SITELONG", "SITEELEV", "LAT-OBS", "LONG-OBS", "ALT-OBS",
    "OBSGEO-B", "OBSGEO-L", "OBSGEO-H", "NAXIS1", "NAXIS2", "BITPIX", "NCOMBINE",
    "DATE-END",
}

# XISF properties worth keeping, stored under these header names. WBPP writes
# EXPTIME = 0 on every master bias; the property holds the real sub exposure.
XISF_PROPERTIES = {"Instrument:FrameExposureTime": "XISF-FRAMEEXP"}

_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$")


def parse_value(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("'"):
        # FITS strings: single-quoted, '' escapes a quote, trailing blanks insignificant.
        out, i = [], 1
        while i < len(raw):
            ch = raw[i]
            if ch == "'":
                if i + 1 < len(raw) and raw[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(ch)
            i += 1
        return "".join(out).rstrip()
    token = raw
    if token == "T":
        return True
    if token == "F":
        return False
    if _NUMBER.match(token):
        token = token.replace("D", "E").replace("d", "e")
        try:
            if re.fullmatch(r"[+-]?\d+", token):
                return int(token)
            return float(token)
        except ValueError:
            return raw
    return raw


def _split_card_value(text: str) -> str:
    """Strip an inline `/ comment` that is outside a quoted string."""
    in_str = False
    for i, ch in enumerate(text):
        if ch == "'":
            in_str = not in_str
        elif ch == "/" and not in_str:
            return text[:i]
    return text


def read_fits_header(path: Path) -> dict[str, Any]:
    header: dict[str, Any] = {}
    with open(path, "rb") as fh:
        for _ in range(MAX_FITS_BLOCKS):
            block = fh.read(FITS_BLOCK)
            if len(block) < FITS_BLOCK:
                raise ValueError("truncated FITS header")
            text = block.decode("ascii", errors="replace")
            for i in range(0, FITS_BLOCK, 80):
                card = text[i:i + 80]
                key = card[:8].strip()
                if key == "END":
                    return header
                if key in KEEP and key not in header and card[8:10] == "= ":
                    header[key] = parse_value(_split_card_value(card[10:]))
    raise ValueError("no END card in FITS header")


_XISF_KEYWORD = re.compile(r'<FITSKeyword\s+name="([^"]+)"\s+value="([^"]*)"')
_XISF_IMAGE_TYPE = re.compile(r'<Image\b[^>]*\bimageType="([^"]+)"')
_XISF_PROPERTY = re.compile(r'<Property\s+id="([^"]+)"[^>]*\bvalue="([^"]*)"')


def read_xisf_header(path: Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        prefix = fh.read(16)
        if prefix[:8] != b"XISF0100":
            raise ValueError("not an XISF file")
        (length,) = struct.unpack("<I", prefix[8:12])
        if length <= 0 or length > MAX_XISF_HEADER:
            raise ValueError("implausible XISF header length")
        xml = fh.read(length).decode("utf-8", errors="replace")
    header: dict[str, Any] = {}
    for name, value in _XISF_KEYWORD.findall(xml):
        if name in KEEP and name not in header:
            header[name] = parse_value(value.replace("&apos;", "'").replace("&quot;", '"'))
    m = _XISF_IMAGE_TYPE.search(xml)
    if m and "IMAGETYP" not in header:
        header["IMAGETYP"] = m.group(1)
    for prop_id, value in _XISF_PROPERTY.findall(xml):
        key = XISF_PROPERTIES.get(prop_id)
        if key and key not in header:
            header[key] = parse_value(value)
    return header


IMAGE_SUFFIXES = {".fits", ".fit", ".fts", ".xisf"}


def read_header(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".xisf":
        return read_xisf_header(path)
    return read_fits_header(path)
