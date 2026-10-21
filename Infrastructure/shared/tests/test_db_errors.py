from __future__ import annotations

import psycopg2

from shared.db_errors import is_transient_db_error


class _SqlStateError(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


def test_transient_db_error_classification() -> None:
    assert is_transient_db_error(OSError("connection reset"))
    assert is_transient_db_error(_SqlStateError("57P01"))
    assert not is_transient_db_error(_SqlStateError("23503"))
    assert is_transient_db_error(psycopg2.OperationalError())
    assert not is_transient_db_error(ValueError("invalid data"))
