"""
Pytest configuration file.

This file ensures that the project root is in the Python path,
allowing tests to import from the api and open_notebook modules.
"""

import os
import sys
from pathlib import Path

# Ensure password auth is disabled for tests BEFORE any imports
# The PasswordAuthMiddleware skips auth when this env var is not set
# Set to empty string instead of deleting to prevent it from being reloaded
os.environ["OPEN_NOTEBOOK_PASSWORD"] = ""

# Load environment variables from .env file
# This must be done BEFORE any imports that depend on environment variables
from dotenv import load_dotenv

# Load .env file from project root
dotenv_path = Path(__file__).parent.parent / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
    print(f"Loaded environment variables from {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}")

# Test clients exercise routers with mocked persistence. A developer .env may
# contain real login credentials, which must not turn those unit tests into
# accidental authentication integration tests. Individual auth tests set their
# own credentials/session fixtures explicitly.
os.environ["OPEN_NOTEBOOK_PASSWORD"] = ""
os.environ["OPEN_NOTEBOOK_CITIZEN_PASSWORD"] = ""
os.environ["OPEN_NOTEBOOK_OFFICER_PASSWORD"] = ""
os.environ["OPEN_NOTEBOOK_ADMIN_PASSWORD"] = ""
for _secret_file_var in (
    "OPEN_NOTEBOOK_PASSWORD_FILE",
    "OPEN_NOTEBOOK_CITIZEN_PASSWORD_FILE",
    "OPEN_NOTEBOOK_OFFICER_PASSWORD_FILE",
    "OPEN_NOTEBOOK_ADMIN_PASSWORD_FILE",
):
    os.environ.pop(_secret_file_var, None)

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
