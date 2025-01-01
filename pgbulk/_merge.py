"""Submodule for support of the MERGE statement."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Generic, Iterable, Literal, NamedTuple, TypeVar, cast

from django.db import connections, models
from django.db.backends.utils import CursorWrapper
from django.db.models import QuerySet
from typing_extensions import Self, TypeAlias

_M = TypeVar("_M", bound=models.Model)


Values: TypeAlias = "QuerySet[_M] | Iterable[_M]"

MergeUsing: TypeAlias = "_MergeUsing[_M]"


@dataclasses.dataclass
class _Update:
    fields: list[str]

    def evaluate(self) -> str:
        sql = "UPDATE SET"
        for idx, field in enumerate(self.fields):
            sql += f" {field} = source.{field}"
            if idx < len(self.fields) - 1:
                sql += ","
        return sql


@dataclasses.dataclass
class _Insert:
    columns: list[str]
    values: list[str]

    def evaluate(self) -> str:
        sql = "INSERT"
        if self.columns:
            sql += " ("
            sql += ", ".join(self.columns)
            sql += ") VALUES"
        else:
            sql += " VALUES"

        if self.values:
            sql += " ("
            sql += ", ".join(self.values)
            sql += ")"

        return sql


class _Delete:
    def evaluate(self) -> str:
        return "DELETE"


class _DoNothing:
    def evaluate(self) -> str:
        return "DO NOTHING"


class _Row(NamedTuple):
    """A row returned from a merge operation."""

    merge_action: Literal["UPDATE", "INSERT", "DELETE", "DO NOTHING"]
    """The status of the row.

    - `c`: Created
    - `u`: Updated
    - `d`: Deleted
    """

    if TYPE_CHECKING:

        def __getattr__(self, item: str) -> Any: ...


class _MergeResult(list[_Row]):
    """The result of a merge operation."""

    @property
    def created(self) -> list[_Row]:
        return [i for i in self if i.merge_action == "INSERT"]

    @property
    def updated(self) -> list[_Row]:
        return [i for i in self if i.merge_action == "UPDATE"]

    @property
    def deleted(self) -> list[_Row]:
        return [i for i in self if i.merge_action == "DELETE"]

    @classmethod
    def from_cursor(cls, cursor: CursorWrapper) -> Self:
        row = [(col.name, Any) for col in cursor.description or []]
        nt_result = NamedTuple("_Row", row)
        return cls([cast(_Row, nt_result(*row)) for row in cursor.fetchall()])


def _all_fields(model: type[models.Model]) -> list[str]:
    from pgbulk.core import _model_fields

    all_fields = [
        field.column for field in _model_fields(model) if not isinstance(field, models.AutoField)
    ]
    return all_fields


@dataclasses.dataclass
class _When:
    """
    A WHEN clause to evaluate.

    Evaluates to a SQL clause in the form:

    ```sql
    WHEN MATCHED {by} THEN {then}
    ```
    """

    condition: Literal["MATCHED", "NOT MATCHED"]
    by: Literal["SOURCE", "TARGET"]
    then: _DoNothing | _Update | _Delete | _Insert

    def evaluate(self) -> str:
        sql = f"WHEN {self.condition}"
        # Target is implicit.
        if self.by == "SOURCE":
            sql += " BY SOURCE"
        sql += f" THEN {self.then.evaluate()}"
        return sql


class _CompiledMerge(NamedTuple):
    """A compiled merge statement."""

    sql: str
    sql_args: list[Any]


def _compile_on(on: _MergeOn[_M]) -> str:
    """Compile an ON clause into a SQL string."""
    sql = "ON"
    sign = "=" if not on.distinct_from else "IS NOT DISTINCT FROM"
    for idx, field in enumerate(on.fields):
        sql += f" source.{field} {sign} target.{field}"
        if idx < len(on.fields) - 1:
            sql += " AND"

    for when in on.whens:
        sql += f" {when.evaluate()}"

    return sql


def _compile_merge(
    on: _MergeOn[_M], *, returning: list[str] | bool = False
) -> _CompiledMerge | None:
    """Compile a merge statement into a SQL string and arguments."""
    from pgbulk.core import _get_values_for_rows, _model_fields, _quote

    sql = (
        "MERGE INTO {table_name} target USING (VALUES {row_values_sql}) "
        "AS source ({all_field_names_sql})"
    )
    using = on.using

    all_fields = _model_fields(using.into.model)
    if not using.values:
        return None

    row_values, sql_args = _get_values_for_rows(
        using.into,
        using.values,
        # TODO: Revaluate this potentially.
        all_fields,
    )

    with connections[using.into.db].cursor() as cursor:
        row_values_sql = ", ".join(row_values)
        all_field_names_sql = ", ".join([_quote(field.column, cursor) for field in all_fields])
        sql = sql.format(
            table_name=_quote(using.into.model._meta.db_table, cursor),
            row_values_sql=row_values_sql,
            all_field_names_sql=all_field_names_sql,
        )

        sql += f" {_compile_on(on)}"
        if returning is not False:
            returning = returning if returning is not True else _all_fields(using.into.model)
            sql += " RETURNING merge_action()"
            if len(returning) > 1:
                sql += ","
                sql += f" {', '.join((f'target.{field}' for field in returning))}"

    return _CompiledMerge(sql, sql_args)


@dataclasses.dataclass
class _WhenMatched(Generic[_M]):
    """`WHEN MATCHED` part of a merge statement."""

    merge_on: _MergeOn[_M]

    def update(self, fields: list[str] | None = None) -> _MergeOn[_M]:
        """Add an UPDATE clause to the merge statement."""
        fields = fields or _all_fields(self.merge_on.using.into.model)
        return dataclasses.replace(
            self.merge_on,
            whens=[
                *self.merge_on.whens,
                _When(
                    condition="MATCHED",
                    by="TARGET",
                    then=_Update(fields),
                ),
            ],
        )

    def delete(self) -> _MergeOn[_M]:
        """Add a DELETE clause to the merge statement."""
        return dataclasses.replace(
            self.merge_on,
            whens=[
                *self.merge_on.whens,
                _When(condition="MATCHED", by="TARGET", then=_Delete()),
            ],
        )

    def do_nothing(self) -> _MergeOn[_M]:
        """Add a DO NOTHING clause to the merge statement."""
        return dataclasses.replace(
            self.merge_on,
            whens=[
                *self.merge_on.whens,
                _When(condition="MATCHED", by="TARGET", then=_DoNothing()),
            ],
        )


@dataclasses.dataclass
class _WhenNotMatched(Generic[_M]):
    """`WHEN NOT MATCHED` part of a merge statement."""

    merge_on: _MergeOn[_M]
    by: Literal["SOURCE", "TARGET"] = "TARGET"

    def insert(self) -> _MergeOn[_M]:
        """Add an INSERT clause to the merge statement."""
        columns = _all_fields(self.merge_on.using.into.model)
        values = [f"source.{field}" for field in columns]
        return dataclasses.replace(
            self.merge_on,
            whens=[
                *self.merge_on.whens,
                _When(condition="NOT MATCHED", by=self.by, then=_Insert(columns, values)),
            ],
        )

    def do_nothing(self) -> _MergeOn[_M]:
        """Add a DO NOTHING clause to the merge statement."""
        return dataclasses.replace(
            self.merge_on,
            whens=[
                *self.merge_on.whens,
                _When(condition="NOT MATCHED", by=self.by, then=_DoNothing()),
            ],
        )


@dataclasses.dataclass
class _MergeReturning(Generic[_M]):
    """`RETURNING` part of a merge statement."""

    merge_on: _MergeOn[_M]
    fields: list[str] | bool

    def execute(self) -> _MergeResult:
        """Execute the merge statement and return the result."""
        compiled = _compile_merge(self.merge_on, returning=self.fields)
        if compiled is None:
            return _MergeResult([])

        with connections[self.merge_on.using.into.db].cursor() as cursor:
            mogrified_query = cursor.mogrify(compiled.sql, compiled.sql_args)
            print("mogrified_query", mogrified_query)
            cursor.execute(compiled.sql, compiled.sql_args)
            return _MergeResult.from_cursor(cursor)


@dataclasses.dataclass
class _MergeOn(Generic[_M]):
    """`ON` part of a merge statement."""

    using: _MergeUsing[_M]
    fields: list[str]
    distinct_from: bool
    whens: list[_When] = dataclasses.field(default_factory=list)

    def when_matched(self) -> _WhenMatched[_M]:
        """Add a WHEN MATCHED clause to the merge statement."""
        return _WhenMatched(self)

    def when_not_matched(
        self,
        *,
        by: Literal["SOURCE", "TARGET"] = "TARGET",
    ) -> _WhenNotMatched[_M]:
        """Add a WHEN NOT MATCHED clause to the merge statement.

        Args:
            by: The side to match on (SOURCE being the row from the VALUES clause, TARGET being the
                row from the target table).

        Returns:
            The new WHEN NOT MATCHED clause which you can match on.
        """
        return _WhenNotMatched(merge_on=self, by=by)

    def execute(self) -> None:
        """Execute the merge statement."""
        compiled = _compile_merge(self)
        if compiled is None:
            return

        with connections[self.using.into.db].cursor() as cursor:
            cursor.execute(compiled.sql, compiled.sql_args)

    def returning(self, fields: list[str] | bool = True) -> _MergeReturning[_M]:
        """Set the fields to return from the merge operation.

        Args:
            fields: The fields to return. If `True`, all fields are returned.
                If a list, only the fields in the list are returned.
        """
        return _MergeReturning(self, fields)


@dataclasses.dataclass
class _MergeUsing(Generic[_M]):
    """`USING` part of a merge statement."""

    into: models.QuerySet[_M]
    values: Values[_M]

    def on(
        self,
        fields: list[str],
        *,
        distinct_from: bool = True,
    ) -> _MergeOn[_M]:
        """Build an ON clause for a merge operation by specifying fields to match between tables.

        By default, this method uses the `IS DISTINCT FROM` operator for comparisons,
        which treats NULL values as distinct from non-NULL values.

        Args:
            fields: The fields to compare. If not provided,  non-auto fields are used.
            distinct_from: Whether to use `IS DISTINCallT FROM` instead of `=`.
        """
        return _MergeOn(self, fields, distinct_from)


def build(into: models.QuerySet[_M], values: Values[_M]) -> _MergeUsing[_M]:
    """Build a merge statement.

    Args:
        into: The queryset to merge into.
        values: The values to merge, either a queryset or an iterable of model instances.

    Returns:
        A merge statement builder.
    """
    return _MergeUsing(into, values)
