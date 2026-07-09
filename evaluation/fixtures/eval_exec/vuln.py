"""Dangerous eval fixture (CWE-95).

``eval`` is invoked on a string read from the user, allowing arbitrary Python
expression (and therefore code) execution.
"""


def compute(expression_from_user):
    # BAD: eval on untrusted input allows arbitrary code execution.
    return eval(expression_from_user)
