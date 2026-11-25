"""make coverage (#480): start coverage.py in every Python process of the backend suite.

On PYTHONPATH only for the coverage run; process_startup() does nothing unless
COVERAGE_PROCESS_START names the rc file.
"""

import coverage

coverage.process_startup()
