"""Hard-coded secret fixture (CWE-798 / CWE-259).

An API key is embedded as a string literal in source. Secrets should be loaded
from the environment or a secret manager, never committed to the codebase.
"""

# BAD: hard-coded credential committed to source control.
API_KEY = "sk_live_9f8a7b6c5d4e3f2a1b0c"


def client_headers():
    return {"Authorization": "Bearer " + API_KEY}
