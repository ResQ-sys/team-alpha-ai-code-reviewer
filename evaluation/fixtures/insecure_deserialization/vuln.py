"""Insecure deserialization fixture (CWE-502).

``pickle.loads`` is called on caller-supplied bytes. Unpickling untrusted data
can execute arbitrary code during object reconstruction.
"""
import pickle


def load_session(blob):
    # BAD: unpickling untrusted bytes can lead to remote code execution.
    return pickle.loads(blob)
