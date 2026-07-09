"""OS command injection fixture (CWE-78).

Untrusted ``host`` is concatenated into a shell command handed to
``os.system`` — an attacker-controlled value can inject arbitrary commands.
"""
import os


def ping(host):
    # BAD: host concatenated straight into the shell command line.
    os.system("ping -c 1 " + host)
