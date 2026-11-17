"""Native SQL for functions Django's sqlite3 backend runs in Python."""

import collections.abc
import typing as t

from django.db.models import aggregates, expressions, functions
from django.db.models.sql import compiler

from dj_turso import exceptions, operations

SQLParams = tuple[str, tuple[t.Any, ...]]

# 53 random bits, as many as a float holds exactly, scaled into [0, 1).
RANDOM_TEMPLATE = "((RANDOM() & 9007199254740991) / 9007199254740992.0)"

# Django stores a UUID as 32 lowercase hex digits; Turso's functions return a blob.
NATIVE_TEMPLATES: dict[type[expressions.Func], str] = {
    functions.Cot: "(1 / TAN(%(expressions)s))",
    functions.Random: RANDOM_TEMPLATE,
    functions.UUID4: "LOWER(HEX(UUID4()))",
    functions.UUID7: "LOWER(HEX(UUID7()))",
}

# Turso, like SQLite, ignores a negative ROUND precision; scale to units and back.
# The factor is a real literal so integer operands do not divide as integers.
ROUND_LEFT_OF_POINT_TEMPLATE = "(ROUND(%(expressions)s / {factor}) * {factor})"

HASH_FUNCTIONS = (
    functions.MD5,
    functions.SHA1,
    functions.SHA224,
    functions.SHA256,
    functions.SHA384,
    functions.SHA512,
)


# Turso's STDDEV is the sample deviation; a single value has a population one of 0.
STDDEV_POP_TEMPLATE = "(COALESCE({stddev}, 0) * SQRT(({count} - 1.0) / {count}))"

# The aggregate of each bit, from the lowest: BIT_XOR keeps the parity of the ones.
BIT_AGGREGATE_FUNCTIONS = {"BIT_AND": "MIN", "BIT_OR": "MAX", "BIT_XOR": "SUM"}
BIT_TEMPLATE = "((%(function)s((%(expressions)s >> {bit}) & 1)%(filter)s & 1) << {bit})"
INTEGER_BITS = 64


class SQLCompiler(compiler.SQLCompiler):
    def compile(self, node: t.Any) -> t.Any:
        if isinstance(node, HASH_FUNCTIONS):
            raise exceptions.HashFunctionNotSupportedError(type(node).__name__)
        if isinstance(node, functions.Round) and has_negative_precision(node):
            return compile_round_left_of_point(self, node)
        if isinstance(node, aggregates.Aggregate):
            aggregate_compiler = AGGREGATE_COMPILERS.get(node.function)
            if aggregate_compiler is not None:
                return aggregate_compiler(self, node)
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


def has_negative_precision(node: functions.Round) -> bool:
    precision = node.get_source_expressions()[1]
    return isinstance(precision, expressions.Value) and precision.value < 0


# Func's own as_sqlite keeps Django's NUMERIC cast of decimal results, which Round's
# would refuse for a negative precision.
def compile_round_left_of_point(
    compiler: SQLCompiler, node: functions.Round
) -> SQLParams:
    value, precision = node.get_source_expressions()
    factor = f"1{'0' * -int(precision.value)}.0"
    rounded = node.copy()
    rounded.set_source_expressions([value])
    sql, params = expressions.Func.as_sqlite(
        rounded,
        compiler,
        compiler.connection,
        template=ROUND_LEFT_OF_POINT_TEMPLATE.format(factor=factor),
    )
    return sql, tuple(params)


def compile_any_value(compiler: SQLCompiler, node: t.Any) -> SQLParams:
    return compile_call(compiler, node, "MIN")


def compile_stddev_samp(compiler: SQLCompiler, node: t.Any) -> SQLParams:
    return compile_call(compiler, node, "STDDEV")


def compile_stddev_pop(compiler: SQLCompiler, node: t.Any) -> SQLParams:
    return operations.fill_template(
        STDDEV_POP_TEMPLATE,
        count=compile_call(compiler, node, "COUNT"),
        stddev=compile_call(compiler, node, "STDDEV"),
    )


def compile_var_samp(compiler: SQLCompiler, node: t.Any) -> SQLParams:
    return operations.fill_template(
        "POWER({stddev}, 2)", stddev=compile_stddev_samp(compiler, node)
    )


def compile_var_pop(compiler: SQLCompiler, node: t.Any) -> SQLParams:
    return operations.fill_template(
        "POWER({stddev}, 2)", stddev=compile_stddev_pop(compiler, node)
    )


# One aggregate per bit, each compiled on its own to carry its own parameters.
def compile_bit_aggregate(compiler: SQLCompiler, node: t.Any) -> SQLParams:
    function = BIT_AGGREGATE_FUNCTIONS[node.function]
    terms = [
        node.as_sqlite(
            compiler,
            compiler.connection,
            function=function,
            template=BIT_TEMPLATE.format(bit=bit),
        )
        for bit in range(INTEGER_BITS)
    ]
    return "(" + " | ".join(sql for sql, _ in terms) + ")", tuple(
        param for _, params in terms for param in params
    )


def compile_call(compiler: SQLCompiler, node: t.Any, function: str) -> SQLParams:
    sql, params = node.as_sqlite(compiler, compiler.connection, function=function)
    return sql, tuple(params)


AGGREGATE_COMPILERS: dict[
    str | None, collections.abc.Callable[[SQLCompiler, t.Any], SQLParams]
] = {
    "ANY_VALUE": compile_any_value,
    "BIT_AND": compile_bit_aggregate,
    "BIT_OR": compile_bit_aggregate,
    "BIT_XOR": compile_bit_aggregate,
    "STDDEV_POP": compile_stddev_pop,
    "STDDEV_SAMP": compile_stddev_samp,
    "VAR_POP": compile_var_pop,
    "VAR_SAMP": compile_var_samp,
}
