from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_release_compose_exposes_only_tls_gateway():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    assert services["gateway"]["ports"] == ["443:443"]
    for name in ("frontend", "api", "legal_retrieval", "surrealdb", "legal_postgres"):
        assert "ports" not in services[name]
        assert services[name]["restart"] == "unless-stopped"


def test_release_api_uses_shared_runtime_catalog_path_and_fail_closed_flag():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["api"]["environment"]

    assert environment["OPEN_NOTEBOOK_DATA_DIR"] == "/app/data"
    assert environment["PRODUCTION_MODE"] == "true"
    assert environment["OPEN_NOTEBOOK_RETURN_RESET_TOKEN"] == "false"
    assert environment["LEGAL_SECTION_GROUNDING_ENABLED"].endswith(":-false}")


def test_release_api_uses_dedicated_admin_bootstrap_secret_not_shared_password():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["api"]["environment"]

    assert "OPEN_NOTEBOOK_PASSWORD" not in environment
    assert environment["OPEN_NOTEBOOK_ADMIN_PASSWORD"] == (
        "${OPEN_NOTEBOOK_ADMIN_PASSWORD}"
    )


def test_release_surreal_uses_fresh_named_volume_not_packaged_runtime_database():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")
    )
    volumes = compose["services"]["surrealdb"]["volumes"]

    assert volumes == ["surreal_data:/mydata"]
    assert "surreal_data" in compose["volumes"]


def test_gateway_enforces_internal_tls_and_security_headers():
    caddyfile = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text(encoding="utf-8")

    assert "tls internal" in caddyfile
    assert "Strict-Transport-Security" in caddyfile
    assert "Content-Security-Policy" in caddyfile
    assert "reverse_proxy api:5055" in caddyfile
    assert "reverse_proxy frontend:8502" in caddyfile
    assert "max_header_size 32KB" in caddyfile
    assert "request_body" in caddyfile
    assert "forward_auth api:5055" in caddyfile
    assert "X-Gateway-Token {$GATEWAY_RATE_LIMIT_TOKEN}" in caddyfile


def test_frontend_and_api_are_independent_non_root_images_with_healthchecks():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    assert services["frontend"]["build"]["dockerfile"] == "deploy/Dockerfile.frontend"
    assert services["api"]["build"]["dockerfile"] == "deploy/Dockerfile.api"
    assert services["frontend"]["environment"]["INTERNAL_API_URL"] == "http://api:5055"
    assert services["gateway"]["depends_on"]["frontend"]["condition"] == "service_healthy"
    assert services["gateway"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert "USER node" in (ROOT / "deploy" / "Dockerfile.frontend").read_text(encoding="utf-8")
    assert "USER app" in (ROOT / "deploy" / "Dockerfile.api").read_text(encoding="utf-8")


def test_release_topology_has_two_api_frontend_and_retrieval_lanes():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    for primary, replica in (
        ("frontend", "frontend_replica"),
        ("api", "api_replica"),
        ("legal_retrieval", "legal_retrieval_replica"),
    ):
        assert primary in services
        assert replica in services
        assert services[replica]["restart"] == "unless-stopped"
        assert "ports" not in services[replica]

    expected = (
        "http://legal_retrieval:8765,http://legal_retrieval_replica:8765"
    )
    assert services["api"]["environment"]["LEGAL_SEARCH_URLS"] == expected
    assert services["api_replica"]["environment"]["LEGAL_SEARCH_URLS"] == expected

    caddyfile = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text(
        encoding="utf-8"
    )
    assert "reverse_proxy api:5055 api_replica:5055" in caddyfile
    assert "reverse_proxy frontend:8502 frontend_replica:8502" in caddyfile
    assert caddyfile.count("health_uri /health") == 2
    assert caddyfile.count("lb_try_duration 5s") == 3


def test_release_launcher_requires_dedicated_non_default_secrets_and_manifest():
    script = (ROOT / "scripts" / "start_release.ps1").read_text(encoding="utf-8")

    assert '[string]$EnvFile = ".env.release"' in script
    assert "LEGAL_SECTION_GROUNDING_ENABLED" in script
    assert "manifest.sha256.json" in script
    assert "docker compose --env-file $EnvFile" in script
    assert "http://localhost:5055" not in script
    assert "http://localhost:8765" not in script
    assert '"OPEN_NOTEBOOK_ADMIN_PASSWORD"' in script
    assert '"GATEWAY_RATE_LIMIT_TOKEN"' in script
    assert '"OPEN_NOTEBOOK_PASSWORD"' not in script


def test_release_secret_file_is_excluded_from_git_and_docker_context():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".env.release" in gitignore
    assert ".env.release" in dockerignore


def test_release_data_and_backups_are_never_sent_in_docker_build_context():
    dockerignore = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "release-data" in dockerignore
    assert "release-data.zip" in dockerignore
    assert "backups" in dockerignore
    assert "frontend/.next*" in dockerignore
    assert "outputs" in dockerignore
    assert "tools" in dockerignore


def test_release_data_preparation_uses_fail_closed_form_packager():
    script = (ROOT / "scripts" / "prepare_release_data.ps1").read_text(
        encoding="utf-8"
    )

    assert "package_runtime_forms.py" in script
    assert "Assert-ReleaseTarget" in script
    assert '".venv\\Scripts\\python.exe"' in script
    assert "& $projectPython -X utf8" in script
    assert "& python -X utf8" not in script
    assert "Copy-Item -LiteralPath $FormsRoot" not in script
    assert "SanitizedSurrealData" not in script
    assert "releaseSurreal" not in script

    launcher = (ROOT / "scripts" / "start_release.ps1").read_text(
        encoding="utf-8"
    )
    assert '"release-data\\surreal_data"' not in launcher


def test_app_dockerfile_caches_frontend_dependencies_before_backend_source_copy():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    npm_ci = dockerfile.index("RUN i=0; until npm ci;")
    frontend_build = dockerfile.index("RUN npm run build")
    full_source_copy = dockerfile.index("COPY . /app")
    assert npm_ci < frontend_build < full_source_copy
