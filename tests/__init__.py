"""VoiceType test package.

The __init__.py makes `tests` a proper package so `from tests.x import ...`
resolves on every OS, Python, and pytest invocation (console script or
`python -m`), independent of CWD/sys.path quirks. Without it, CI runners
failed collection with `ModuleNotFoundError: No module named 'tests'`.
"""
