"""
Additional intentionally vulnerable sample code for demo purposes.
"""
import os


def run_diagnostic(hostname):
    """CWE-78: OS command injection via unsanitized shell string."""
    os.system("ping -c 1 " + hostname)


def evaluate_expression(user_expr):
    """CWE-95 / dangerous eval of untrusted input."""
    return eval(user_expr)


def render_greeting(name):
    """CWE-79: reflected XSS-style pattern — untrusted input concatenated
    directly into HTML without escaping."""
    html = "<div>Hello, " + name + "!</div>"
    return html
