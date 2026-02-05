import json, os
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Callable, Dict, Tuple, Optional, List
from ollama import Client

c = Client(
    host="http://localhost:11434",
)

global_model = "qwen3-vl:4b"

base_system_prompt = open("brain/SOUL.md").read().strip()

DEFAULT_TOOL_TIMEOUT_SECONDS = 30
MAX_CONSECUTIVE_TOOL_REQUESTS = 5
MAX_TOTAL_TOOL_REQUESTS = 10
MAX_LOG_RESPONSE_CHARS = 500
LOG_BASE_DIR = "logs/tools"
MAX_RETRIES_PER_TOOL = 2

ToolExecResult = Tuple[str, str]
ToolContext = Dict[str, Any]
ToolHandler = Callable[[Dict[str, Any], ToolContext], ToolExecResult]
TOOL_HANDLERS: Dict[str, ToolHandler] = {}


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    argument_schema: Optional[str] = None


TOOL_METADATA: Dict[str, ToolMetadata] = {}


class ToolConversation:
    """
    Manage the rolling conversation that is sent to the model.
    """

    def __init__(self, initial_payload: Dict[str, Any]) -> None:
        self._events: List[str] = [json.dumps(initial_payload)]

    def render(self) -> str:
        return "\n".join(self._events)

    def add_model_event(self, raw_response: str) -> None:
        self._events.append(raw_response)

    def add_tool_event(self, payload: Dict[str, Any]) -> None:
        self._events.append(json.dumps(payload))

    @property
    def events(self) -> List[str]:
        return list(self._events)


class ToolExecutionLogger:
    """
    Persist tool execution metadata per channel for observability.
    """

    def __init__(self, base_dir: str = LOG_BASE_DIR) -> None:
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _channel_path(self, channel_id: str) -> str:
        safe_id = channel_id or "default"
        return os.path.join(self.base_dir, f"{safe_id}.jsonl")

    def log(self, channel_id: str, entry: Dict[str, Any]) -> None:
        record = dict(entry)
        record["timestamp"] = datetime.utcnow().isoformat() + "Z"
        path = self._channel_path(channel_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a+", encoding="utf-8") as f:
            f.write(json.dumps(record))
            f.write("\n")


def _summarize_response(
    text: Optional[str], limit: int = MAX_LOG_RESPONSE_CHARS
) -> Tuple[str, bool]:
    """
    Produce a bounded preview of the tool response for logging.
    """
    text = text or ""
    if len(text) <= limit:
        return text, False
    return text[: max(limit - 3, 0)] + "...", True


class MemoryManager:
    """
    Handle storing and retrieving memories scoped to a given channel.
    """

    def __init__(self, base_dir: str = "brain/memories") -> None:
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _channel_path(self, channel_id: str) -> str:
        safe_id = channel_id or "default"
        return os.path.join(self.base_dir, f"{safe_id}.md")

    def load(self, channel_id: str) -> str:
        path = self._channel_path(channel_id)
        if not os.path.exists(path):
            return ""
        with open(path, "r") as f:
            return f.read().strip()

    def append(self, channel_id: str, memory: str) -> None:
        if not memory:
            return
        path = self._channel_path(channel_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a+") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() > 0:
                f.write("\n")
            f.write(f"- {memory.strip()}")


memory_manager = MemoryManager()
tool_logger = ToolExecutionLogger()


def register_tool(
    name: str, description: str, argument_schema: Optional[str] = None
) -> Callable[[ToolHandler], ToolHandler]:
    """
    Decorator used to register a tool handler.
    """

    def decorator(func: ToolHandler) -> ToolHandler:
        TOOL_HANDLERS[name] = func
        TOOL_METADATA[name] = ToolMetadata(
            name=name,
            description=description,
            argument_schema=argument_schema,
        )
        return func

    return decorator


@register_tool(
    "bash",
    description="Execute a shell command on the host. Use cautiously and prefer read-only queries.",
    argument_schema='{"command": "str (required)"}',
)
def run_bash_tool(arguments: Dict[str, Any], context: ToolContext) -> ToolExecResult:
    """
    Execute a shell command and return its output.
    """
    command = arguments.get("command", "")
    import subprocess

    timeout = context.get("tool_timeout_seconds", DEFAULT_TOOL_TIMEOUT_SECONDS)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout if result.returncode == 0 else result.stderr
        return "tool_response_done", output
    except subprocess.TimeoutExpired:
        return (
            "tool_response_done",
            f"Command timed out after {timeout} seconds: {command}",
        )


@register_tool(
    "remember",
    description="Persist a short fact relevant to future conversations in this channel.",
    argument_schema='{"memory": "str (required)"}',
)
def run_remember_tool(
    arguments: Dict[str, Any], context: ToolContext
) -> ToolExecResult:
    """
    Append a memory to persistent storage.
    """
    memory = arguments.get("memory", "")
    channel_id = context.get("channel_id", "default")
    memory_manager.append(channel_id, memory)
    return "tool_response_done", f"Memory saved for channel {channel_id}."


def build_system_prompt() -> str:
    """
    Build the system prompt with current tool metadata inserted.
    """
    tools_section_lines: List[str] = []
    for meta in TOOL_METADATA.values():
        line = f"- {meta.name}: {meta.description}"
        if meta.argument_schema:
            line += f" | Args: {meta.argument_schema}"
        tools_section_lines.append(line)
    tools_block = "\n".join(tools_section_lines) or "- no tools registered"

    if "{TOOL_INSERT_HERE}" in base_system_prompt:
        return base_system_prompt.replace("{TOOL_INSERT_HERE}", tools_block)
    return f"{base_system_prompt}\n\n# Tools:\n{tools_block}"


def push_to_ollama(prompt: str, channel_id: str) -> str:
    """
    Push a prompt to an Ollama model and return the response.
    """
    try:

        m = [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": prompt},
        ]

        memories = memory_manager.load(channel_id)
        if memories:
            m.insert(1, {"role": "system", "content": f"# Memories:\n{memories}"})

        response = c.chat(
            model=global_model,
            messages=m,
        )
        return response.message.content
    except Exception as e:
        return f"Error communicating with Ollama model: {str(e)}"


def tool_loop(usr_prompt: str, channel_id: str = "default") -> str:
    """
    Main loop to handle tool requests and responses.
    """
    channel_id = str(channel_id or "default")
    tool_context: ToolContext = {
        "channel_id": channel_id,
        "memory_manager": memory_manager,
        "tool_timeout_seconds": DEFAULT_TOOL_TIMEOUT_SECONDS,
        "tool_logger": tool_logger,
    }
    conversation = ToolConversation(
        {
            "tool": None,
            "arguments": {},
            "status": "waiting_llm_action",
            "response": usr_prompt,
        }
    )
    total_tool_requests = 0
    consecutive_tool_requests = 0
    tool_attempt_tracker: Dict[str, int] = {}
    tools_disabled = False
    tools_disabled_message = ""

    while True:
        combined_history = conversation.render()
        print(f"[DEBUG] Current prompt:\n{combined_history}\n")
        response = push_to_ollama(combined_history, channel_id)
        print(f"[DEBUG] LLM WIP Response:\n{response}\n")
        conversation.add_model_event(response)
        try:
            response_json = json.loads(
                response
            )  # Using eval here for simplicity; in production, use json.loads
            status = response_json.get("status")
            print(f"[DEBUG] Status: {status}")
            if status == "tool_request":
                print(f"[DEBUG] Tool Request: {response_json}")
                tool = response_json.get("tool")
                arguments = response_json.get("arguments", {})

                limit_reason: Optional[str] = None
                handler_found = False
                attempt_count = 0
                retry_limited = False
                if tools_disabled:
                    limit_reason = "tools_disabled_active"
                    prompt_payload = {
                        "tool": tool,
                        "arguments": arguments,
                        "status": "tool_response_done",
                        "response": tools_disabled_message,
                    }
                else:
                    total_tool_requests += 1
                    consecutive_tool_requests += 1

                    if total_tool_requests > MAX_TOTAL_TOOL_REQUESTS:
                        limit_reason = f"Tool usage limit reached ({MAX_TOTAL_TOOL_REQUESTS} total invocations)."
                    elif consecutive_tool_requests > MAX_CONSECUTIVE_TOOL_REQUESTS:
                        limit_reason = f"Consecutive tool usage limit reached ({MAX_CONSECUTIVE_TOOL_REQUESTS} in a row)."

                    if limit_reason:
                        tools_disabled = True
                        tools_disabled_message = f"{limit_reason} Please continue with the best possible answer without running more tools."
                        prompt_payload = {
                            "tool": tool,
                            "arguments": arguments,
                            "status": "tool_response_done",
                            "response": tools_disabled_message,
                        }
                    else:
                        handler = TOOL_HANDLERS.get(tool)
                        handler_found = handler is not None
                        if handler is None:
                            prompt_payload = {
                                "tool": tool,
                                "arguments": arguments,
                                "status": "waiting_llm_action",
                                "response": f"Unknown tool requested: {tool}",
                            }
                        else:
                            attempt_key = json.dumps(
                                {"tool": tool, "arguments": arguments}, sort_keys=True
                            )
                            attempt_count = tool_attempt_tracker.get(attempt_key, 0)
                            if attempt_count >= MAX_RETRIES_PER_TOOL:
                                retry_limited = True
                                prompt_payload = {
                                    "tool": tool,
                                    "arguments": arguments,
                                    "status": "tool_response_done",
                                    "response": (
                                        f"Retry limit reached for tool '{tool}' with the same arguments. "
                                        "Please adjust your approach before requesting another run."
                                    ),
                                }
                            else:
                                tool_attempt_tracker[attempt_key] = attempt_count + 1
                                attempt_count = tool_attempt_tracker[attempt_key]
                                try:
                                    next_status, tool_response = handler(
                                        arguments, tool_context
                                    )
                                except Exception as exc:  # guard against tool crashes
                                    next_status = "tool_response_done"
                                    tool_response = (
                                        f"Tool '{tool}' raised an error: {exc}"
                                    )
                                prompt_payload = {
                                    "tool": tool,
                                    "arguments": arguments,
                                    "status": next_status,
                                    "response": tool_response,
                                }

                preview, truncated = _summarize_response(prompt_payload.get("response"))
                tool_logger.log(
                    channel_id,
                    {
                        "tool": tool,
                        "arguments": arguments,
                        "status": prompt_payload.get("status"),
                        "response_preview": preview,
                        "response_truncated": truncated,
                        "tools_disabled": tools_disabled,
                        "limit_reason": limit_reason,
                        "handler_found": handler_found,
                        "retry_limited": retry_limited,
                        "attempt_count": attempt_count,
                        "total_tool_requests": total_tool_requests,
                        "consecutive_tool_requests": consecutive_tool_requests,
                    },
                )
                # Add to conversation history
                conversation.add_tool_event(prompt_payload)
            elif status == "done":
                return response_json.get("response", "No response provided.")
            elif status == "error":
                return response_json.get("response", "An error occurred.")
            else:
                return "Invalid status in response."
        except Exception as e:
            return f"Error processing response: {str(e)}"


if __name__ == "__main__":
    user_question = "List the files in the current directory."
    final_response = tool_loop(user_question)
    print("Final Response from LLM:")
    print(final_response)
