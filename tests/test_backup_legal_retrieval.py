from scripts.backup_legal_retrieval import _database_url


def test_release_backup_ignores_inherited_isolated_test_database():
    selected = _database_url(
        environment={
            "LEGAL_DATABASE_URL": (
                "postgresql://test:test@127.0.0.1/"
                "chatbotlegal_retrieval_staging"
            )
        },
        repo_settings={
            "LEGAL_RELEASE_DATABASE_URL": (
                "postgresql+psycopg2://runtime:secret@"
                "host.docker.internal:5432/legal_chatbot"
            )
        },
        legacy_settings={},
    )

    assert "127.0.0.1:5432/legal_chatbot" in selected
    assert "chatbotlegal_retrieval_staging" not in selected


def test_release_backup_accepts_only_explicit_corpus_override():
    selected = _database_url(
        environment={
            "LEGAL_DATABASE_URL": (
                "postgresql://test:test@127.0.0.1/"
                "chatbotlegal_retrieval_staging"
            ),
            "LEGAL_CORPUS_DATABASE_URL": (
                "postgresql://runtime:secret@127.0.0.1/"
                "legal_chatbot_snapshot"
            ),
        },
        repo_settings={
            "LEGAL_RELEASE_DATABASE_URL": (
                "postgresql://runtime:secret@127.0.0.1/legal_chatbot"
            )
        },
        legacy_settings={},
    )

    assert selected.endswith("/legal_chatbot_snapshot")
