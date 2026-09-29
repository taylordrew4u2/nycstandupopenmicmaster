"""Small adapter for the application's shared SQLite/PostgreSQL SQL subset."""
import re

# Transaction-scoped locks work with transaction-pooling database URLs (e.g. Neon).
WRITE_LOCK = 78321001
SCHEMA_LOCK = 78321002


class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


def parameters(sql):
    # Translate placeholders only outside SQL string literals. User data is always
    # passed separately to psycopg, never interpolated into the statement.
    return ''.join(part if i % 2 else part.replace('?', '%s')
                   for i, part in enumerate(re.split(r"('(?:''|[^'])*')", sql)))


class PostgresConnection:
    def __init__(self, url):
        import psycopg
        self.connection = psycopg.connect(url, row_factory=row_factory,
                                          connect_timeout=15, prepare_threshold=None)
        self.connection.execute("SET statement_timeout = '15s'")
        self.connection.execute("SET lock_timeout = '15s'")

    def execute(self, sql, args=None):
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            return self.connection.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK,))
        return self.connection.execute(parameters(sql), args)

    def executemany(self, sql, args):
        with self.connection.cursor() as cursor:
            cursor.executemany(parameters(sql), args)

    def executescript(self, sql):
        for statement in sql.split(';'):
            if not statement.strip() or statement.lstrip().upper().startswith('PRAGMA'):
                continue
            statement = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', statement)
            statement = re.sub(r'\bBLOB\b', 'BYTEA', statement)
            self.connection.execute(statement)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()
