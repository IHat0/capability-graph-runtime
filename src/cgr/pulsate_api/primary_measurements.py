"""Bounded original-byte locators. Never infer measurements from prose or OCR.

Locators are reviewed configuration, not model-generated extraction queries.
Only exact cells/spans enter the numerical chain; surrounding narrative stays
in the preserved source, not the admitted datum. PDF admission additionally
requires two-parser agreement and a source-bound rendered-page review.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from html.parser import HTMLParser

from .virtual_organism import canonical, digest


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.tables, self.table_errors = [], [], []
        self.hidden = 0
        self.table = self.row = self.cell = None
        self.table_error = None

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}: self.hidden += 1
        if tag == 'table':
            if self.table is not None: raise ValueError('Nested primary tables are ambiguous.')
            self.table = []
            self.table_error = None
        elif tag == 'tr' and self.table is not None: self.row = []
        elif tag in {'td', 'th'} and self.row is not None:
            if any(k in {'rowspan', 'colspan'} and v != '1' for k, v in attrs):
                self.table_error = 'Merged primary table cells require independent qualification.'
            self.cell = []
        elif tag in {'br', 'p', 'div'} and self.cell is not None:
            # Layout boundaries cannot silently join separate numerical tokens.
            self.cell.append(' ')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}: self.hidden -= 1
        if tag in {'td', 'th'} and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table_errors.append(self.table_error)
            self.table = None

    def handle_data(self, data):
        if self.hidden: return
        self.text.append(data)
        if self.cell is not None: self.cell.append(data)


def _span(text, anchor, literal, *, pdf=False):
    # Some historical PDF text layers omit spaces between words. Only whitespace
    # is removed for anchor search; the numerical literal itself stays exact.
    normalize = (lambda s: ''.join(s.split())) if pdf else (lambda s: ' '.join(s.split()))
    original = text
    text, anchor = normalize(text), normalize(anchor)
    if not isinstance(literal, str) or not anchor or text.count(anchor) != 1:
        raise ValueError('Primary source span is absent or ambiguous.')
    number = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
    numerical = isinstance(literal, str) and re.fullmatch(number, literal)
    if numerical:
        # A sentence period is not part of an integer. A decimal point with
        # following digits, or immediately before an exponent, is numerical.
        token_number = r'[+-]?(?:\d+(?:\.\d+|\.(?=[eE]))?|\.\d+)(?:[eE][+-]?\d+)?'
        tokens = [match for match in re.finditer(r'(?<![\d.+-])' + token_number, anchor)
            if match.group() == literal]
        if not tokens:
            raise ValueError('Primary numerical literal is partial or absent in its exact span.')
        if len(tokens) != 1:
            raise ValueError('Primary source value is not a unique exact literal in its span.')
        offset = tokens[0].start()
    elif not literal or anchor.count(literal) != 1:
        raise ValueError('Primary source value is not a unique exact literal in its span.')
    if numerical:
        start = text.index(anchor) + offset
        end = start + len(literal)
        # Whitespace removal may align words, but must never join digits or
        # turn part of a larger/negative number into an admitted measurement.
        if pdf:
            positions = [i for i, c in enumerate(original) if not c.isspace()]
            start, end = positions[start], positions[end - 1] + 1
            text = original
        if (text[start:end] != literal
                or (start and text[start - 1] in '0123456789.+-')
                or re.match(r'(?:\d|\.\d|[eE][+-]?\d)', text[end:])):
            raise ValueError('Primary numerical literal is partial or whitespace/OCR-dependent.')
    return literal


def locate(payload: bytes, source_format: str, pointer, *, pdf_reviews=(), sources=None):
    """JSON path; CSV cell; HTML cell/unique cell literal; exact text/PDF span.

    Non-JSON locators include an expected exact cell/token. It is returned only
    after checking the preserved bytes. A different cell cannot be substituted.
    PDF pages are one-based; table, row and column indices are zero-based.
    """
    if not isinstance(pointer, (list, tuple)) or len(pointer) > 20 or any(type(p) is int and p < 0 for p in pointer):
        raise ValueError('Invalid primary source pointer.')
    if source_format == 'json':
        value = json.loads(payload)
        for component in pointer: value = value[component]
        return value
    if source_format not in {'text', 'html', 'csv', 'tsv', 'pdf'}:
        raise ValueError('Unsupported original-primary source format.')
    if source_format == 'pdf':
        if len(pointer) != 4 or pointer[0] != 'pdf' or type(pointer[1]) is not int or pointer[1] < 1:
            raise ValueError('PDF evidence requires an exact page/span/literal locator.')
        page, anchor, literal = pointer[1:]
        review = next((r for r in pdf_reviews if r['page'] == page), None)
        if (review is None or review.get('source_sha256') != digest(payload)
                or review.get('status') != 'rendered_page_verified'
                or not all(review.get(k) for k in ('reviewer', 'review_basis', 'render_method'))
                or literal not in review.get('verified_literals', [])):
            raise ValueError('PDF/OCR evidence has no exact source-bound rendered-page review.')
        image_sha = review.get('image_sha256')
        image = (sources or {}).get(image_sha)
        if image is None or digest(image) != image_sha or not image.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('PDF rendered-page review image is missing or tampered.')
        try:
            import pdfplumber
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(payload))
            if reader.is_encrypted or len(reader.pages) > 1000:
                raise ValueError('Encrypted/oversized original PDF is unsupported.')
            first = reader.pages[page - 1].extract_text()
            with pdfplumber.open(io.BytesIO(payload)) as document:
                second = document.pages[page - 1].extract_text()
        except (ImportError, IndexError, OSError) as error:
            raise ValueError('Independent PDF extraction is unavailable.') from error
        if not first or not second:
            raise ValueError('PDF has no independently replayable text layer; OCR alone is not admitted.')
        a = _span(first, anchor, literal, pdf=True)
        b = _span(second, anchor, literal, pdf=True)
        if a != b: raise ValueError('Independent PDF extractors disagree.')
        return a
    text = payload.decode('utf-8-sig', errors='strict')
    if source_format == 'html':
        parser = _HTML()
        parser.feed(text)
        if pointer and ((pointer[0] == 'table' and len(pointer) == 5)
                or (pointer[0] == 'table_span' and len(pointer) == 6)):
            _, table, row, column, expected = pointer[:5]
            if any(type(i) is not int for i in (table,row,column)):
                raise ValueError('Original table indices must be exact nonnegative integers.')
            try: value = parser.tables[table][row][column]
            except IndexError as error: raise ValueError('Original table cell is absent.') from error
            if parser.table_errors[table]:
                raise ValueError(parser.table_errors[table])
            if value != expected: raise ValueError('Original HTML table cell mismatch.')
            if pointer[0] == 'table_span':
                # Both the complete cell and the selected literal are reviewed;
                # never choose a mean, SD, unit or endpoint automatically.
                return _span(value, expected, pointer[5])
            return value
        # Prose spans must not route around the merged-table refusal. Only an
        # explicitly indexed, independently unmerged table is isolated above.
        if any(parser.table_errors):
            raise ValueError(next(error for error in parser.table_errors if error))
        text = ' '.join(parser.text)
    elif source_format in {'csv', 'tsv'}:
        if len(pointer) != 4 or pointer[0] != 'cell':
            raise ValueError('Original delimited table requires an exact row/column/cell locator.')
        _, row, column, expected = pointer
        if any(type(i) is not int for i in (row,column)):
            raise ValueError('Original table indices must be exact nonnegative integers.')
        rows = list(csv.reader(io.StringIO(text), delimiter=',' if source_format == 'csv' else '\t', strict=True))
        if len(rows) > 100000: raise ValueError('Original table exceeds row bound.')
        try: value = rows[row][column]
        except IndexError as error: raise ValueError('Original table cell is absent.') from error
        if value != expected: raise ValueError('Original delimited table cell mismatch.')
        return value
    if len(pointer) != 3 or pointer[0] != 'span':
        raise ValueError('Original prose requires an exact unique span/literal locator.')
    return _span(text, pointer[1], pointer[2])


def same_value(actual, expected):
    if type(expected) in (int, float) and isinstance(actual, str):
        # No ranges, +/- strings, inequalities, locale guesses or OCR substitutions.
        try: actual = float(actual)
        except ValueError: return False
        if not math.isfinite(actual): return False
        return actual == expected
    return canonical(actual) == canonical(expected)


# Deliberately finite, dimension-checked conversions. No arbitrary curator factor
# or LLM unit conversion can turn an incompatible endpoint into a native input.
_UNITS = {
    's': ('time', 1), 'min': ('time', 60), 'h': ('time', 3600),
    'fraction': ('fraction', 1), '%': ('fraction', .01),
    'g/mol': ('molecular_mass', 1), 'mg/L': ('mass_concentration', 1),
    'mg/l': ('mass_concentration', 1), 'g/L': ('mass_concentration', 1000),
    'ml/min/kg': ('weight_normalized_clearance', 1), 'mL/min/kg': ('weight_normalized_clearance', 1),
    'L/h/kg': ('weight_normalized_clearance', 1000/60),
    'M': ('molar_concentration', 1e6), 'mM': ('molar_concentration', 1000),
    'mole/litre': ('molar_concentration', 1e6), 'mole/liter': ('molar_concentration', 1e6),
    'uM': ('molar_concentration', 1), 'umol/l': ('molar_concentration', 1),
    'nM': ('molar_concentration', .001), 'pM': ('molar_concentration', .000001),
    'kg': ('mass', 1), 'g': ('mass', .001),
    'mg': ('dose_mass', 1), 'ug': ('dose_mass', .001),
}


def normalize_value(value, original_unit, unit):
    if original_unit == unit: return value
    if type(value) not in (float, int) or original_unit not in _UNITS or unit not in _UNITS:
        raise ValueError('Unsupported primary measurement unit normalization.')
    dimension, factor = _UNITS[original_unit]
    dimension2, factor2 = _UNITS[unit]
    if dimension != dimension2: raise ValueError('Incompatible primary measurement dimensions.')
    normalized = value * factor / factor2
    if not math.isfinite(normalized): raise ValueError('Nonfinite primary measurement normalization.')
    return normalized
