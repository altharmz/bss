"""Data access for the BSS domain.

Rules for everything in this package:
- functions take an open psycopg.Connection and never commit or roll back - the caller
  owns the transaction;
- nothing reads the clock: every time-dependent function takes `now`/`today`/a date;
- money is Decimal (numeric(12,2) in the DB), never float;
- ranges are psycopg Range objects, half-open [lower, upper); upper=None means open-ended;
- rows come back as dicts keyed by column name.
"""
