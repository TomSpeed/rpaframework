# pylint: disable=too-many-lines
import copy
import csv
import keyword
import logging
import re
from collections import OrderedDict, namedtuple

from itertools import groupby, zip_longest
from numbers import Number
from operator import itemgetter

from RPA.core.types import is_dict_like, is_list_like, is_namedtuple


def to_list(obj, size=1):
    """Convert (possibly scalar) value to list of `size`."""
    if not is_list_like(obj):
        return [obj] * int(size)
    else:
        return obj


def to_identifier(val):
    """Convert string to valid identifier"""
    val = str(val).strip()
    # Replaces spaces, dashes, and slashes to underscores
    val = re.sub(r"[\s\-/\\]", "_", val)
    # Remove remaining invalid characters
    val = re.sub(r"[^0-9a-zA-Z_]", "", val)
    # Identifier can't start with digits
    val = re.sub(r"^[^a-zA-Z_]+", "", val)

    if not val or keyword.iskeyword(val):
        raise ValueError(f"Unable to convert to identifier: {val}")

    return val


def if_none(value, default):
    """Return default if value is None."""
    return value if value is not None else default


class Table:
    """Container class for tabular data.

    Supported data formats:

    - empty: None values populated according to columns/index
    - list: List of objects that contain values, which themselves
      can be namedtuples, dictionaries, lists, or tuples
    - dict: Dictionary of columns as keys and rows as values

    .. todo:: Integers as column names? Columns forced to strings?
    .. todo:: Implement column slicing
    .. todo:: Existing column to index conversion
    .. todo:: Index accessing through dot notation?
    .. todo:: Index name conflict in exports/imports
    .. todo:: Return Robot Framework DotDict instead of dict?

    :param data:     values for table,  see "Supported data formats"
    :param columns:  names for columns, should match data dimensions
    :param index:    names for rows,    should match data dimensions
    """

    def __init__(self, data=None, columns=None, index=None):
        self._data = []
        self._columns = []
        self._index = []

        # Use public setters to validate data
        if columns is not None:
            self.columns = list(columns)
        if index is not None:
            self.index = list(index)

        if not data:
            self._init_empty()
        elif isinstance(data, Table):
            self._init_table(data)
        elif is_dict_like(data):
            self._init_dict(data)
        elif is_list_like(data):
            self._init_list(data)
        else:
            raise TypeError("Not a valid input format")

        if columns:
            self._sort_columns(columns)

        self._validate_self()

    def _init_empty(self):
        """Initialize table with empty data."""
        self._data = [[None for _ in self._columns] for _ in self._index]

    def _init_table(self, table):
        """Initialize table with another table."""
        if not self.columns:
            self.columns = table.columns
        if not self.index:
            self.index = table.index
        self._data = table.data

    def _init_list(self, data):
        """Initialize table from list-like container."""
        # Assume data is homogenous in regard to type
        obj = data[0]
        column_names = self._column_name_getter(obj)
        column_values = self._column_value_getter(obj)

        # Do not update columns or index if predefined
        add_columns = not bool(self._columns)
        add_index = not bool(self._index)

        for idx, obj in enumerate(data):
            row = [None] * len(self._columns)

            for column in column_names(obj):
                # Dictionaries and namedtuples can
                # contain unknown columns
                if column not in self._columns:
                    if not add_columns:
                        continue

                    self._add_column(column)
                    while len(row) < len(self._columns):
                        row.append(None)

                col = self.column_location(column)
                row[col] = column_values(obj, column)

            self._data.append(row)

            # Generate range-based index if not predefined
            if add_index:
                self._index.append(idx)

    def _init_dict(self, data):
        """Initialize table from dict-like container."""
        if not self._columns:
            self._columns = list(data.keys())

        # Filter values by defined columns
        columns = (
            to_list(values)
            for column, values in data.items()
            if column in self._columns
        )

        # Convert columns to rows
        self._data = [list(row) for row in zip_longest(*columns)]

        self._index = self._index or list(range(len(self._data)))

    def __repr__(self):
        return "Table(columns={}, rows={})".format(self.columns, len(self))

    def __len__(self):
        return len(self._index)

    def __iter__(self):
        return self.iter_dicts(with_index=False)

    def __eq__(self, other):
        if not isinstance(other, Table):
            return NotImplemented
        return (
            self._index == other._index
            and self._columns == other._columns
            and self._data == other._data
        )

    @property
    def data(self):
        return self._data.copy()

    @property
    def dimensions(self):
        return len(self._index), len(self._columns)

    @property
    def columns(self):
        return self._columns.copy()

    @columns.setter
    def columns(self, names):
        """Rename columns with given values."""
        self._validate_columns(names)
        self._columns = list(names)

    def _validate_columns(self, names):
        """Validate that given column names can be used."""
        if not is_list_like(names):
            raise ValueError("Columns should be list-like")

        if len(set(names)) != len(names):
            raise ValueError("Duplicate column names")

        if self._data and len(names) != len(self._data[0]):
            raise ValueError("Invalid columns length")

    def _column_name_getter(self, obj):
        """Create callable that returns column names for given obj types."""
        if is_namedtuple(obj):
            # Use namedtuple fields as columns
            def get(obj):
                return list(obj._fields)

        elif is_dict_like(obj):
            # Use dictionary keys as columns
            def get(obj):
                return list(obj.keys())

        elif is_list_like(obj):
            # Use either predefined columns, or
            # generate range-based column values
            predefined = list(self._columns)

            def get(obj):
                count = len(obj)
                if predefined:
                    if count > len(predefined):
                        raise ValueError(f"Data had more than defined {count} columns")
                    return predefined[:count]
                else:
                    return list(range(count))

        else:
            # Fallback to single column
            def get(_):
                return self._columns[:1] if self._columns else [0]

        return get

    def _column_value_getter(self, obj):
        """Create callable that returns column values for given object types."""
        if is_namedtuple(obj):
            # Get values using properties
            def get(obj, column):
                return getattr(obj, column, None)

        elif is_dict_like(obj):
            # Get values using dictionary keys
            def get(obj, column):
                return obj.get(column)

        elif is_list_like(obj):
            # Get values using list indexes
            def get(obj, column):
                col = self.column_location(column)
                try:
                    return obj[col]
                except IndexError:
                    return None

        else:
            # Fallback to single column
            def get(obj, _):
                return obj

        return get

    def _sort_columns(self, order):
        """Sort columns to match given order."""
        unknown = set(self._columns) - set(order)
        if unknown:
            names = ", ".join(str(name) for name in unknown)
            raise ValueError(f"Unknown columns: {names}")

        cols = [self.column_location(column) for column in order]

        self._columns = [self._columns[col] for col in cols]
        self._data = [[row[col] for col in cols] for row in self._data]

    @property
    def index(self):
        return self._index.copy()

    @index.setter
    def index(self, names):
        """Renames index with given values."""
        self._validate_index(names)
        self._index = list(names)

    def _validate_index(self, names):
        """Validate that given index names can be used."""
        if not is_list_like(names):
            raise ValueError("Index should be list-like")

        if len(set(names)) != len(names):
            raise ValueError("Duplicate index names")

        if self._data and len(names) != len(self._data):
            raise ValueError("Invalid index length")

    def _validate_self(self):
        """Validate that internal data is valid and coherent."""
        self._validate_columns(self._columns)
        self._validate_index(self._index)

        head = self._data[0] if self._data else []
        if len(head) != len(self._columns):
            raise ValueError("Columns length does not match data")

        if len(self._data) != len(self._index):
            raise ValueError("Index length does not match data")

    def column_location(self, value):
        return self._location("column", self._columns, value)

    def index_location(self, value):
        return self._location("index", self._index, value)

    @staticmethod
    def _location(name, container, value):
        """Find location for index/column value."""
        # Directly indexing columns
        if isinstance(value, int):
            if value in container:
                location = container.index(value)
            elif value < 0:
                location = value + len(container)
            else:
                location = value

            if location >= len(container):
                raise IndexError(f"{name.title()} out of of range")

        # Finding index by name
        else:
            try:
                location = container.index(value)
            except ValueError:
                raise ValueError(f"Unknown {name} name: {value}")

        return location

    def __getitem__(self, key):
        """Helper method for accessing items in the Table.

        Examples:
            table[:10]              First 10 rows
            table[0,1]              Value in first row and second column
            table[2:10,"email"]     Values in "email" column for rows 3 to 11
        """
        # Both row index and columns given
        if isinstance(key, tuple):
            index, column = key
            index = self._slice_index(index) if isinstance(index, slice) else index
            return self.get(indexes=index, columns=column, as_list=True)
        # Row indexed with slice, all columns
        elif isinstance(key, slice):
            return self.get(indexes=self._slice_index(key), as_list=True)
        # Single row
        else:
            return self.get(indexes=key, as_list=True)

    def __setitem__(self, key, value):
        """Helper method for setting items in the Table.

        Examples:
            table[5]  = ["Mikko", "Mallikas"]
            table[:2] = [["Marko", "Markonen"], ["Pentti", "Penttinen"]]
        """
        # Both row index and columns given
        if isinstance(key, tuple):
            index, column = key
            index = self._slice_index(index) if isinstance(index, slice) else index
            return self.set(indexes=index, columns=column, values=value)
        # Row indexed with slice, all columns
        elif isinstance(key, slice):
            return self.set(indexes=self._slice_index(key), values=value)
        # Single row
        else:
            return self.set(indexes=key, values=value)

    def _slice_index(self, slicer):
        """Create list of index values from slice object."""
        if slicer.start is None:
            start = 0
        else:
            try:
                start = self.index_location(slicer.start)
            except ValueError:
                raise IndexError("Start of slice not in index")

        if slicer.stop is None:
            end = len(self._index)
        else:
            try:
                end = self.index_location(slicer.stop)
            except ValueError:
                raise IndexError("End of slice not in index")

        if start > end:
            raise IndexError("Start of slice after end of slice")

        return list(range(start, end))

    def copy(self):
        """Create a copy of this table."""
        return copy.deepcopy(self)

    def clear(self):
        """Remove all rows from this table."""
        self.delete_rows(self.index)

    def head(self, rows, as_list=False):
        """Return first n rows of table."""
        indexes = self._index[: int(rows)]
        return self.get_table(indexes, as_list=as_list)

    def tail(self, rows, as_list=False):
        """Return last n rows of table."""
        indexes = self._index[-int(rows) :]
        return self.get_table(indexes, as_list=as_list)

    def get(self, indexes=None, columns=None, as_list=False):
        """Get values from table. Return type depends on input dimensions.

        If `indexes` and `columns` are scalar, i.e. not lists:
            Returns single cell value

        If either `indexes` or `columns` is a list:
            Returns matching row or column

        If both `indexes` and `columns` are lists:
            Returns a new Table instance with matching cell values

        :param indexes: list of indexes, or all if not given
        :param columns: list of columns, or all if not given
        """
        indexes = if_none(indexes, self._index)
        columns = if_none(columns, self._columns)

        if is_list_like(indexes) and is_list_like(columns):
            return self.get_table(indexes, columns, as_list)
        elif not is_list_like(indexes) and is_list_like(columns):
            return self.get_row(indexes, columns, as_list)
        elif is_list_like(indexes) and not is_list_like(columns):
            return self.get_column(columns, indexes, as_list)
        else:
            return self.get_cell(indexes, columns)

    def get_cell(self, index, column):
        """Get single cell value."""
        idx = self.index_location(index)
        col = self.column_location(column)
        return self._data[idx][col]

    def get_row(self, index, columns=None, as_list=False):
        """Get column values from row.

        :param index:   index for row
        :param columns: column names to include, or all if not given
        :param as_list: return row as dictionary, instead of list
        """
        columns = if_none(columns, self._columns)
        idx = self.index_location(index)

        if as_list:
            row = []
            for column in columns:
                col = self.column_location(column)
                row.append(self._data[idx][col])
            return row
        else:
            row = {}
            for column in columns:
                col = self.column_location(column)
                row[self._columns[col]] = self._data[idx][col]
            return row

    def get_column(self, column, indexes=None, as_list=False):
        """Get row values from column.

        :param columns: name for column
        :param indexes: row indexes to include, or all if not given
        :param as_list: return column as dictionary, instead of list
        """
        indexes = if_none(indexes, self._index)
        col = self.column_location(column)

        if as_list:
            column = []
            for index in indexes:
                idx = self.index_location(index)
                column.append(self._data[idx][col])
            return column
        else:
            column = {}
            for index in indexes:
                idx = self.index_location(index)
                column[self._index[idx]] = self._data[idx][col]
            return column

    def get_table(self, indexes=None, columns=None, as_list=False):
        """Get a new table from all cells matching indexes and columns."""
        indexes = if_none(indexes, self._index)
        columns = if_none(columns, self._columns)

        if indexes == self._index and columns == self._columns:
            return self.copy()

        idxs = [self.index_location(index) for index in indexes]
        cols = [self.column_location(column) for column in columns]
        data = [[self._data[idx][col] for col in cols] for idx in idxs]

        if as_list:
            return data
        else:
            return Table(data=data, index=indexes, columns=columns)

    def get_slice(self, start=None, end=None):
        """Get a new table from rows between start and end index."""
        start = self.index_location(start) if start is not None else 0
        end = self.index_location(end) if end is not None else len(self._index)

        if start > end:
            raise ValueError("Start index after end index")

        index = self._index[start : end + 1]
        return self.get_table(index, self._columns)

    def _add_row(self, index):
        """Add a new empty row into the table."""
        if index is None:
            index = len(self._index)

        if index in self._index:
            raise ValueError(f"Duplicate row index: {index}")

        if isinstance(index, int):
            assert index >= len(self._index)
            for empty in range(len(self._index), index):
                self._add_row(empty)

        self._index.append(index)
        self._data.append([None] * len(self._columns))

        return len(self._index) - 1

    def _add_column(self, column):
        """Add a new empty column into the table."""
        if column is None:
            column = len(self._columns)

        if column in self._columns:
            raise ValueError(f"Duplicate column name: {column}")

        if isinstance(column, int):
            assert column >= len(self._columns)
            for empty in range(len(self._columns), column):
                self._add_column(empty)

        self._columns.append(column)
        for idx, _ in enumerate(self._index):
            row = self._data[idx]
            row.append(None)

        return len(self._columns) - 1

    def set(self, indexes=None, columns=None, values=None):
        """Sets multiple cell values at a time.

        Both `indexes` and `columns` can be scalar or list-like,
        which enables setting individual cells, rows/columns, or regions.

        If `values` is scalar, all matching cells will be set to that value.
        Otherwise the length should match the cell count defined by the
        other parameters.
        """
        indexes = to_list(if_none(indexes, self._index))
        columns = to_list(if_none(columns, self._columns))

        size = len(indexes) + len(columns)
        values = to_list(values, size=size)
        if not len(values) == size:
            raise ValueError("Values size does not match indexes and columns")

        for index in indexes:
            idx = self.index_location(index)
            for column in columns:
                col = self.column_location(column)
                self.set_cell(index, column, values[idx + col])

    def set_cell(self, index, column, value):
        """Set individual cell value.
        If either index or column is missing, they are created.
        """
        try:
            idx = self.index_location(index)
        except (IndexError, ValueError):
            idx = self._add_row(index)

        try:
            col = self.column_location(column)
        except (IndexError, ValueError):
            col = self._add_column(column)

        self._data[idx][col] = value

    def set_row(self, index, values):
        """Set values in row. If index is missing, it is created."""
        try:
            idx = self.index_location(index)
        except ValueError:
            idx = self._add_row(index)

        column_values = self._column_value_getter(values)
        row = [column_values(values, column) for column in self._columns]

        self._data[idx] = row

    def set_column(self, column, values):
        """Set values in column. If column is missing, it is created."""
        values = to_list(values, size=len(self._index))

        if len(values) != len(self._index):
            raise ValueError("Index and values lengths should match")

        if column not in self._columns:
            self._add_column(column)

        for index in self._index:
            idx = self.index_location(index)
            self.set_cell(index, column, values[idx])

    def append_row(self, row=None, index=None):
        """Append new row to table."""
        if index is not None and index in self._index:
            raise IndexError(f"Index already exists: {index}")

        self.set_row(index, row)

    def append_rows(self, rows, indexes=None):
        """Append multiple rows to table."""
        if indexes is not None and len(indexes) > len(rows):
            raise ValueError("Index length longer than data")

        if indexes is None:
            indexes = []

        for row, index in zip_longest(rows, indexes):
            self.append_row(row, index)

    def append_column(self, column=None, values=None):
        if column is not None and column in self._columns:
            raise ValueError(f"Column already exists: {column}")

        self.set_column(column, values)

    def delete_rows(self, indexes):
        """Remove rows with matching indexes."""
        indexes = to_list(indexes)

        unknown = set(indexes) - set(self._index)
        if unknown:
            names = ", ".join(str(name) for name in unknown)
            raise ValueError(f"Unable to remove unknown rows: {names}")

        for index in indexes:
            idx = self.index_location(index)
            del self._index[idx]
            del self._data[idx]

    def delete_columns(self, columns):
        """Remove columns with matching names."""
        columns = to_list(columns)

        unknown = set(columns) - set(self._columns)
        if unknown:
            names = ", ".join(str(name) for name in unknown)
            raise ValueError(f"Unable to remove unknown columns: {names}")

        for column in columns:
            col = self.column_location(column)
            for index in self.index:
                idx = self.index_location(index)
                del self._data[idx][col]
            del self._columns[col]

        # All data has been removed
        if not self._columns:
            self._index = []

    def append_table(self, table):
        """Append data from table to current data."""
        if not table:
            return

        indexes = []
        for idx, index in enumerate(table.index):
            if isinstance(index, int):
                index = len(self) + idx
            elif index in self._index:
                raise ValueError(f"Duplicate index name: {index}")
            indexes.append(index)

        self.set(indexes=indexes, columns=table.columns, values=table.data)

    def reset_index(self, drop=False):
        """Remove all named row indexes and use range-based values."""
        if not drop:
            self.append_column(column="index", values=self._index)

        self.index = list(range(len(self)))

    def sort_by_index(self, ascending=False):
        """Sort table by index values."""
        self._sort_by(self.index, reverse=not ascending)

    def sort_by_column(self, columns, ascending=False):
        """Sort table by columns."""
        columns = to_list(columns)

        # Create sort criteria list, with each row as tuple of column values
        values = (self.get_column(column, as_list=True) for column in columns)
        values = list(zip(*values))

        self._sort_by(values, reverse=not ascending)

    def _sort_by(self, values, reverse=False):
        """Sort index and data by using `values` as sorting criteria."""
        assert is_list_like(values)
        assert len(values) == len(self._data)

        def sorter(row):
            """Sort table by given values, while allowing for disparate types.
            Order priority:
                - Values by typename
                - Numeric types
                - None values
            """
            criteria = []
            for value in row[1]:  # Ignore enumeration
                criteria.append(
                    (
                        value is not None,
                        "" if isinstance(value, Number) else type(value).__name__,
                        value,
                    )
                )
            return criteria

        # Store original index order using enumerate() before sort, and
        # use it to sort index/data later
        values = sorted(enumerate(values), key=sorter, reverse=reverse)
        idxs = [value[0] for value in values]

        # Re-order index, and update range-based values
        indexes = []
        for idx_new, idx_old in enumerate(idxs):
            index = self._index[idx_old]
            if isinstance(index, int):
                indexes.append(idx_new)
            else:
                indexes.append(index)

        # Re-order data
        self._data = [self._data[idx] for idx in idxs]

    def group_by_column(self, column):
        """Group rows by column value and return as list of tables."""
        # TODO: Ensure original index is maintained?
        ref = self.copy()
        ref.sort_by_column(column)

        col = self.column_location(column)
        groups = groupby(ref.data, itemgetter(col))

        result = []
        ref.clear()
        for _, group in groups:
            table = ref.copy()
            table.append_rows(group)
            result.append(table)

        return result

    def filter_by_column(self, column, condition):
        """Remove rows by evaluating `condition` for all `column`
        values. All rows where it evaluates to falsy are removed.
        """
        filtered = []
        for index in self.index:
            value = self.get_cell(index, column)
            if not condition(value):
                filtered.append(index)

        self.delete_rows(filtered)

    def iter_lists(self, with_index=True):
        """Iterate rows with values as lists."""
        for idx, row in zip(self._index, self._data):
            if with_index:
                yield idx, list(row)
            else:
                yield list(row)

    def iter_dicts(self, with_index=True):
        """Iterate rows with values as dicts."""
        for index in self._index:
            row = {"index": index} if with_index else {}
            for column in self._columns:
                row[column] = self.get_cell(index, column)
            yield row

    def iter_tuples(self, with_index=True, name="Row"):
        """Iterate rows with values as namedtuples.
        Converts column names to valid Python identifiers,
        e.g. "First Name" -> "First_Name"
        """
        columns = {column: to_identifier(column) for column in self._columns}

        fields = ["index"] if with_index else []
        fields.extend(columns.values())

        container = namedtuple(name, fields)
        for row in self.iter_dicts(with_index):
            row = {columns[k]: v for k, v in row.items()}
            yield container(**row)

    def to_list(self, with_index=True):
        """Convert table to list representation."""
        export = []

        for index in self._index:
            row = OrderedDict()
            if with_index:
                row["index"] = index
            for column in self._columns:
                row[column] = self.get_cell(index, column)
            export.append(row)

        return export

    def to_dict(self, with_index=True):
        """Convert table to dict representation."""
        export = OrderedDict()

        if with_index:
            export["index"] = self.index

        for column in self._columns:
            export[column] = []

        for index in self._index:
            for column in self._columns:
                value = self.get_cell(index, column)
                export[column].append(value)

        return export


class Tables:
    """Robot Framework library for manipulating tabular data."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def requires_table(obj):
        if not isinstance(obj, Table):
            raise TypeError("Keyword requires Table object")

    def create_table(self, data=None):
        """Create Table object from data.

        Data can be a combination of various iterable containers, e.g.
        list of lists, list of dicts, dict of lists.

        :param data:    source data for table
        """
        table = Table(data)
        self.logger.info("Created table: %s", table)
        return table

    def export_table(self, table, with_index=False, as_list=True):
        """Convert table object to standard Python containers.

        :param table:       table to convert to dict
        :param with_index:  include index in values
        :param as_list:     export data as list instead of dict
        """
        self.requires_table(table)
        if as_list:
            return table.to_list(with_index)
        else:
            return table.to_dict(with_index)

    def copy_table(self, table):
        """Copy table object.

        :param table:   table to copy
        """
        self.requires_table(table)
        return table.copy()

    def clear_table(self, table):
        """Clear table in-place, but keep columns.

        :param table:   table to clear
        """
        self.requires_table(table)
        table.clear()

    def get_table_dimensions(self, table):
        """Return table dimensions, as (rows, columns).

        :param table:    table to inspect
        """
        self.requires_table(table)
        return table.dimensions

    def rename_table_columns(self, table, columns, strict=False):
        """Renames columns in the Table with given values. Columns with
        name as `None` will be use previous value.

        :param table:   table to modify
        :param columns: list of new column names
        :param strict:  if True, raises ValueError if column lengths
                        do not match
        """
        self.requires_table(table)
        before = table.columns

        if strict and len(before) != len(columns):
            raise ValueError("Column lengths do not match")

        after = []
        for old, new in zip_longest(before, columns):
            if old is None:
                break
            elif new is None:
                after.append(old)
            else:
                after.append(new)

        table.columns = after

    def add_table_column(self, table, name=None, values=None):
        """Append a column to a table.

        :param table:   table to modify
        :param name:    name of new column
        :param values:  row values (or single scalar value for all rows)
        """
        self.requires_table(table)
        table.append_column(name, values)

    def add_table_row(self, table, row, index=None):
        """Append rows to a table.

        :param table:   table to modify
        :param row:     value for new row
        :param index:   index name for new row
        """
        self.requires_table(table)
        table.append_row(row, index=index)

    def get_table_row(self, table, index, as_list=False):
        """Get a single row from table.

        :param table:   table to read
        :param row:     row to read
        :param as_list: return list instead of dictionary
        """
        self.requires_table(table)
        return table.get_row(index, as_list=as_list)

    def get_table_column(self, table, column, as_list=False):
        """Get all column values from table.

        :param table:   table to read
        :param column:  column to read
        :param as_list: return list instead of dictionary
        """
        self.requires_table(table)
        return table.get_column(column, as_list=as_list)

    def set_table_row(self, table, row, values):
        """Assign values to a row in the table.

        :param table:   table to modify
        :param row:     row to modify
        :param values:  value(s) to set
        """
        self.requires_table(table)
        table.set_row(row, values)

    def set_table_column(self, table, column, values):
        """Assign values to entire column in the table.

        :param table:   table to modify
        :param column:  column to modify
        :param values:  value(s) to set
        """
        self.requires_table(table)
        table.set_column(column, values)

    def pop_table_row(self, table, index=None, as_list=False):
        """Remove row from table and return it.

        :param table:   table to modify
        :param index:   row index, pops first row if none given
        :param as_list: return list instead of dictionary
        """
        self.requires_table(table)
        index = if_none(index, table.index[0])

        values = table.get_row(table, index, as_list)
        table.delete_rows(index)
        return values

    def pop_table_column(self, table, column=None, as_list=False):
        """Remove column from table and return it.

        :param table:   table to modify
        :param column:  column to remove
        :param as_list: return list instead of dictionary
        """
        self.requires_table(table)
        column = if_none(column, table.columns[0])

        values = self.get_table_column(table, column, as_list)
        table.delete_columns(column)
        return values

    def get_table_slice(self, table, start=None, end=None):
        """Return a new Table from a subset of given Table rows.

        :param table:   table to read from
        :param start:   start index (inclusive)
        :param start:   end index (inclusive)
        """
        self.requires_table(table)
        return table.get_slice(start, end)

    def set_column_as_index(self, table, column=None):
        """Set existing column as index for rows.

        :param table:   table to modify
        :param column:  column to convert to index
        """
        values = self.pop_table_column(column)
        table.index = values

    def table_head(self, table, count=5, as_list=False):
        """Return first `count` rows from table.

        :param table:   table to read from
        :param count:   number of lines to read
        :param as_list: return list instead of Table
        """
        self.requires_table(table)
        return table.head(count, as_list)

    def table_tail(self, table, count=5, as_list=False):
        """Return last `count` rows from table.

        :param table:   table to read from
        :param count:   number of lines to read
        :param as_list: return list instead of Table
        """
        self.requires_table(table)
        return table.tail(count, as_list)

    def get_table_cell(self, table, row, column):
        """Get a cell value from table.

        :param table:   table to read from
        :param row:     row of cell
        :param column:  column of cell
        """
        self.requires_table(table)
        return table.get_cell(row, column)

    def set_table_cell(self, table, row, column, value):
        """Set a cell value in the table.

        :param table:   table to modify to
        :param row:     row of cell
        :param column:  column of cell
        :param value:   value to set
        """
        self.requires_table(table)
        table.set_cell(row, column, value)

    def sort_table_by_column(self, table, column, ascending=False):
        """Sort table in-place according to `column`.

        :param table:       table to sort
        :param column:      column to sort with
        :param ascending:   table sort order
        """
        self.requires_table(table)
        table.sort_by_column(column, ascending=ascending)

    def group_table_by_column(self, table, column):
        """Group table by `column` and return a list of grouped Tables.

        :param table:   table to use for grouping
        :param column:  column which is used as grouping criteria
        """
        self.requires_table(table)
        groups = table.group_by_column(column)
        self.logger.info("Found %s groups", len(groups))
        return groups

    def filter_table_by_column(self, table, column, operator, value):
        """Return all rows where the column values match the given condition.

        :param table:       table to filter
        :param column:      column to filter with
        :param operator:    filtering operator, e.g. >, <, ==, contains
        :param value:       value to compare column to (using operator)
        """
        self.requires_table(table)

        operator = str(operator).lower().strip()
        condition = {
            ">": lambda x: x > value,
            "<": lambda x: x < value,
            ">=": lambda x: x >= value,
            "<=": lambda x: x <= value,
            "==": lambda x: x == value,
            "!=": lambda x: x != value,
            "in": lambda x: x in value,
            "contains": lambda x: value in x,
        }.get(operator)

        if not condition:
            raise ValueError(f"Unknown operator: {operator}")

        self.logger.info("Rows before filtering: %s", len(table))
        table.filter_by_column(column, condition)
        self.logger.info("Rows after filtering: %s", len(table))

    def filter_empty_rows(self, table):
        """Remove all rows from the table which have only None values.

        :param table:   table to filter
        """
        self.requires_table(table)

        empty = []
        for idx, row in table.iter_lists():
            if all(value is None for value in row):
                empty.append(idx)

        table.delete_rows(empty)

    def trim_empty_rows(self, table):
        """Remove all rows from the end of the table
        which have only None values.

        :param table:    table to filter
        """
        self.requires_table(table)

        empty = []
        for idx in reversed(table.index):
            row = table[idx]
            if any(value is not None for value in row):
                break
            empty.append(idx)

        table.delete_rows(empty)

    def trim_column_names(self, table):
        """Remove all extraneous whitespace from column names."""
        self.requires_table(table)
        table.columns = [
            column.strip() if isinstance(column, str) else column
            for column in table.columns
        ]

    def read_table_from_csv(
        self, path, header=None, columns=None, dialect=None, delimiters=None
    ):
        """Read a CSV file as a table.

        By default attempts to deduce the CSV format and headers
        from a sample of the input file. If it's unable to determine
        the format automatically, the dialect and header will
        have to be defined manually.

        Valid ``dialect`` values are ``excel``, ``excel-tab``, and ``unix``,
        and ``header`` is boolean argument (``True``/``False``). Optionally a
        set of valid ``delimiters`` can be given as a string.

        The ``columns`` argument can be used to override the names of columns
        in the resulting table. The amount of columns must match the input
        data.

        :param path:        path to CSV file
        :param header:      CSV file includes header
        :param columns:     names of columns in resulting table
        :param dialect:     format of CSV file
        :param delimiters:  string of possible delimiters
        """
        sniffer = csv.Sniffer()
        with open(path, newline="") as fd:
            sample = fd.read(1024)

        if dialect is None:
            dialect = sniffer.sniff(sample, delimiters)
        if header is None:
            header = sniffer.has_header(sample)

        with open(path, newline="") as fd:
            if header:
                reader = csv.DictReader(fd, dialect=dialect)
            else:
                reader = csv.reader(fd, dialect=dialect)
            rows = list(reader)

        return Table(rows, columns)

    def write_table_to_csv(self, table, path, header=True, dialect="excel"):
        """Write a table as a CSV file.

        Valid ``dialect`` values are ``excel``, ``excel-tab``, and ``unix``.

        :param path:    path to write to
        :param table:   table to write
        :param header:  write columns as header to CSV file
        :param dialect: the format of output CSV
        """
        self.requires_table(table)

        with open(path, mode="w", newline="") as fd:
            writer = csv.DictWriter(fd, fieldnames=table.columns, dialect=dialect)

            if header:
                writer.writeheader()

            for row in table.iter_dicts(with_index=False):
                writer.writerow(row)
