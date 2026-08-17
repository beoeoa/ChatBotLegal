from pathlib import Path


def test_model_provisioning_never_logs_live_model_repr():
    source = Path("open_notebook/ai/provision.py").read_text(encoding="utf-8")

    assert 'logger.debug(f"Using model: {model}")' not in source
    assert "type(model).__name__" in source
    assert "selection_reason" in source
