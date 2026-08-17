"""Contract for the intentionally retired podcast command surface."""

import commands


def test_retired_podcast_command_is_not_exported():
    assert "generate_podcast_command" not in commands.__all__
    assert "build_episode_output_dir" not in commands.__all__
