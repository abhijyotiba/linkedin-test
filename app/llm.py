"""Claude client: one structured call, optional server-side web search, cost accounting, offline fake for tests."""
import json
import os
import re
from dataclasses import dataclass

PRICES = {  # $ per 1M tokens: input, output, cache read
    "claude-opus-5-5": (4.0, 20.0, 0.2), "claude-sonnet-5-5": (2.0, 10.0, 0.2), "claude-haiku-5-5": (0.1, 0.5, 0.01),
    "claude-opus-5": (5.0, 25.0, 0.5), "claude-fable-5-1": (10.0, 50.0, 1.0),
}
MODELS = list(PRICES)


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage"):
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.cost_usd += other.cost_usd


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium", fallbacks: bool = True):
        self.model, self.effort, self.fallbacks = model, effort, fallbacks
        self.fake = os.environ.get("FAKE_LLM") == "1"
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(max_retries=3)
        return self._client

    async def structured(self, *, system: str, user: str, schema: dict, effort: str | None = None,
                         web_search: bool = False, max_uses: int = 5, max_tokens: int = 12000) -> tuple[dict, Usage]:
        """Return (parsed JSON matching schema, usage). Web search runs server-side when enabled."""
        if self.fake:
            return fake_output(schema), Usage()
        import anthropic
        kwargs = dict(model=self.model, max_tokens=max_tokens,
                      system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                      messages=[{"role": "user", "content": user}],
                      output_config={"effort": effort or self.effort, "format": {"type": "json_schema", "schema": api_schema(schema)}})
        if web_search:
            kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]
        usage = Usage()
        for _ in range(6):  # pause_turn continuation loop for long web searches
            try:
                if self.fallbacks:
                    resp = await self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
                else:
                    resp = await self.client.messages.create(**kwargs)
            except anthropic.BadRequestError as e:
                raise LLMError(f"Claude rejected the request: {e.message}") from e
            except anthropic.AuthenticationError as e:
                raise LLMError("ANTHROPIC_API_KEY is missing or invalid") from e
            except anthropic.RateLimitError as e:
                raise LLMError("Claude rate limit hit after retries; try again in a minute") from e
            except anthropic.APIStatusError as e:
                raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
            except anthropic.APIConnectionError as e:
                raise LLMError("Could not reach the Claude API (network)") from e
            usage.add(self._usage(resp))
            if resp.stop_reason == "pause_turn":
                kwargs["messages"] = kwargs["messages"] + [{"role": "assistant", "content": resp.content}]
                continue
            if resp.stop_reason == "refusal":
                raise LLMError("Claude declined this request (safety refusal)")
            if resp.stop_reason == "max_tokens":
                raise LLMError("Output hit max_tokens; raise it or shorten the prompt")
            text = next((b.text for b in resp.content if b.type == "text"), "")
            return parse_json(text), usage
        raise LLMError("web search did not finish after 6 continuations")

    def _usage(self, resp) -> Usage:
        u = resp.usage
        pin, pout, pcache = PRICES.get(self.model, (4.0, 20.0, 0.2))
        cached = getattr(u, "cache_read_input_tokens", 0) or 0
        cost = (u.input_tokens * pin + u.output_tokens * pout + cached * pcache) / 1_000_000
        return Usage(u.input_tokens + cached, u.output_tokens, cost)


def api_schema(schema):
    """Schema without minItems: the API gets plain JSON Schema; counts are enforced in code (and honoured by the fake)."""
    if isinstance(schema, dict):
        return {k: api_schema(v) for k, v in schema.items() if k != "minItems"}
    return [api_schema(x) for x in schema] if isinstance(schema, list) else schema


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"model returned invalid JSON: {e}") from e


# ----------------------------------------------------------------- fake ----
LOREM = ("The checklist that shipped on Monday. " * 12).strip()


def fake_output(schema: dict, key: str = "") -> object:
    """Deterministic, schema-shaped data so the whole pipeline runs offline (FAKE_LLM=1)."""
    t = schema.get("type")
    if t == "object":
        return {k: fake_output(v, k) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        n = schema.get("minItems", 2)
        return [fake_output(schema.get("items", {"type": "string"}), key) for _ in range(n)]
    if t == "boolean":
        return True
    if t in ("number", "integer"):
        return 7
    if key == "hook":
        return "Testing hook for the pipeline."
    if key == "text":
        body = "Testing hook for the pipeline.\n\nA short story line about a real operator.\nSecond line of the story.\n\n1. First lesson with a number: 40%.\n2. Second lesson about the process.\n3. Third lesson about measurement.\n\n"
        return body + LOREM + "\n\nWhat would you do first? Save this for Monday."
    if key == "comment":
        return "First line adds an insight.\nSecond line adds a number: 23%.\nWhat do you see in your team?"
    if key == "message":
        return "Hey [Name], saw your post on topic. The point about the specific detail hit me. Building in the same space, would love to connect."
    if key == "source":
        return "https://example.com/source"
    if key in ("title",):
        return "Five words make a title"
    if key == "body":
        return "Twenty words of slide body text explaining one concrete step a reader can take today without tools."
    if key == "angle":
        return "story"
    if key == "fix":
        return ""
    return f"fake {key or 'value'}"
