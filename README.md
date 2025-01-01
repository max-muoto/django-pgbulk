# django-pgbulk

`django-pgbulk` provides functions for doing native Postgres bulk upserts (i.e. [UPDATE ON CONFLICT](https://www.postgresql.org/docs/current/sql-insert.html)), bulk updates, and [COPY FROM](https://www.postgresql.org/docs/current/sql-copy.html).

Bulk upserts can distinguish between updated/created rows and ignore unchanged updates.

Bulk updates are true bulk updates, unlike Django's [bulk_update](https://docs.djangoproject.com/en/4.2/ref/models/querysets/#bulk-update) which can still suffer from *O(N)* queries and can create poor locking scenarios.

Bulk copies can significantly speed-up bulk inserts, sometimes by an order of magnitude over Django's `bulk_create`.

## Quick Start

### Examples

#### Update or insert rows

```python
import pgbulk

pgbulk.upsert(
    MyModel,
    [
        MyModel(int_field=1, some_attr="some_val1"),
        MyModel(int_field=2, some_attr="some_val2"),
    ],
    # These are the fields that identify the uniqueness constraint.
    ["int_field"],
    # These are the fields that will be updated if the row already
    # exists. If not provided, all fields will be updated
    ["some_attr"]
)
```

#### Bulk update rows

```python
import pgbulk

pgbulk.update(
    MyModel,
    [
        MyModel(id=1, some_attr='some_val1'),
        MyModel(id=2, some_attr='some_val2')
    ],
    # These are the fields that will be updated. If not provided,
    # all fields will be updated
    ['some_attr']
)
```

#### Copy rows into a table

```python
import pgbulk

pgbulk.copy(
    MyModel,
    # Insert these rows using COPY FROM
    [
        MyModel(id=1, some_attr='some_val1'),
        MyModel(id=2, some_attr='some_val2')
    ],
)
```

#### Merge rows into a table

```python
import pgbulk

pgbulk.merge(
    MyModel,
    [
        MyModel(id=1, some_attr="some_val1"),
    ]
).on(["some_attr"])
# When the row is not matched on `some_attr`, insert it.
# Otherwise, update it with the new values.
.when_not_matched()
.insert()
.when_matched()
.update()
.execute()
```

### Advanced Features

Here are some advanced features at a glance:

- `pgbulk.upsert` can categorize which rows were inserted or updated.
- `pgbulk.upsert` and `pgbulk.update` can ignore updating unchanged fields.
- `pgbulk.upsert` and `pgbulk.update` can use expressions in updates.
- `pgbulk.merge` can chain multiple `WHEN` clauses.

## Documentation

[View the django-pgbulk docs here](https://django-pgbulk.readthedocs.io/) for more examples.

## Compatibility

`django-pgbulk` is compatible with Python 3.9 - 3.13, Django 4.2 - 5.1, Psycopg 2 - 3, and Postgres 13 - 17.

## Installation

Install `django-pgbulk` with:

    pip3 install django-pgbulk

## Contributing Guide

For information on setting up django-pgbulk for development and contributing changes, view [CONTRIBUTING.md](CONTRIBUTING.md).

## Creators

- [Wes Kendall](https://github.com/wesleykendall)

## Other Contributors

- @max-muoto
- @dalberto
