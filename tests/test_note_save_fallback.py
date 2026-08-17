from unittest.mock import AsyncMock, patch

import pytest

from open_notebook.domain.base import ObjectModel
from open_notebook.domain.notebook import Note


@pytest.mark.asyncio
async def test_note_save_succeeds_when_embedding_queue_is_unavailable():
    note = Note(id="note:test", title="Test", content="Persist this note")

    with (
        patch.object(ObjectModel, "save", new=AsyncMock()) as save_record,
        patch(
            "open_notebook.domain.notebook.submit_command",
            side_effect=RuntimeError("queue unavailable"),
        ),
    ):
        command_id = await note.save()

    save_record.assert_awaited_once()
    assert command_id is None
