"""Pytest bootstrap for the src/ layout.

The application source lives under src/ (agents/, utils/, config.py, graph.py,
...). Adding src/ to sys.path here lets the tests in src/tests keep their
`from agents...` / `from graph import ...` imports without any per-test hackery.
Run the suite from the repo root with:  pytest src/tests
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
