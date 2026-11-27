"""Ask the model for JSON that validates against a Pydantic schema, with repair retries.

The Generic Agent has no native structured-output mode, so the schema goes into the
prompt and the reply is extracted from text (code fences or the first balanced object).
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from generic_agent.runner import GenericAgent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def extract_json(text: str):
    for match in _FENCE.finditer(text):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    # first balanced {...}
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in model reply")


def schema_instructions(schema: type[BaseModel]) -> str:
    return (
        "Return ONLY one JSON object inside a ```json code fence. No prose before or after.\n"
        "The object must validate against this JSON Schema:\n"
        f"```json\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n```"
    )


async def ainvoke_structured(
    agent: GenericAgent,
    task: str,
    schema: type[T],
    *,
    retries: int = 2,
    **run_input: Any,
) -> T:
    """Run `agent` on `task` (+ schema instructions) and validate the JSON reply.

    `run_input` is passed to `GenericAgent.run()` (instructions, rules, guidelines, skills...).
    An invalid reply is sent back as history with a correction request.
    """
    prompt = f"{task}\n\n{schema_instructions(schema)}"
    history: list[BaseMessage] = []
    last_error = ""
    for _ in range(retries + 1):
        result = await agent.arun(prompt, history=history, **run_input)
        text = result.text
        try:
            return schema.model_validate(extract_json(text))
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:2000]
            history = history + [HumanMessage(content=prompt), AIMessage(content=text[:4000])]
            prompt = (
                "Your previous answer was not valid for the required schema.\n"
                f"Error: {last_error}\n"
                "Return the corrected JSON object only, inside a ```json fence."
            )
    raise ValueError(f"model did not return valid {schema.__name__}: {last_error}")


async def ainvoke_text(agent: GenericAgent, task: str, **run_input: Any) -> str:
    """Run `agent` on `task` and return the Markdown reply."""
    text = (await agent.arun(task, **run_input)).text
    # Strip a single wrapping ```markdown fence if the agent added one.
    m = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*)\n```\s*", text, re.DOTALL)
    return (m.group(1) if m else text).strip() + "\n"
