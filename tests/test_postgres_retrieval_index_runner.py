from pathlib import Path

import pytest

from scripts import manage_postgres_retrieval_indexes as runner


class _Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, traceback):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return _Context(self.connection)


def test_cli_is_dry_run_by_default_and_mutations_are_explicit():
    parser = runner.build_parser()

    assert parser.parse_args([]).mode == runner.Mode.DRY_RUN.value
    assert (
        parser.parse_args(["--plan-only"]).mode
        == runner.Mode.PLAN_ONLY.value
    )
    assert parser.parse_args(["--apply"]).mode == runner.Mode.APPLY.value
    assert parser.parse_args(["--rollback"]).mode == runner.Mode.ROLLBACK.value

    with pytest.raises(SystemExit):
        parser.parse_args(["--apply", "--rollback"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--plan-only", "--apply"])


def test_plan_only_never_resolves_url_or_opens_database(monkeypatch, tmp_path):
    def unexpected_call(*args, **kwargs):
        raise AssertionError("offline plan must not inspect database state")

    monkeypatch.setattr(runner, "resolve_database_url", unexpected_call)
    monkeypatch.setattr(runner, "create_autocommit_engine", unexpected_call)
    report_path = tmp_path / "offline-index-plan.json"

    exit_code = runner.main(["--plan-only", "--report", str(report_path)])
    report = report_path.read_text(encoding="utf-8")

    assert exit_code == 0
    assert '"mode": "plan-only"' in report
    assert '"database_connection_opened": false' in report
    assert '"executed": false' in report
    assert '"planned_statements": 12' in report
    assert '"migration": "001_retrieval_indexes_down.sql"' in report
    assert '"validated": true' in report


def test_engine_is_created_in_postgresql_autocommit_mode():
    captured = {}
    sentinel = object()

    def fake_engine_factory(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    result = runner.create_autocommit_engine(
        "postgresql+psycopg2://example/legal",
        engine_factory=fake_engine_factory,
    )

    assert result is sentinel
    assert captured["isolation_level"] == "AUTOCOMMIT"
    assert captured["pool_pre_ping"] is True


def test_database_url_requires_explicit_input_or_environment(monkeypatch):
    monkeypatch.delenv("LEGAL_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="LEGAL_DATABASE_URL"):
        runner.resolve_database_url()

    monkeypatch.setenv(
        "LEGAL_DATABASE_URL", "postgresql+psycopg2://localhost/legal"
    )
    assert (
        runner.resolve_database_url()
        == "postgresql+psycopg2://localhost/legal"
    )


def test_dry_run_runs_preflight_but_never_executes_migration(monkeypatch):
    events = []
    connection = object()

    monkeypatch.setattr(
        runner,
        "run_preflight",
        lambda conn, mode: events.append(("preflight", conn, mode)) or {"ok": True},
    )
    monkeypatch.setattr(
        runner,
        "execute_migration",
        lambda *args, **kwargs: events.append(("migration", args, kwargs)),
    )
    monkeypatch.setattr(
        runner,
        "run_explain_analyze",
        lambda *args, **kwargs: events.append(("explain", args, kwargs)),
    )

    result = runner.run_with_engine(
        _Engine(connection),
        mode=runner.Mode.DRY_RUN,
        benchmark_term="khai sinh",
    )

    assert result["mode"] == runner.Mode.DRY_RUN.value
    assert events == [("preflight", connection, runner.Mode.DRY_RUN)]
    assert result["planned_statements"] > 0


@pytest.mark.parametrize("mode", [runner.Mode.APPLY, runner.Mode.ROLLBACK])
def test_mutating_modes_capture_explain_analyze_before_and_after(
    monkeypatch, mode
):
    events = []
    connection = object()

    monkeypatch.setattr(
        runner,
        "run_preflight",
        lambda conn, selected_mode: events.append(("preflight", selected_mode))
        or {"ok": True},
    )
    monkeypatch.setattr(
        runner,
        "run_explain_analyze",
        lambda conn, term: events.append(("explain", term))
        or {"Execution Time": 1.0},
    )
    monkeypatch.setattr(
        runner,
        "execute_migration",
        lambda conn, statements: events.append(("migration", len(statements))),
    )

    result = runner.run_with_engine(
        _Engine(connection), mode=mode, benchmark_term="khai sinh"
    )

    assert events[0] == ("preflight", mode)
    assert events[1] == ("explain", "khai sinh")
    assert events[2][0] == "migration"
    assert events[3] == ("explain", "khai sinh")
    assert result["explain_before"] == {"Execution Time": 1.0}
    assert result["explain_after"] == {"Execution Time": 1.0}


def test_apply_failure_is_not_followed_by_automatic_rollback(monkeypatch):
    events = []
    connection = object()

    monkeypatch.setattr(runner, "run_preflight", lambda *args: {"ok": True})
    monkeypatch.setattr(
        runner, "run_explain_analyze", lambda *args: {"Execution Time": 1.0}
    )

    def fail_once(conn, statements):
        events.append(Path(runner.UP_SQL_PATH).name)
        raise RuntimeError("index build failed")

    monkeypatch.setattr(runner, "execute_migration", fail_once)

    with pytest.raises(RuntimeError, match="index build failed"):
        runner.run_with_engine(
            _Engine(connection),
            mode=runner.Mode.APPLY,
            benchmark_term="khai sinh",
        )

    assert events == ["001_retrieval_indexes_up.sql"]


def test_benchmark_uses_explain_analyze_and_is_read_only():
    sql = runner.BENCHMARK_SQL.upper()

    assert "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)" in sql
    assert "SELECT DISTINCT" in sql
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE "):
        assert forbidden not in sql


def test_preflight_rejects_invalid_index_for_apply_but_allows_explicit_cleanup(
    monkeypatch,
):
    class Driver:
        autocommit = True

    class Proxy:
        driver_connection = Driver()

    class Dialect:
        name = "postgresql"

    class Connection:
        dialect = Dialect()
        connection = Proxy()

    metadata = iter(
        [
            {
                "database_name": "legal",
                "database_user": "operator",
                "server_version_num": 160000,
                "in_recovery": False,
                "can_create": True,
            },
            {"available": True, "installed": True},
        ]
    )
    monkeypatch.setattr(runner, "_one_mapping", lambda *args: next(metadata))
    monkeypatch.setattr(
        runner,
        "_inspect_columns",
        lambda connection, table_name: runner.REQUIRED_COLUMNS[table_name],
    )
    monkeypatch.setattr(
        runner,
        "_inspect_indexes",
        lambda connection: [
            {
                "index_name": "ix_legal_retrieval_chunks_content_trgm",
                "ready": False,
                "valid": False,
                "definition": (
                    "CREATE INDEX ix_legal_retrieval_chunks_content_trgm "
                    "ON public.legal_article_chunks USING gin "
                    "(lower(COALESCE(content, ''::text)) gin_trgm_ops)"
                ),
            }
        ],
    )

    with pytest.raises(runner.PreflightError, match="invalid state"):
        runner.run_preflight(Connection(), runner.Mode.APPLY)

    metadata = iter(
        [
            {
                "database_name": "legal",
                "database_user": "operator",
                "server_version_num": 160000,
                "in_recovery": False,
                "can_create": True,
            },
            {"available": True, "installed": True},
        ]
    )
    monkeypatch.setattr(runner, "_one_mapping", lambda *args: next(metadata))
    result = runner.run_preflight(Connection(), runner.Mode.ROLLBACK)

    assert result["invalid_target_indexes"] == [
        "ix_legal_retrieval_chunks_content_trgm"
    ]


def test_existing_target_index_name_must_match_owned_definition():
    mismatched = [
        {
            "index_name": "ix_legal_retrieval_chunks_content_trgm",
            "ready": True,
            "valid": True,
            "definition": "CREATE INDEX ix_legal_retrieval_chunks_content_trgm ON unrelated_table USING btree (id)",
        }
    ]

    with pytest.raises(runner.PreflightError, match="unexpected definition"):
        runner.validate_existing_index_definitions(mismatched)
