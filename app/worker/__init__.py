"""The billing worker (stage 4, docs/stage4-design.md): everything time-driven - the daily
billing run, the replay of missed dates, the status flips and the UI's requests.

A second process from the app image. It owns billing through a session advisory lock on
its one database connection (db.py), decides what a billing run does for a date
(runs.py), and reads time only through a Clock (runtime.py).
"""
