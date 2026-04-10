"""text-analyzer-bench - direct strategy: sends the task instruction directly to an LLM.

Supports 3 backends:
  cli           - uses `claude -p`
  api           - uses Anthropic Python SDK
  openai_compat - uses an OpenAI-compatible endpoint

Meta-agent may edit everything above the [HARBOR ADAPTER - FIXO] boundary.
"""

import json
import os
import subprocess
import tempfile
import time

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# ============================================================
# SYSTEM PROMPT - primary optimization surface
# ============================================================
SYSTEM_PROMPT = """You are a precise text analysis assistant. Follow the output format exactly as
specified in each task. Do not add explanations or extra text beyond the format.
"""

# ============================================================
# Runner configuration
# ============================================================
BACKEND = "cli"
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_TIMEOUT = 120
CLAUDE_MAX_RETRIES = 3
API_KEY_ENV = "ANTHROPIC_API_KEY"
BASE_URL = ""


def _run_cli(prompt: str) -> str:
    for attempt in range(CLAUDE_MAX_RETRIES):
        try:
            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    prompt,
                    "--output-format",
                    "json",
                    "--max-turns",
                    "1",
                    "--tools",
                    "",
                    "--model",
                    CLAUDE_MODEL,
                ],
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT,
            )

            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                if "429" in err or "rate" in err.lower():
                    if attempt < CLAUDE_MAX_RETRIES - 1:
                        time.sleep(30 * (attempt + 1))
                        continue
                raise RuntimeError(
                    f"claude -p failed (exit {result.returncode}): {err[:300]}"
                )

            data = json.loads(result.stdout)
            return data.get("result", "").strip()

        except subprocess.TimeoutExpired:
            if attempt < CLAUDE_MAX_RETRIES - 1:
                time.sleep(10)
                continue
            raise RuntimeError(f"claude -p timed out after {CLAUDE_TIMEOUT}s")

    raise RuntimeError("claude -p: max retries exceeded")


def _run_api(prompt: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "anthropic package not installed. Add 'anthropic' to pyproject.toml dependencies."
        )

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"API key not found. Set the {API_KEY_ENV} environment variable."
        )

    client = anthropic.Anthropic(api_key=api_key)

    for attempt in range(CLAUDE_MAX_RETRIES):
        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text.strip()
        except Exception as exc:
            err = str(exc)
            if "429" in err or "rate" in err.lower():
                if attempt < CLAUDE_MAX_RETRIES - 1:
                    time.sleep(30 * (attempt + 1))
                    continue
            raise RuntimeError(f"Anthropic API error: {err[:300]}")

    raise RuntimeError("Anthropic API: max retries exceeded")


def _run_openai_compat(prompt: str) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError(
            "openai package not installed. Add 'openai' to pyproject.toml dependencies."
        )

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"API key not found. Set the {API_KEY_ENV} environment variable."
        )

    client = OpenAI(api_key=api_key, base_url=BASE_URL or None)

    for attempt in range(CLAUDE_MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=CLAUDE_MODEL,
                messages=[{"role": "user", "content": prompt}],
                timeout=CLAUDE_TIMEOUT,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            err = str(exc)
            if "429" in err or "rate" in err.lower():
                if attempt < CLAUDE_MAX_RETRIES - 1:
                    time.sleep(30 * (attempt + 1))
                    continue
            raise RuntimeError(f"OpenAI-compat API error: {err[:300]}")

    raise RuntimeError("OpenAI-compat API: max retries exceeded")


def run_llm(instruction: str) -> str:
    prompt = f"{SYSTEM_PROMPT}\n\n{instruction}" if SYSTEM_PROMPT.strip() else instruction

    if BACKEND == "cli":
        return _run_cli(prompt)
    if BACKEND == "api":
        return _run_api(prompt)
    if BACKEND == "openai_compat":
        return _run_openai_compat(prompt)
    raise RuntimeError(
        f"Unknown backend: {BACKEND!r}. Choose cli | api | openai_compat"
    )


# ============================================================
# [HARBOR ADAPTER - FIXO] - do not edit this section
# ============================================================
class TextAnalyzerAgent(BaseAgent):
    """Harbor-compatible agent (direct strategy)."""

    @staticmethod
    def name() -> str:
        return "text-analyzer-bench"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        await environment.exec("mkdir -p /logs/agent")

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        output = run_llm(instruction)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as file:
            file.write(output)
            tmp_path = file.name

        await environment.upload_file(tmp_path, "/logs/agent/output.txt")
        os.unlink(tmp_path)

        context.metadata = {"output_length": len(output)}
