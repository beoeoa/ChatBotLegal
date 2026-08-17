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

# Unit and contract tests must never inherit credentials from the developer's
# runtime .env.  A live integration job can opt in explicitly and must point at
# an isolated namespace/database rather than the local application database.
if os.getenv("CHATBOTLEGAL_TEST_LOAD_DOTENV", "").strip().casefold() in {
    "1",
    "true",
    "yes",
    "on",
}:
    from dotenv import load_dotenv

    dotenv_path = Path(__file__).parent.parent / ".env.test"
    if not dotenv_path.exists():
        raise RuntimeError(
            "CHATBOTLEGAL_TEST_LOAD_DOTENV requires an isolated .env.test file"
        )
    load_dotenv(dotenv_path, override=False)

# Test clients exercise routers with mocked persistence. A developer .env may
# contain real login credentials, which must not turn those unit tests into
# accidental authentication integration tests. Individual auth tests set their
# own credentials/session fixtures explicitly.
os.environ["OPEN_NOTEBOOK_PASSWORD"] = ""
os.environ["OPEN_NOTEBOOK_CITIZEN_PASSWORD"] = ""
os.environ["OPEN_NOTEBOOK_OFFICER_PASSWORD"] = ""
os.environ["OPEN_NOTEBOOK_ADMIN_PASSWORD"] = ""

# Keep unit/contract tests independent from feature flags in the developer's
# local .env.  api.main loads that file during import; pre-seeding rollback
# defaults here prevents load_dotenv() from silently routing legacy tests into
# V2/section orchestration. Tests for those paths opt in with monkeypatch.
os.environ["LEGAL_SECTION_GROUNDING_ENABLED"] = "false"
os.environ["LEGAL_ANSWER_PIPELINE_V2_ENABLED"] = "false"
os.environ["LEGAL_ANSWER_OPTIMIZED_PROFILE_ENABLED"] = "false"
os.environ["LEGAL_ANSWER_OPTIMIZED_PROFILE_ENFORCED"] = "false"
os.environ["LEGAL_PROBLEM_MAP_LLM_ENABLED"] = "false"
os.environ["FORM_GOVERNANCE_ROUTER_MODE"] = "disabled"
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
