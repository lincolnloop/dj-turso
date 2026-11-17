"""Turso's features, set by hand: Turso reports SQLite 3.50.4 but lags it in places."""

from django.db.backends.sqlite3 import features as sqlite_features

# SQLite's compile-time default for SQLITE_MAX_VARIABLE_NUMBER, which Turso keeps.
MAX_QUERY_PARAMS = 32766


class DatabaseFeatures(sqlite_features.DatabaseFeatures):
    can_clone_databases = False
    max_query_params = MAX_QUERY_PARAMS
    # Turso's REGEXP is Rust's regex crate, which has no backreferences.
    supports_regex_backreferencing = False
    supports_aggregate_order_by_clause = False
    # Built into Turso, where Django's sqlite3 backend needs Python 3.14's uuid7().
    supports_uuid7_function = True
    # Generated columns need an experimental flag, off by default, and Turso has
    # no STORED form.
    supports_stored_generated_columns = False
    supports_virtual_generated_columns = False
