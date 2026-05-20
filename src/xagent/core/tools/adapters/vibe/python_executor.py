"""
Python Code Execution Tool for xagent
Framework wrapper around the pure Python executor tool
"""

import asyncio
import logging
from typing import Any, Dict, Mapping, Optional, Type

from pydantic import BaseModel, Field

from ....workspace import TaskWorkspace
from ...core.python_executor import PythonExecutorCore
from .base import AbstractBaseTool, ToolCategory, ToolVisibility
from .function import FunctionTool
from .sandboxed_tool.sandbox_config import sandbox_config

logger = logging.getLogger(__name__)


class PythonExecutorFunctionTool(FunctionTool):
    """Python executor tool with BASIC category."""

    category = ToolCategory.BASIC


class PythonExecutorArgs(BaseModel):
    code: str = Field(description="Python code to execute")
    capture_output: bool = Field(
        default=True, description="Whether to capture stdout/stderr"
    )


class PythonExecutorResult(BaseModel):
    success: bool = Field(description="Whether the code executed successfully")
    output: str = Field(description="Output from the code execution")
    error: str = Field(default="", description="Error message if execution failed")


class PythonExecutorTool(AbstractBaseTool):
    """Framework wrapper for the pure Python executor tool"""

    def __init__(self, workspace: Optional[TaskWorkspace] = None) -> None:
        self._visibility = ToolVisibility.PUBLIC
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "python_executor"

    @property
    def description(self) -> str:
        return """Execute Python code safely and return the output.
        Supports most Python operations including calculations, data processing, and visualization.
        Captures stdout and stderr from the execution."""

    @property
    def tags(self) -> list[str]:
        return ["python", "code", "execution", "computation"]

    def args_type(self) -> Type[BaseModel]:
        return PythonExecutorArgs

    def return_type(self) -> Type[BaseModel]:
        return PythonExecutorResult

    def run_json_sync(self, args: Mapping[str, Any]) -> Any:
        exec_args = PythonExecutorArgs.model_validate(args)

        # Determine working directory
        working_directory = self._get_working_directory()

        # Add workspace environment variables if workspace is available
        workspace_env = self._get_workspace_env()

        # Create core executor instance
        executor = PythonExecutorCore(working_directory=working_directory)

        # Add workspace variables to the executor's globals if available
        if workspace_env:
            # Inject workspace variables into the code execution environment
            env_code = "\n".join([f"{k} = {repr(v)}" for k, v in workspace_env.items()])
            full_code = f"{env_code}\n\n{exec_args.code}"
        else:
            full_code = exec_args.code

        # Execute code within auto_register context.
        #
        # The python_executor's `working_directory` is not always the same as
        # `self._workspace.workspace_dir` — they can diverge when the agent
        # workspace is keyed by raw task_id ("67") but the executor cwd lands
        # in the per-user output tree ("web_task_67/output"). In that case the
        # built-in `auto_register_files()` scan (which walks workspace_dir)
        # would miss files saved by openpyxl/pptxgenjs/etc. via raw fs IO.
        #
        # Belt-and-braces: snapshot the executor's actual working_directory
        # before/after the call and manually register any new files. This is
        # a no-op when the dirs already coincide (file_id lookup deduplicates).
        if self._workspace and working_directory:
            import logging as _logging

            _log = _logging.getLogger(__name__)
            from pathlib import Path as _Path

            def _scan_cwd() -> set:
                wd = _Path(working_directory)
                if not wd.exists():
                    return set()
                # Only filter on the path SEGMENTS BELOW working_directory.
                # We cannot reject paths whose parents are hidden (e.g.
                # `.xagent_data`) because the whole tree lives under one.
                wd_resolved = wd.resolve()
                results = set()
                for p in wd.rglob("*"):
                    if not p.is_file():
                        continue
                    try:
                        rel_parts = p.resolve().relative_to(wd_resolved).parts
                    except ValueError:
                        continue
                    if any(part.startswith(".") for part in rel_parts):
                        continue
                    if "__pycache__" in rel_parts or "node_modules" in rel_parts:
                        continue
                    results.add(p)
                return results

            files_before = _scan_cwd()
            with self._workspace.auto_register_files():
                result = executor.execute_code(full_code, exec_args.capture_output)
            files_after = _scan_cwd()
            new_files = files_after - files_before
            for fp in new_files:
                try:
                    self._workspace.register_file(str(fp))
                    _log.info(f"python_executor: backfill-registered new file {fp}")
                except Exception as e:
                    _log.warning(f"python_executor: failed to register {fp}: {e}")
        else:
            result = executor.execute_code(full_code, exec_args.capture_output)

        return PythonExecutorResult(**result).model_dump()

    async def run_json_async(self, args: Mapping[str, Any]) -> Any:
        return await asyncio.to_thread(self.run_json_sync, args)

    def _get_working_directory(self) -> Optional[str]:
        """Determine the working directory based on workspace settings"""
        if self._workspace:
            # Use workspace output directory as working directory
            return str(self._workspace.resolve_path(""))
        return None

    def _get_workspace_env(self) -> Optional[Dict[str, str]]:
        """Get workspace environment variables"""
        if not self._workspace:
            return None

        return {
            "WORKSPACE_OUTPUT_DIR": str(self._workspace.resolve_path("")),
            "WORKSPACE_INPUT_DIR": str(self._workspace.resolve_path("", "input")),
            "WORKSPACE_TEMP_DIR": str(self._workspace.resolve_path("", "temp")),
            "WORKSPACE_DIR": str(self._workspace.workspace_dir.resolve()),
        }


@sandbox_config(packages=["pandas>=1.3.0", "numpy>=1.21.0", "matplotlib>=3.5.0"])
class PythonExecutorToolForBasic(PythonExecutorTool):
    """Python executor tool with BASIC category."""

    category = ToolCategory.BASIC

    @property
    def name(self) -> str:
        return "execute_python_code"


def get_python_executor_tool(info: Optional[dict[str, Any]] = None) -> FunctionTool:
    """
    Create a workspace-bound Python executor tool.

    Args:
        info: Dictionary containing workspace information

    Returns:
        A Python executor tool bound to the specified workspace
    """
    # Extract workspace from info if provided
    workspace = None
    if info and "workspace" in info:
        workspace = (
            info["workspace"] if isinstance(info["workspace"], TaskWorkspace) else None
        )

    # Create workspace-bound Python executor
    executor = PythonExecutorTool(workspace=workspace)

    # Wrap as LangChain tool
    def execute_python_code(code: str, capture_output: bool = True) -> Dict[str, Any]:
        result: Dict[str, Any] = executor.run_json_sync(
            {"code": code, "capture_output": capture_output}
        )
        return result

    return PythonExecutorFunctionTool(
        execute_python_code, description=executor.description
    )


def create_python_executor_tool(workspace: TaskWorkspace) -> AbstractBaseTool:
    """Create Python executor tool bound to workspace"""
    return PythonExecutorTool(workspace)
