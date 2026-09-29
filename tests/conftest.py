import os
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote

import pytest


@pytest.fixture(params=['sqlite'] + (['postgres'] if os.getenv('TEST_DATABASE_URL') else []))
def database(request, tmp_path):
    if request.param == 'sqlite':
        yield str(tmp_path / 'test.sqlite3')
        return
    import psycopg
    from psycopg import sql
    url = os.environ['TEST_DATABASE_URL']
    schema = 'test_' + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query['options'] = '-c search_path=' + schema
    try:
        # libpq URIs decode %20 for spaces, not form-encoding's plus sign.
        yield urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
