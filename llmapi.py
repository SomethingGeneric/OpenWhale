import json,os
from ollama import Client

c = Client(
    host="http://localhost:11434",
)

global_model = "qwen3-vl:4b"

system_prompt = open('brain/SOUL.md').read().strip()
# TODO: dynamically insert tool descriptions here
# and generate this string
system_prompt = system_prompt.replace("{TOOL_INSERT_HERE}", open('brain/TOOLS.md').read().strip())


def push_to_ollama(prompt: str) -> str:
    """
    Push a prompt to an Ollama model and return the response.
    """
    try:

        m = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        if os.path.exists("brain/MEMORIES.md"):
            memories = open("brain/MEMORIES.md").read().strip()
            m.insert(1, {"role": "system", "content": f"# Memories:\n{memories}"})

        response = c.chat(
            model=global_model,
            messages=m,
        )
        return response.message.content
    except Exception as e:
        return f"Error communicating with Ollama model: {str(e)}"


def tool_loop(usr_prompt: str) -> str:
    """
    Main loop to handle tool requests and responses.
    """
    # Wrap the initial user prompt in JSON format
    prompt = json.dumps(
        {"tool": None, "arguments": {}, "status": "done", "response": usr_prompt}
    )
    # Keep track of conversation history
    conversation_history = [prompt]

    while True:
        combined_history = "\n".join(conversation_history)
        print(f"[DEBUG] Current prompt:\n{combined_history}\n")
        response = push_to_ollama(combined_history)
        print(f"[DEBUG] LLM WIP Response:\n{response}\n")
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
                if tool == "bash":
                    command = arguments.get("command", "")
                    import subprocess

                    result = subprocess.run(
                        command, shell=True, capture_output=True, text=True
                    )
                    tool_response = (
                        result.stdout if result.returncode == 0 else result.stderr
                    )
                    # Wrap the tool response in JSON format so the model knows the tool is done
                    prompt = json.dumps(
                        {
                            "tool": tool,
                            "arguments": arguments,
                            "status": "tool_response_done",
                            "response": tool_response,
                        }
                    )
                if tool == "remember":
                    memory = arguments.get("memory", "")
                    with open("brain/MEMORIES.md", "a") as f:
                        f.write(f"\n- {memory}\n")
                    prompt = json.dumps(
                        {
                            "tool": tool,
                            "arguments": arguments,
                            "status": "tool_response_done",
                            "response": "Memory saved.",
                        }
                    )
                else:  # Unknown tool requested
                    prompt = json.dumps(
                        {
                            "tool": tool,
                            "arguments": arguments,
                            "status": "done",
                            "response": f"Unknown tool requested: {tool}",
                        }
                    )
                # Add to conversation history
                conversation_history.append(response)
                conversation_history.append(prompt)
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
