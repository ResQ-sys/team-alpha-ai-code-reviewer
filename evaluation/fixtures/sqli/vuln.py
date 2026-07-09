"""SQL injection fixture (CWE-89).

The query is built with %-string formatting from untrusted input and passed
straight to ``cursor.execute`` — a parameterized query should be used instead.
"""


def get_user(cursor, user_id):
    # BAD: user_id interpolated directly into the SQL string.
    cursor.execute("SELECT * FROM users WHERE id = '%s'" % user_id)
    return cursor.fetchone()
