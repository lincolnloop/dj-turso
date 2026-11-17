"""Turso's features, set by hand: Turso reports SQLite 3.50.4 but lags it in places."""

from django.db.backends.sqlite3 import features as sqlite_features

# SQLite's compile-time default for SQLITE_MAX_VARIABLE_NUMBER, which Turso keeps.
MAX_QUERY_PARAMS = 32766


class DatabaseFeatures(sqlite_features.DatabaseFeatures):
    can_clone_databases = False
    max_query_params = MAX_QUERY_PARAMS
    supports_aggregate_order_by_clause = False
    # Generated columns need an experimental flag, off by default, and Turso has
    # no STORED form.
    supports_stored_generated_columns = False
    supports_virtual_generated_columns = False
    # The gap index marks known failures of Django's own tests, not these.
    django_test_expected_failures: set[str] = set()
    django_test_skips: dict[str, set[str]] = {}
