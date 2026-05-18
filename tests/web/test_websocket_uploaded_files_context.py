from xagent.web.api.websocket import (
    _append_uploaded_files_context_to_message,
    _build_uploaded_files_context,
    _normalize_attachments_for_persistence,
    _strip_uploaded_files_block,
)


def test_build_uploaded_files_context_includes_agent_builder_kb_instruction():
    context = _build_uploaded_files_context(
        [
            {
                "file_id": "file-123",
                "name": "faq.docx",
                "original_name": "FAQ.docx",
            }
        ],
        is_agent_builder=True,
    )

    assert "FAQ.docx: file_id=file-123" in context
    assert "create_knowledge_base_from_file" in context
    assert 'file_ids = ["file-123"]' in context
    assert "Do NOT ask the user to upload again" in context


def test_append_uploaded_files_context_to_message_is_idempotent():
    context = _build_uploaded_files_context(
        [{"file_id": "file-123", "name": "faq.docx"}],
        is_agent_builder=False,
    )

    message = _append_uploaded_files_context_to_message("Upload File", context)
    assert message.startswith("Upload File\n\n## UPLOADED FILES")
    assert _append_uploaded_files_context_to_message(message, context) == message


def test_strip_uploaded_files_block_removes_trailing_marker():
    augmented = (
        "please analyze the file\n\n"
        "## UPLOADED FILES\n"
        "The user has uploaded file(s) for this turn. Use these exact file_id values:\n"
        "- asr_test.wav: file_id=44dc00e0-d189-43c0-a3bd-2e3e146f422f"
    )

    assert _strip_uploaded_files_block(augmented) == "please analyze the file"


def test_strip_uploaded_files_block_handles_message_that_is_only_the_block():
    only_block = (
        "## UPLOADED FILES\n"
        "The user has uploaded file(s) for this turn. Use these exact file_id values:\n"
        "- a.txt: file_id=abc"
    )

    assert _strip_uploaded_files_block(only_block) == ""


def test_strip_uploaded_files_block_is_a_noop_for_clean_content():
    assert _strip_uploaded_files_block("just a plain user message") == (
        "just a plain user message"
    )
    assert _strip_uploaded_files_block("") == ""


def test_normalize_attachments_for_persistence_projects_minimal_fields():
    persisted = _normalize_attachments_for_persistence(
        [
            {
                "file_id": "uuid-1",
                "name": "normalized.wav",
                "original_name": "Original.wav",
                "size": 12345,
                "type": "audio/wav",
                "path": "/secret/abs/path/Original.wav",
                "workspace_path": "/secret/abs/workspace/Original.wav",
            }
        ]
    )

    # We persist only the fields the UI needs and the file_id; absolute paths
    # must not leak into the chat history payload.
    assert persisted == [
        {
            "file_id": "uuid-1",
            "name": "Original.wav",
            "size": 12345,
            "type": "audio/wav",
        }
    ]


def test_normalize_attachments_for_persistence_drops_entries_without_file_id():
    persisted = _normalize_attachments_for_persistence(
        [
            {"name": "no-id.txt", "size": 1},
            {"file_id": "uuid-2", "name": "ok.txt"},
        ]
    )

    assert persisted == [
        {"file_id": "uuid-2", "name": "ok.txt", "size": None, "type": None}
    ]


def test_normalize_attachments_for_persistence_empty_input():
    assert _normalize_attachments_for_persistence([]) == []
    assert _normalize_attachments_for_persistence(None) == []  # type: ignore[arg-type]
