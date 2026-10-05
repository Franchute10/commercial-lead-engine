"""UTF-8, header-based CSV provider. It never writes business records."""

import csv
from collections.abc import Iterator
from pathlib import Path

from lead_engine.application.discovery import DiscoveryCandidate, DiscoveryQuery
from lead_engine.domain.enums import SourceType


class CsvDiscoveryProvider:
    name = "csv"
    source_type = SourceType.MANUAL

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def discover(self, query: DiscoveryQuery) -> Iterator[DiscoveryCandidate]:
        try:
            with self.path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle, strict=True)
                header = next(reader, None)
                if not header:
                    raise ValueError("CSV must contain a header")
                headers = [value.strip() for value in header]
                if len(headers) != len(set(headers)) or any(not value for value in headers):
                    raise ValueError("CSV has duplicate or blank headers")
                if "name" not in headers:
                    raise ValueError("CSV requires a name column")
                count = 0
                while count < query.limit:
                    start_line = reader.line_num + 1
                    cells = next(reader, None)
                    if cells is None:
                        break
                    if not cells:
                        continue
                    values = dict(zip(headers, cells, strict=False))
                    supported = DiscoveryCandidate.model_fields.keys() - {
                        "metadata",
                        "retrieved_at",
                        "validation_error",
                    }
                    fields = {
                        key: value.strip() or None
                        for key, value in values.items()
                        if key in supported
                    }
                    error = None
                    if len(cells) != len(headers):
                        error = "CSV row has a different number of cells than its header"
                    yield DiscoveryCandidate.model_validate(
                        {
                            **fields,
                            "metadata": {
                                "filename": self.path.name,
                                "file_path": str(self.path),
                                "row_number": start_line,
                                "end_line": reader.line_num,
                                "raw_cells": cells,
                                "headers": headers,
                            },
                            "validation_error": error,
                        }
                    )
                    count += 1
        except (UnicodeError, csv.Error) as error:
            raise ValueError("CSV must be valid UTF-8 with well-formed quoting") from error
