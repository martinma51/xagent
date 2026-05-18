from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from xagent.core.agent_v2 import ExecutionContext, TraceEventCallback


class TraceRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def trace_event(
        self,
        event_type: Any,
        *,
        task_id: str | None = None,
        data: dict[str, Any] | None = None,
        **_: Any,
    ) -> str:
        self.events.append(
            {
                "event_type": getattr(event_type, "value", str(event_type)),
                "task_id": task_id,
                "data": data or {},
            }
        )
        return str(len(self.events))


@pytest.mark.asyncio
async def test_trace_callback_success_emits_user_assistant_and_completion() -> None:
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-trace")
    context.metadata["task"] = "Write summary"

    await callback.on_run_start(runner=runner, context=context)
    await callback.on_run_end(
        runner=runner,
        context=context,
        result={"success": True, "execution_id": "exec-trace", "answer": "Done"},
    )

    assert [event["event_type"] for event in tracer.events] == [
        "task_start_message",
        "task_end_message",
        "task_end_general",
    ]
    assert tracer.events[1]["data"]["content"] == "Done"


@pytest.mark.asyncio
async def test_trace_callback_failed_run_emits_error() -> None:
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-trace")

    await callback.on_run_end(
        runner=runner,
        context=context,
        result={
            "success": False,
            "execution_id": "exec-trace",
            "error": "failed",
        },
    )

    assert tracer.events[0]["event_type"] == "task_error_general"
    assert tracer.events[0]["data"]["error_message"] == "failed"


@pytest.mark.asyncio
async def test_trace_callback_resume_does_not_duplicate_task_start() -> None:
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-trace")
    context.metadata["task"] = "Resume task"

    await callback.on_run_start(runner=runner, context=context, resume=True)
    await callback.on_run_start(
        runner=runner, context=context, checkpoint={"context": {}}
    )

    assert tracer.events == []


@pytest.mark.asyncio
async def test_trace_callback_no_tracer_is_noop() -> None:
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=None)
    context = ExecutionContext(execution_id="exec-trace")

    await callback.on_run_start(runner=runner, context=context)
    await callback.on_run_end(
        runner=runner,
        context=context,
        result={"success": True, "output": "Done"},
    )


@pytest.mark.asyncio
async def test_trace_callback_surfaces_uploaded_files_for_chip_rendering() -> None:
    """On run start, file_info from request_context must flow to trace_data.files
    so the frontend can render attachment chips alongside the user bubble."""
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-trace")
    context.metadata["task"] = "can u generate this?"
    # Mirrors the shape AgentV2ExecutionAdapter.execute builds when the websocket
    # handler hands its raw context dict to v2.
    context.metadata["request_context"] = {
        "uploaded_files": ["/abs/path/xAgent - UAT P0 - Chatbot_0513.xlsx"],
        "file_info": [
            {
                "file_id": "6cdc124b-d758-47e3-9871-284e1c90a98a",
                "name": "normalized.xlsx",
                "original_name": "xAgent - UAT P0 - Chatbot_0513.xlsx",
                "size": 291953,
                "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "path": "/abs/leak/should/be/stripped.xlsx",
            }
        ],
    }

    await callback.on_run_start(runner=runner, context=context)

    assert len(tracer.events) == 1
    data = tracer.events[0]["data"]
    # Frontend reads eventData.files; this is the critical assertion.
    assert data["files"] == [
        {
            "file_id": "6cdc124b-d758-47e3-9871-284e1c90a98a",
            "name": "xAgent - UAT P0 - Chatbot_0513.xlsx",
            "size": 291953,
            "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    ]
    # Mirror under attachments for clients that prefer the alias.
    assert data["attachments"] == data["files"]
    # Absolute filesystem paths must not leak through the WS payload.
    for f in data["files"]:
        assert "path" not in f


@pytest.mark.asyncio
async def test_trace_callback_omits_files_field_when_no_uploads() -> None:
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-trace")
    context.metadata["task"] = "plain question, no files"

    await callback.on_run_start(runner=runner, context=context)

    assert len(tracer.events) == 1
    data = tracer.events[0]["data"]
    assert "files" not in data
    assert "attachments" not in data


@pytest.mark.asyncio
async def test_on_user_message_posted_emits_trace_event_with_files() -> None:
    """When the websocket calls ``post_user_message`` with attachments, the
    runner's ``on_user_message_posted`` callback must emit a user_message
    trace event with the files surfaced at the top level — same chip shape
    as the fresh-start path."""
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-cont")
    context.metadata["task"] = "Original task"
    # Simulate runner.inject_user_message attaching files to the Message
    # metadata before dispatching the callback.
    files = [
        {
            "file_id": "fid-cont-1",
            "name": "follow-up.pdf",
            "size": 4096,
            "type": "application/pdf",
        }
    ]
    new_message = context.add_user_message(
        "Follow-up question with a file.",
        metadata={"files": files},
    )

    await callback.on_user_message_posted(
        runner=runner,
        context=context,
        message=new_message,
        files=files,
    )

    assert len(tracer.events) == 1
    event = tracer.events[0]
    assert event["event_type"] == "task_start_message"
    assert event["data"]["message"] == "Follow-up question with a file."
    assert event["data"]["files"] == files
    assert event["data"]["attachments"] == files


@pytest.mark.asyncio
async def test_on_user_message_posted_prevents_resume_from_duplicating() -> None:
    """After ``on_user_message_posted`` fires, a subsequent resume must NOT
    re-emit the same user_message trace event — the watermark stored on
    ``context.metadata`` is the claim ticket that prevents duplication."""
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-cont")
    context.metadata["task"] = "Original task"
    msg = context.add_user_message("Follow-up.", metadata={"files": []})

    await callback.on_user_message_posted(runner=runner, context=context, message=msg)
    # Simulate the resume that the websocket's execute_v2_resume_background
    # kicks off right after post_user_message.
    await callback.on_run_start(runner=runner, context=context, resume=True)

    assert len(tracer.events) == 1  # not two


@pytest.mark.asyncio
async def test_on_run_start_resume_emits_untraced_user_message() -> None:
    """If a checkpoint contains a user message whose trace event was never
    emitted (e.g., worker crashed between persist and emit), the resume's
    ``on_run_start`` must replay it so the chip still shows up."""
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-recovery")
    context.metadata["task"] = "Original task"
    files = [
        {
            "file_id": "fid-recover",
            "name": "recover.csv",
            "size": 256,
            "type": "text/csv",
        }
    ]
    # Simulate the state restored from a checkpoint: the user message and
    # its attached files are in the context, but no watermark — meaning the
    # prior worker died before TraceEventCallback could mark it traced.
    context.add_user_message("Continue from here.", metadata={"files": files})

    await callback.on_run_start(
        runner=runner,
        context=context,
        resume=True,
        checkpoint={"context": context.to_dict()},
    )

    assert len(tracer.events) == 1
    data = tracer.events[0]["data"]
    assert data["message"] == "Continue from here."
    assert data["files"] == files


@pytest.mark.asyncio
async def test_on_run_start_resume_skips_already_traced_user_messages() -> None:
    """A pure resume (no new user message since the watermark) must NOT
    emit anything — this protects scenario C (user clicks "Resume" on a
    paused task without sending a new message)."""
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-pure-resume")
    context.metadata["task"] = "Original task"
    msg = context.add_user_message("Original turn.")
    # Simulate the original run having already marked this message traced.
    callback._mark_traced(context, msg)

    await callback.on_run_start(runner=runner, context=context, resume=True)
    await callback.on_run_start(
        runner=runner, context=context, checkpoint={"context": context.to_dict()}
    )

    assert tracer.events == []


@pytest.mark.asyncio
async def test_on_run_start_resume_falls_back_to_request_context_for_initial_files() -> (
    None
):
    """Crash-recovery case for the very first turn: the runner attaches the
    initial user message but didn't propagate request_context.file_info
    into Message.metadata. The resume catch-up should still surface chips by
    falling back to context.metadata.request_context.file_info for that
    chronologically-first user message."""
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-first-recovery")
    context.metadata["task"] = "Initial task"
    context.metadata["request_context"] = {
        "file_info": [
            {
                "file_id": "fid-initial",
                "name": "initial.xlsx",
                "original_name": "initial.xlsx",
                "size": 1024,
                "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        ]
    }
    # Initial user message WITHOUT files metadata (runner.run only does
    # add_user_message(task) — it doesn't attach file_info to the Message).
    context.add_user_message("Initial task")

    await callback.on_run_start(
        runner=runner,
        context=context,
        resume=True,
    )

    assert len(tracer.events) == 1
    data = tracer.events[0]["data"]
    assert data["files"][0]["file_id"] == "fid-initial"


@pytest.mark.asyncio
async def test_trace_callback_skips_file_info_entries_without_file_id() -> None:
    tracer = TraceRecorder()
    callback = TraceEventCallback()
    runner = SimpleNamespace(tracer=tracer)
    context = ExecutionContext(execution_id="exec-trace")
    context.metadata["task"] = "mixed"
    context.metadata["request_context"] = {
        "file_info": [
            {"name": "no-id.txt", "size": 1},
            {"file_id": "uuid-keep", "name": "keep.txt"},
        ]
    }

    await callback.on_run_start(runner=runner, context=context)

    data = tracer.events[0]["data"]
    assert data["files"] == [
        {"file_id": "uuid-keep", "name": "keep.txt", "size": None, "type": None}
    ]
