from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_source_command_import_does_not_eager_load_podcast_reranker_stack():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import commands.source_commands; "
                "assert 'podcast_creator' not in sys.modules; "
                "assert 'sentence_transformers' not in sys.modules; "
                "assert 'multiprocess' not in sys.modules"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        # Process startup can be delayed while the full 1,800+ test suite is
        # concurrently releasing worker resources. This test verifies the
        # imported module set, not a cold-start latency SLO.
        timeout=45,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_commands_package_keeps_backwards_compatible_lazy_exports():
    import commands

    assert callable(commands.process_source_command)


def test_commands_package_does_not_export_retired_podcast_command():
    import commands

    assert "generate_podcast_command" not in commands.__all__
    assert not hasattr(commands, "generate_podcast_command")
