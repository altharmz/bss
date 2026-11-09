"""Load-test tooling (#281-#289): the dataset generator (`python -m loadtest.seed`).

Not part of the runtime image: it runs only in the local load-test stack
(compose.loadtest.yaml, docs/loadtest.md) and refuses any APP_ENV but `test`.
"""
