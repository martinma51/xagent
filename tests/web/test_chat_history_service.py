from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.core.agent.transcript import build_assistant_transcript_content
from xagent.web.models.chat_message import TaskChatMessage
from xagent.web.models.database import Base
from xagent.web.models.task import Task, TaskStatus
from xagent.web.models.user import User
from xagent.web.services.chat_history_service import (
    get_latest_waiting_question,
    load_task_transcript,
    persist_assistant_message,
    persist_user_message,
)


def _create_db_session():
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def _create_task(db_session):
    user = User(username="tester", password_hash="hashed_password", is_admin=False)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    task = Task(
        user_id=int(user.id),
        title="Chat task",
        description="Task chat",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def test_load_task_transcript_returns_prior_turns_only():
    db_session = _create_db_session()
    try:
        task = _create_task(db_session)

        first_user = persist_user_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "Summarize the repo",
        )
        assert first_user is not None

        assistant = persist_assistant_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "The main risks are architecture drift and persistence gaps.",
            message_type="final_answer",
        )
        assert assistant is not None

        second_user = persist_user_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "Expand the persistence gap",
        )
        assert second_user is not None

        transcript = load_task_transcript(
            db_session,
            int(task.id),
            before_message_id=int(second_user.id),
        )

        assert transcript == [
            {"role": "user", "content": "Summarize the repo"},
            {
                "role": "assistant",
                "content": "The main risks are architecture drift and persistence gaps.",
            },
        ]
    finally:
        db_session.close()


def test_persist_assistant_message_formats_interactions_into_transcript():
    db_session = _create_db_session()
    try:
        task = _create_task(db_session)

        persist_assistant_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "I need one more detail before I continue.",
            message_type="chat_response",
            interactions=[
                {
                    "type": "text_input",
                    "label": "Repository path",
                    "placeholder": "Enter the repository path",
                }
            ],
        )

        stored_message = (
            db_session.query(TaskChatMessage)
            .filter(TaskChatMessage.task_id == int(task.id))
            .first()
        )

        assert stored_message is not None
        assert stored_message.role == "assistant"
        assert "Please answer the following questions:" in stored_message.content
        assert "Repository path: Enter the repository path" in stored_message.content
    finally:
        db_session.close()


def test_get_latest_waiting_question_returns_latest_question_only():
    db_session = _create_db_session()
    try:
        task = _create_task(db_session)

        persist_assistant_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "First question",
            message_type="question",
            interactions=[{"type": "text_input", "label": "First"}],
        )
        persist_assistant_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "Regular answer",
            message_type="assistant_message",
        )
        persist_assistant_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "Second question",
            message_type="question",
            interactions=[{"type": "text_input", "label": "Second"}],
        )

        question, interactions = get_latest_waiting_question(db_session, int(task.id))

        assert question is not None
        assert question.startswith("Second question")
        assert interactions == [{"type": "text_input", "label": "Second"}]
    finally:
        db_session.close()


def test_build_assistant_transcript_content_skips_empty_unknown_interactions_header():
    content = build_assistant_transcript_content("Test", [{"type": "unknown_type"}])

    assert content == "Test"


def test_persist_user_message_stores_attachments_json():
    """The user's typed text and the file attachments live in separate columns."""
    db_session = _create_db_session()
    try:
        task = _create_task(db_session)

        attachments = [
            {
                "file_id": "44dc00e0-d189-43c0-a3bd-2e3e146f422f",
                "name": "asr_test.wav",
                "size": 12345,
                "type": "audio/wav",
            }
        ]
        persisted = persist_user_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "please transcribe this clip",
            attachments=attachments,
        )

        assert persisted is not None

        stored = (
            db_session.query(TaskChatMessage)
            .filter(TaskChatMessage.id == int(persisted.id))
            .one()
        )

        # The persisted body must NOT include the LLM-only ``## UPLOADED FILES``
        # block — that block belongs in the system prompt / per-turn LLM context
        # and would otherwise leak into the chat bubble on reload.
        assert stored.content == "please transcribe this clip"
        assert "## UPLOADED FILES" not in stored.content
        assert stored.attachments == attachments
    finally:
        db_session.close()


def test_persist_user_message_without_attachments_leaves_column_null():
    db_session = _create_db_session()
    try:
        task = _create_task(db_session)

        persisted = persist_user_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "hello, no files this turn",
        )

        assert persisted is not None
        stored = (
            db_session.query(TaskChatMessage)
            .filter(TaskChatMessage.id == int(persisted.id))
            .one()
        )

        assert stored.attachments is None
    finally:
        db_session.close()


def test_persist_user_message_allows_empty_text_when_attachments_present():
    """A user might send a file with no caption — that should still be saved."""
    db_session = _create_db_session()
    try:
        task = _create_task(db_session)

        persisted = persist_user_message(
            db_session,
            int(task.id),
            int(task.user_id),
            "",
            attachments=[{"file_id": "fid", "name": "x.txt"}],
        )

        assert persisted is not None
        assert persisted.content == ""
        assert persisted.attachments == [{"file_id": "fid", "name": "x.txt"}]
    finally:
        db_session.close()
