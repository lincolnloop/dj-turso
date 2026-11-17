"""Native SQL for functions Django's sqlite3 backend runs in Python."""

import typing as t

from django.db.models import expressions, functions
from django.db.models.sql import compiler

from dj_turso import exceptions

# 53 random bits, as many as a float holds exactly, scaled into [0, 1).
RANDOM_TEMPLATE = "((RANDOM() & 9007199254740991) / 9007199254740992.0)"

# Django stores a UUID as 32 lowercase hex digits; Turso's functions return a blob.
NATIVE_TEMPLATES: dict[type[expressions.Func], str] = {
    functions.Cot: "(1 / TAN(%(expressions)s))",
    functions.Random: RANDOM_TEMPLATE,
    functions.UUID4: "LOWER(HEX(UUID4()))",
    functions.UUID7: "LOWER(HEX(UUID7()))",
}

HASH_FUNCTIONS = (
    functions.MD5,
    functions.SHA1,
    functions.SHA224,
    functions.SHA256,
    functions.SHA384,
    functions.SHA512,
)


class SQLCompiler(compiler.SQLCompiler):
    def compile(self, node: t.Any) -> t.Any:
        if isinstance(node, HASH_FUNCTIONS):
            raise exceptions.HashFunctionNotSupportedError(type(node).__name__)
        for function, template in NATIVE_TEMPLATES.items():
            if isinstance(node, function):
                # as_sqlite keeps Django's NUMERIC cast of decimal results.
                return node.as_sqlite(self, self.connection, template=template)
        return super().compile(node)


class SQLInsertCompiler(SQLCompiler, compiler.SQLInsertCompiler):
    pass


class SQLDeleteCompiler(SQLCompiler, compiler.SQLDeleteCompiler):
    pass


class SQLUpdateCompiler(SQLCompiler, compiler.SQLUpdateCompiler):
    pass


class SQLAggregateCompiler(SQLCompiler, compiler.SQLAggregateCompiler):
    pass
