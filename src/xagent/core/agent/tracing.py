from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .result import extract_assistant_message
from .trace import (
    get_display_user_message,
    trace_ai_message,
    trace_error,
    trace_task_completion,
    trace_user_message,
)

# Stored on ``context.metadata`` to remember which user messages have already
# been emitted as ``trace_user_message`` events. Uses the latest traced
# message's ISO-8601 UTC timestamp as a high-water mark — ISO strings compare
# lexicographically when timezone-normalized, and the mark survives
# checkpoint round-trips because ``context.metadata`` is persisted in
# ``ExecutionContext.to_dict``.
TRACE_WATERMARK_KEY = "_user_message_trace_watermark"


@dataclass
class TraceEventCallback:
    """Bridge agent runner callbacks into the existing web trace stream."""

    async def on_run_start(
        self,
        *,
        runner: Any,
        context: Any,
        resume: bool = False,
        checkpoint: dict[str, Any] | None = None,
    ) -> None:
        tracer = getattr(runner, "tracer", None)
        if tracer is None or not callable(getattr(tracer, "trace_event", None)):
            return

        if not (resume or checkpoint):
            task = self._task_from_context(context)
            if not task:
                return
            await self._emit_user_message_trace(
                tracer=tracer,
                context=context,
                message=get_display_user_message(context, task),
                files=self._files_from_context(context),
            )
            # Mark the latest user message (if the runner added one) as
            # traced so a subsequent resume does not re-emit it.
            latest = self._latest_user_message(context)
            if latest is not None:
                self._mark_traced(context, latest)
            return

        # Resume / checkpoint replay: emit any user messages the prior turn
        # did not get to trace. This handles two real scenarios:
        #   1. ``inject_user_message`` was called, the checkpoint was
        #      persisted, but the in-process trace emission was lost
        #      (worker crash between persist and emit).
        #   2. Defensive double-coverage for the continuation flow even
        #      though ``on_user_message_posted`` already covers the happy
        #      path — keeps the chip from disappearing if the callback
        #      somehow didn't fire on the prior worker.
        await self._emit_untraced_user_messages(tracer=tracer, context=context)

    async def on_user_message_posted(
        self,
        *,
        runner: Any,
        context: Any,
        message: Any,
        files: list[dict[str, Any]] | None = None,
    ) -> None:
        """Fire when ``runner.inject_user_message`` lands a fresh user turn.

        ``message`` is the freshly added ``Message`` instance. ``files`` is
        the normalized attachment list the websocket layer received; when
        absent we fall back to ``message.metadata['files']`` (which
        ``inject_user_message`` populates when the caller passes ``files``).
        """
        tracer = getattr(runner, "tracer", None)
        if tracer is None or not callable(getattr(tracer, "trace_event", None)):
            return

        content = getattr(message, "content", None) or ""
        if not content:
            return

        resolved_files = files or self._files_from_message(message)
        await self._emit_user_message_trace(
            tracer=tracer,
            context=context,
            message=content,
            files=resolved_files,
        )
        self._mark_traced(context, message)

    async def on_run_end(
        self, *, runner: Any, context: Any, result: dict[str, Any]
    ) -> None:
        tracer = getattr(runner, "tracer", None)
        if tracer is None or not callable(getattr(tracer, "trace_event", None)):
            return

        execution_id = str(
            result.get("execution_id") or getattr(context, "execution_id", "") or ""
        )
        status = str(result.get("status") or "")
        output = extract_assistant_message(result)
        data: dict[str, Any] = {
            "execution_id": execution_id,
            "status": status or ("completed" if result.get("success") else "failed"),
            "pattern": result.get("pattern"),
        }

        if result.get("success"):
            if output:
                completion_result: dict[str, Any] = {"content": output}
                file_outputs = result.get("file_outputs")
                if file_outputs:
                    completion_result["file_outputs"] = file_outputs
                    completion_result["output"] = output
                await trace_ai_message(tracer, execution_id, output, data)
                await trace_task_completion(
                    tracer,
                    execution_id,
                    result=completion_result,
                    success=True,
                )
            return

        if status in {"interrupted", "waiting_for_user"}:
            # Paused/interrupted executions are resumable control states, not
            # completions. The web trace compatibility layer maps
            # TASK_END_GENERAL to task_completion, so do not emit it here.
            return

        await trace_error(
            tracer,
            execution_id,
            error_type="agent_error",
            error_message=str(result.get("error") or "agent execution failed"),
            data={**data, "context": self._context_payload(context)},
        )

    async def _emit_user_message_trace(
        self,
        *,
        tracer: Any,
        context: Any,
        message: str,
        files: list[dict[str, Any]],
    ) -> None:
        trace_data: dict[str, Any] = {"context": self._context_payload(context)}
        if files:
            # Surface uploaded files at the top level of trace_data so the
            # frontend user-message renderer (which reads ``eventData.files``)
            # can show clickable file chips alongside the user's message.
            trace_data["files"] = files
            trace_data["attachments"] = files
        execution_id = str(getattr(context, "execution_id", "") or "")
        await trace_user_message(tracer, execution_id, message, trace_data)

    async def _emit_untraced_user_messages(self, *, tracer: Any, context: Any) -> None:
        watermark = self._watermark(context)
        messages = list(getattr(context, "messages", []) or [])
        for index, message in enumerate(messages):
            if getattr(message, "role", None) != "user":
                continue
            content = getattr(message, "content", None) or ""
            if not content:
                continue
            ts = self._message_timestamp_iso(message)
            if watermark and ts and ts <= watermark:
                continue
            files = self._files_from_message(message)
            # For the chronologically first user message we additionally fall
            # back to request_context.file_info — the runner's fresh-start
            # path attaches files to the request_context dict but not to the
            # ``Message`` itself, so a crash-recovery resume needs this
            # fallback to surface chips for the *original* turn.
            if not files and index == self._first_user_message_index(messages):
                files = self._files_from_context(context)
            await self._emit_user_message_trace(
                tracer=tracer,
                context=context,
                message=content,
                files=files,
            )
            self._mark_traced(context, message)

    def _watermark(self, context: Any) -> str | None:
        metadata = getattr(context, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        value = metadata.get(TRACE_WATERMARK_KEY)
        return value if isinstance(value, str) and value else None

    def _mark_traced(self, context: Any, message: Any) -> None:
        ts = self._message_timestamp_iso(message)
        if ts is None:
            return
        metadata = getattr(context, "metadata", None)
        if not isinstance(metadata, dict):
            return
        existing = metadata.get(TRACE_WATERMARK_KEY)
        if not isinstance(existing, str) or ts > existing:
            metadata[TRACE_WATERMARK_KEY] = ts

    def _message_timestamp_iso(self, message: Any) -> str | None:
        ts = getattr(message, "timestamp", None)
        if isinstance(ts, datetime):
            return ts.isoformat()
        if isinstance(ts, str) and ts:
            return ts
        return None

    def _latest_user_message(self, context: Any) -> Any | None:
        messages = list(getattr(context, "messages", []) or [])
        for message in reversed(messages):
            if getattr(message, "role", None) == "user":
                return message
        return None

    def _first_user_message_index(self, messages: list[Any]) -> int:
        for index, message in enumerate(messages):
            if getattr(message, "role", None) == "user":
                return index
        return -1

    def _files_from_message(self, message: Any) -> list[dict[str, Any]]:
        metadata = getattr(message, "metadata", None)
        if not isinstance(metadata, dict):
            return []
        files = metadata.get("files")
        if not isinstance(files, list):
            return []
        return [entry for entry in files if isinstance(entry, dict)]

    def _context_payload(self, context: Any) -> dict[str, Any] | None:
        to_dict = getattr(context, "to_dict", None)
        if callable(to_dict):
            payload = to_dict()
            return dict(payload) if isinstance(payload, dict) else None
        return None

    def _task_from_context(self, context: Any) -> str | None:
        metadata = getattr(context, "metadata", None)
        if isinstance(metadata, dict):
            task = metadata.get("task")
            if isinstance(task, str) and task:
                return task
        messages = getattr(context, "messages", [])
        for message in messages:
            if getattr(message, "role", None) == "user":
                content = getattr(message, "content", None)
                if isinstance(content, str) and content:
                    return content
        return None

    def _files_from_context(self, context: Any) -> list[dict[str, Any]]:
        """Extract uploaded-file metadata from the execution context.

        The websocket adapter wraps the raw context dict — including
        ``file_info`` — inside ``metadata["request_context"]`` when starting
        a run. Project that into the minimal shape the frontend FileAttachment
        chip needs, stripping absolute filesystem paths.
        """
        metadata = getattr(context, "metadata", None)
        if not isinstance(metadata, dict):
            return []
        request_context = metadata.get("request_context")
        if not isinstance(request_context, dict):
            return []
        file_info_list = request_context.get("file_info")
        if not isinstance(file_info_list, list):
            return []

        projected: list[dict[str, Any]] = []
        for info in file_info_list:
            if not isinstance(info, dict):
                continue
            file_id = info.get("file_id")
            if not file_id:
                continue
            projected.append(
                {
                    "file_id": str(file_id),
                    "name": str(
                        info.get("original_name") or info.get("name") or "uploaded file"
                    ),
                    "size": info.get("size"),
                    "type": info.get("type"),
                }
            )
        return projected
