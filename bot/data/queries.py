from typing import Optional, TypeVar, Any, Callable, Generic, List, Union
from psycopg import AsyncConnection, AsyncCursor
from psycopg import sql
from psycopg.rows import DictRow

import logging

from .conditions import Condition
from .base import Expression, RawExpr
from .connector import Connector


logger = logging.getLogger(__name__)


TQueryT = TypeVar('TQueryT', bound='TableQuery')
SQueryT = TypeVar('SQueryT', bound='Select')

QueryResult = TypeVar('QueryResult')


class Query(Generic[QueryResult]):
    """
    ABC for an executable query statement.
    """
    __slots__ = ('conn', 'cursor', '_adapter', 'connector')

    _adapter: Callable[..., QueryResult]

    def __init__(self, *args, row_adapter=None, connector=None, conn=None, cursor=None, **kwargs):
        self.connector: Optional[Connector] = connector
        self.conn: Optional[AsyncConnection] = conn
        self.cursor: Optional[AsyncCursor] = cursor

        if row_adapter is not None:
            self._adapter = row_adapter
        else:
            self._adapter = self._no_adapter

    def bind(self, connector: Connector):
        self.connector = connector
        return self

    def with_cursor(self, cursor: AsyncCursor):
        self.cursor = cursor
        return self

    def with_connection(self, conn: AsyncConnection):
        self.conn = conn
        return self

    def _no_adapter(self, *data: DictRow) -> tuple[DictRow, ...]:
        return data

    def with_adapter(self, callable: Callable[..., QueryResult]):
        # NOTE: Postcomposition functor, Query[QR2] = (QR1 -> QR2) o Query[QR1]
        # For this to work cleanly, callable should have arg type of QR1, not any
        self._adapter = callable
        return self

    def one(self):
        # TODO: Postcomposition with item functor, Query[List[QR1]] -> Query[QR1]
        return self

    def build(self) -> Expression:
        raise NotImplementedError

    async def _execute(self, cursor: AsyncCursor) -> QueryResult:
        query, values = self.build().as_tuple()
        # TODO: Move logging out to a custom cursor
        # logger.debug(
        #     f"Executing query ({query.as_string(cursor)}) with values {values}",
        #     extra={'action': "Query"}
        # )
        await cursor.execute(sql.Composed((query,)), values)
        data = await cursor.fetchall()
        return self._adapter(*data)

    async def execute(self, cursor=None) -> QueryResult:
        """
        Execute the query, optionally with the provided cursor, and return the result rows.
        If no cursor is provided, and no cursor has been set with `with_cursor`,
        the execution will create a new cursor from the connection and close it automatically.
        """
        # Create a cursor if possible
        cursor = cursor if cursor is not None else self.cursor
        if self.cursor is None:
            if self.conn is None:
                if self.connector is None:
                    raise ValueError("Cannot execute query without cursor, connection, or connector.")
                else:
                    conn = await self.connector.get_connection()
            else:
                conn = self.conn

            async with conn.cursor() as cursor:
                data = await self._execute(cursor)
        else:
            data = await self._execute(cursor)
        return data

    def __await__(self):
        return self.execute().__await__()


class TableQuery(Query[QueryResult]):
    """
    ABC for an executable query statement expected to be run on a single table.
    """
    __slots__ = (
        'table',
        'condition', '_extra', '_limit', '_order', '_joins'
    )

    def __init__(self, table, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.table: str = table

    def options(self, **kwargs):
        """
        Set some query options.
        Default implementation does nothing.
        Should be overridden to provide specific options.
        """
        return self


class WhereMixin(TableQuery[QueryResult]):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.condition: Optional[Condition] = None

    def where(self, *args: Condition, **kwargs):
        """
        Add a Condition to the query.
        Position arguments should be Conditions,
        and keyword arguments should be of the form `column=Value`,
        where Value may be a Value-type or a literal value.
        All provided Conditions will be and-ed together to create a new Condition.
        TODO: Maybe just pass this verbatim to a condition.
        """
        if args or kwargs:
            condition = Condition.construct(*args, **kwargs)
            if self.condition is not None:
                condition = self.condition & condition

            self.condition = condition

        return self

    @property
    def _where_section(self) -> Optional[Expression]:
        if self.condition is not None:
            return RawExpr.join_tuples((sql.SQL('WHERE'), ()), self.condition.as_tuple())
        else:
            return None


class JoinMixin(TableQuery[QueryResult]):
    __slots__ = ()
    # TODO: Remember to add join slots to TableQuery

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._joins: list[Expression, ...] = []

    def join(self,
             target: Union[str, Expression],
             on: Optional[Condition] = None, using: Union[Expression, tuple[str, ...]] = None,
             natural=False):
        available = (on is not None) + (using is not None) + natural
        if available == 0:
            raise ValueError("No conditions given for Query Join")
        if available > 1:
            raise ValueError("Exactly one join format must be given for Query Join")

        sections: list[tuple[sql.Composable, tuple[Any, ...]]] = [(sql.SQL('JOIN'), ())]
        if isinstance(target, str):
            sections.append((sql.Identifier(target), ()))
        else:
            sections.append(target.as_tuple())

        if on is not None:
            sections.append((sql.SQL('ON'), ()))
            sections.append(on.as_tuple())
        elif using is not None:
            sections.append((sql.SQL('USING'), ()))
            if isinstance(using, Expression):
                sections.append(using.as_tuple())
            elif isinstance(using, tuple) and len(using) > 0 and isinstance(using[0], str):
                cols = sql.SQL("({})").format(sql.SQL(',').join(sql.Identifier(col) for col in using))
                sections.append((cols, ()))
            else:
                raise ValueError("Unrecognised 'using' type.")
        elif natural:
            sections.insert(0, (sql.SQL('NATURAL'), ()))

        expr = RawExpr.join_tuples(*sections)
        self._joins.append(expr)
        return self

    @property
    def _join_section(self) -> Optional[Expression]:
        if self._joins:
            return RawExpr.join(*self._joins)
        else:
            return None


class ExtraMixin(TableQuery[QueryResult]):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._extra: Optional[Expression] = None

    def extra(self, extra: sql.Composable, values: tuple[Any, ...] = ()):
        """
        Add an extra string, and optionally values, to this query.
        The extra string is inserted after any condition, and before the limit.
        """
        extra_expr = RawExpr(extra, values)
        if self._extra is not None:
            extra_expr = RawExpr.join(self._extra, extra_expr)
        self._extra = extra_expr
        return self

    @property
    def _extra_section(self) -> Optional[Expression]:
        if self._extra is None:
            return None
        else:
            return self._extra


class LimitMixin(TableQuery[QueryResult]):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._limit: Optional[int] = None

    def limit(self, limit: int):
        """
        Add a limit to this query.
        """
        self._limit = limit
        return self

    @property
    def _limit_section(self) -> Optional[Expression]:
        if self._limit is not None:
            return RawExpr(sql.SQL("LIMIT {}").format(sql.Placeholder()), (self._limit,))
        else:
            return None


class OrderMixin(TableQuery[QueryResult]):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._order: Optional[sql.Composable] = None

    def order_by(self, expression, direction=None, nulls=None):
        """
        Add a single sort expression to the query.
        This method stacks.
        """
        # TODO: Accept a ColumnExpression, string, or sql Composable
        # TODO: Enums for direction (ORDER.ASC or ORDER.DESC) and nulls (NULLS.FIRST, NULLS.LAST)
        ...

    @property
    def _order_section(self) -> Optional[Expression]:
        if self._order is not None:
            return RawExpr(sql.SQL("ORDER BY {}").format(self._order), ())
        else:
            return None


class Insert(ExtraMixin, TableQuery[QueryResult]):
    """
    Query type representing a table insert query.
    """
    # TODO: Support ON CONFLICT for upserts
    __slots__ = ('_columns', '_values')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._columns: tuple[str, ...] = ()
        self._values: tuple[tuple[Any, ...], ...] = ()

    def insert(self, columns, *values):
        """
        Insert the given data.

        Parameters
        ----------
        columns: tuple[str]
            Tuple of column names to insert.

        values: tuple[tuple[Any, ...], ...]
            Tuple of values to insert, corresponding to the columns.
        """
        if not values:
            raise ValueError("Cannot insert zero rows.")
        if len(values[0]) != len(columns):
            raise ValueError("Number of columns does not match length of values.")

        self._columns = columns
        self._values = values

    def build(self):
        columns = sql.SQL(',').join(map(sql.Identifier, self._columns))
        single_value_str = sql.SQL('({})').format(
            sql.SQL(',').join(sql.Placeholder() * len(self._columns))
        )
        values_str = sql.SQL(',').join(single_value_str * len(self._values))

        # TODO: Check efficiency of inserting multiple values like this
        # Also implement a Copy query
        base = sql.Sql("INSERT INTO {table} {columns} VALUES {values_str}").format(
            table=sql.Identifier(self.table),
            columns=columns,
            values_str=values_str
        )

        sections = [
            RawExpr(base),
            self._extra_section,
            RawExpr(sql.SQL('RETURNING *'))
        ]

        sections = (section for section in sections if section is not None)
        return RawExpr.join(*sections)


class Select(WhereMixin, ExtraMixin, OrderMixin, LimitMixin, JoinMixin, TableQuery[QueryResult]):
    """
    Select rows from a table matching provided conditions.
    """
    __slots__ = ('_columns',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._columns: tuple[Expression, ...] = ()

    def select(self, *columns: str, **exprs: Union[str, sql.Composable, Expression]):
        """
        Set the columns and expressions to select.
        If none are given, selects all columns.
        """
        cols: List[Expression] = []
        if columns:
            cols.extend(map(RawExpr, map(sql.Identifier, columns)))
        if exprs:
            for name, expr in exprs.items():
                if isinstance(expr, str):
                    cols.append(
                        RawExpr(sql.SQL(expr) + sql.SQL(' AS ') + sql.Identifier(name))
                    )
                elif isinstance(expr, sql.Composable):
                    cols.append(
                        RawExpr(expr + sql.SQL(' AS ') + sql.Identifier(name))
                    )
                elif isinstance(expr, Expression):
                    value_expr, value_values = expr.as_tuple()
                    cols.append(RawExpr(
                        value_expr + sql.SQL(' AS ') + sql.Identifier(name),
                        value_values
                    ))
        if cols:
            self._columns = (*self._columns, *cols)
        return self

    def build(self):
        if not self._columns:
            columns, columns_values = sql.SQL('*'), ()
        else:
            columns, columns_values = RawExpr.join(*self._columns, joiner=sql.SQL(',')).as_tuple()

        base = sql.SQL("SELECT {columns} FROM {table}").format(
            columns=columns,
            table=sql.Identifier(self.table)
        )

        sections = [
            RawExpr(base, columns_values),
            self._join_section,
            self._where_section,
            self._extra_section,
            self._order_section,
            self._limit_section,
        ]

        sections = (section for section in sections if section is not None)
        return RawExpr.join(*sections)


class Delete(WhereMixin, ExtraMixin, TableQuery[QueryResult]):
    """
    Query type representing a table delete query.
    """
    # TODO: Cascade option for delete, maybe other options
    # TODO: Require a where unless specifically disabled, for safety

    def build(self):
        base = sql.SQL("DELETE FROM {table}").format(
            table=sql.Identifier(self.table),
        )
        sections = [
            RawExpr(base),
            self._where_section,
            self._extra_section,
            RawExpr(sql.SQL('RETURNING *'))
        ]

        sections = (section for section in sections if section is not None)
        return RawExpr.join(*sections)


class Update(LimitMixin, WhereMixin, ExtraMixin, TableQuery[QueryResult]):
    __slots__ = (
        '_set',
    )
    # TODO: Again, require a where unless specifically disabled

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._set: List[Expression] = []

    def set(self, **column_values: Union[Any, Expression]):
        exprs: List[Expression] = []
        for name, value in column_values.items():
            if isinstance(value, Expression):
                value_tup = value.as_tuple()
            else:
                value_tup = (sql.Placeholder(), value)

            exprs.append(
                RawExpr.join_tuples(
                    (sql.Identifier(name), ()),
                    value_tup,
                    joiner=sql.SQL(' = ')
                )
            )
        self._set.extend(exprs)
        return self

    def build(self):
        if not self._set:
            raise ValueError("No columns provided to update.")
        set_expr, set_values = RawExpr.join(*self._set, joiner=sql.SQL(', ')).as_tuple()

        base = sql.SQL("UPDATE {table} SET {set}").format(
            table=sql.Identifier(self.table),
            set=set_expr
        )
        sections = [
            RawExpr(base, set_values),
            self._where_section,
            self._extra_section,
            self._limit_section,
            RawExpr(sql.SQL('RETURNING *'))
        ]

        sections = (section for section in sections if section is not None)
        return RawExpr.join(*sections)


# async def upsert(cursor, table, constraint, **values):
#     """
#     Insert or on conflict update.
#     """
#     valuedict = values
#     keys, values = zip(*values.items())
#
#     key_str = _format_insertkeys(keys)
#     value_str, values = _format_insertvalues(values)
#     update_key_str, update_key_values = _format_updatestr(valuedict)
#
#     if not isinstance(constraint, str):
#         constraint = ", ".join(constraint)
#
#     await cursor.execute(
#         'INSERT INTO {} {} VALUES {} ON CONFLICT({}) DO UPDATE SET {} RETURNING *'.format(
#             table, key_str, value_str, constraint, update_key_str
#         ),
#         tuple((*values, *update_key_values))
#     )
#     return await cursor.fetchone()


# def update_many(table, *values, set_keys=None, where_keys=None, cast_row=None, cursor=None):
#     cursor = cursor or conn.cursor()
#
#     # TODO: executemany or copy syntax now
#     return execute_values(
#         cursor,
#         """
#         UPDATE {table}
#         SET {set_clause}
#         FROM (VALUES {cast_row}%s)
#         AS {temp_table}
#         WHERE {where_clause}
#         RETURNING *
#         """.format(
#             table=table,
#             set_clause=', '.join("{0} = _t.{0}".format(key) for key in set_keys),
#             cast_row=cast_row + ',' if cast_row else '',
#             where_clause=' AND '.join("{1}.{0} = _t.{0}".format(key, table) for key in where_keys),
#             temp_table="_t ({})".format(', '.join(set_keys + where_keys))
#         ),
#         values,
#         fetch=True
#     )
