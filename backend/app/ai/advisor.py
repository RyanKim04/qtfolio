"""AI analyst — Claude Fable 5 with server-side web search, streamed as SSE.

Owner-directed design (2026-08-13): the analyst KNOWS the owner's aggressive
tech-focused profile, researches upcoming catalysts (earnings, Fed, macro) with
web search, and never emits boilerplate advisory disclaimers. Fable 5 rules per
the API docs: thinking always on (omit the param or explicit adaptive only),
no sampling params, refusal fallback to Opus 4.8 opted in by default.
"""

import json
import logging
from typing import AsyncIterator

from ..config import settings as env

log = logging.getLogger(__name__)

MODEL = "claude-fable-5"
FALLBACK_MODEL = "claude-opus-4-8"
BETAS = ["server-side-fallback-2026-06-01"]

DRAFT_SEARCHES = 12
CRITIC_SEARCHES = 5
REFINE_SEARCHES = 4
CHAT_SEARCHES = 5

STYLE = """\
Style rules (owner's explicit preferences):
- NEVER include advisory boilerplate: no "not financial advice", "do your own research",
  "consult a professional", "investing involves risk" or any variant. The owner built this
  tool and finds that copy insulting.
- ANALYZE, don't advise. The owner explicitly does NOT want trade recommendations, position-
  sizing instructions, buy/sell/trim calls, or action items. Describe what IS: the state of
  the portfolio, the facts, the upcoming events, what the market is pricing. You may state
  what a development would mean for the portfolio ("an NVDA miss would hit 43% of the book"),
  but never tell the owner what to do about it.
- Always respond in ENGLISH, regardless of what language the user writes in.
- Use markdown. Format tables as proper GitHub tables — header row, separator row, and each
  row on its OWN line. Cite concrete numbers from the portfolio context.
- Dates matter: today's date is in the portfolio context — check event dates against it."""

ANALYSIS_TASK = """\
Produce the owner's portfolio briefing. Use web search to ground it in what's true TODAY —
verify upcoming dates rather than recalling them.

Write it to the standard of a senior sell-side analyst's internal desk note — the calibre of
a bulge-bracket morning meeting brief: tight professional prose, precise figures (%, bps, $,
implied moves), scenario tables, measured probability language ("we see risk skewed...",
"consensus embeds..."), zero emoji, zero filler. Open with a dated title line and a 2-3
sentence executive summary stating the portfolio's current posture and the dominant near-term
risk factor.

Structure:

1. **Position & Performance Review** — the book as it stands: concentration, effective leverage
   exposure, lifetime P&L attribution by name, cash position, recent tape action. Diagnosis
   only — no prescriptions.
2. **Catalyst Calendar & Expected Impact** (researched) — earnings for held names (RDDT, GOOG;
   underlying drivers for SOXL/TQQQ/SMH/QQQ/VOO: NVDA/AVGO/TSM etc.), next FOMC + current
   rate-cut odds, CPI/jobs prints. For EACH catalyst: consensus expectations, what's priced in
   (implied move, positioning, run-up), beat/inline/miss scenarios with the likely price
   reaction of the affected holdings, and this portfolio's exposure to the event. Close each
   with a one-line probability-weighted read ("market prices X; asymmetry to the downside
   because Y"). Use a table where it compresses well.
3. **Single-Name Review** (researched) — per significant holding: recent price action, latest
   material developments, analyst-consensus shifts, prevailing narrative — and where that
   narrative could break, in either direction. Facts and forward-looking assessment, not
   trade verdicts.

Target length: a focused note the owner reads in 3-4 minutes. Depth over breadth."""

CRITIC_SYSTEM = """\
You are the desk's devil's advocate — a skeptical senior risk analyst red-teaming a colleague's
draft portfolio briefing before it reaches the owner. Your only job is to attack the draft:

- Verify suspicious factual claims (dates, consensus numbers, rate-cut odds) with web search —
  flag anything stale, wrong, or unverifiable.
- Challenge every impact scenario: what is the credible bear case against each bullish read and
  the bull case against each bearish read? What is the draft NOT pricing?
- Flag overconfident probability language, internal inconsistencies, and claims that lean on
  recalled (rather than verified) information.
- Identify material risks or upcoming events the draft missed entirely.

Output a numbered critique, most damaging first. Be harsh, specific, and cite what you verified.
No praise, no summary of the good parts, no rewritten report — critique only."""

REFINE_TASK = """\
A skeptical reviewer red-teamed your draft. Their critique follows below. Produce the FINAL
briefing: correct factual errors, address the valid critiques (verify with search where the
reviewer flagged uncertainty), fold the strongest counter-arguments into your scenario analysis
so each read is two-sided, and drop or soften any claim that did not survive review. Keep the
same dated-title + executive-summary + 3-section structure and desk-note standard. Output ONLY
the final report — no meta-commentary about the review process, no mention of the reviewer.

REVIEWER CRITIQUE:
"""


def _client():
    from anthropic import AsyncAnthropic

    if not env.anthropic_api_key:
        return None
    return AsyncAnthropic(api_key=env.anthropic_api_key)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _tools(max_uses: int) -> list[dict]:
    return [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]


async def _run_stream(
    client,
    system: str,
    messages: list[dict],
    max_uses: int,
    text_sink: list[str] | None = None,
    emit_text: bool = True,
    suppress_done: bool = False,
) -> AsyncIterator[str]:
    """Drive one streamed request; translate API events into UI SSE events.
    text_sink collects the emitted text (for multi-phase pipelines); emit_text=False
    keeps a phase's prose off the UI while still surfacing progress events."""
    from anthropic import APIStatusError

    kwargs = dict(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=messages,
        tools=_tools(max_uses),
        thinking={"type": "adaptive", "display": "summarized"},
        betas=BETAS,
        fallbacks=[{"model": FALLBACK_MODEL}],
    )
    try:
        stream_cm = client.beta.messages.stream(**kwargs)
    except TypeError:  # older SDK without fallbacks param
        kwargs.pop("betas", None)
        kwargs.pop("fallbacks", None)
        stream_cm = client.beta.messages.stream(**kwargs)

    try:
        async with stream_cm as stream:
            async for event in stream:
                et = event.type
                if et == "content_block_start":
                    block = event.content_block
                    if block.type == "server_tool_use":
                        yield _sse({"type": "progress", "kind": "search", "detail": ""})
                    elif block.type == "thinking":
                        yield _sse({"type": "phase", "detail": "thinking"})
                elif et == "content_block_delta":
                    d = event.delta
                    if d.type == "text_delta":
                        if text_sink is not None:
                            text_sink.append(d.text)
                        if emit_text:
                            yield _sse({"type": "text", "delta": d.text})
                    elif d.type == "thinking_delta" and d.thinking:
                        yield _sse({"type": "thinking", "delta": d.thinking})
                    elif d.type == "input_json_delta":
                        pass  # search query streamed as partial JSON; surfaced on block stop
                elif et == "content_block_stop":
                    block = getattr(event, "content_block", None)
                    if block is not None and block.type == "server_tool_use":
                        query = (block.input or {}).get("query") if isinstance(block.input, dict) else None
                        if query:
                            yield _sse({"type": "progress", "kind": "search", "detail": query})
                    elif block is not None and block.type == "web_search_tool_result":
                        content = block.content
                        n = len(content) if isinstance(content, list) else 0
                        yield _sse({"type": "progress", "kind": "results", "detail": f"{n} results"})

            final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                yield _sse({"type": "error", "message": "The model declined this request (safety classifier). Try rephrasing."})
                return
            if not suppress_done:
                usage = final.usage
                yield _sse({
                    "type": "done",
                    "model": final.model,
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                })
    except APIStatusError as exc:
        log.warning("AI request failed: %s %s", exc.status_code, exc.message)
        yield _sse({"type": "error", "message": f"Anthropic API error {exc.status_code}: {exc.message}"})
    except Exception as exc:
        log.warning("AI stream failed: %s", exc)
        yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


async def stream_analysis(state, profile: str, portfolio_context: str) -> AsyncIterator[str]:
    """Three-phase pipeline: draft → devil's-advocate critique → refined final.
    Only the final phase's prose reaches the UI; earlier phases surface as
    progress events so the user watches the desk work."""
    client = _client()
    if client is None:
        yield _sse({"type": "error", "message": "ANTHROPIC_API_KEY is not set — add it to .env and restart the backend."})
        return
    system = (
        "You are the resident portfolio analyst inside qtfolio, the owner's private portfolio terminal. "
        "You have their live portfolio data and web search.\n\n"
        f"INVESTOR PROFILE:\n{profile}\n\n{STYLE}\n\n"
        f"LIVE PORTFOLIO DATA (authoritative, computed from their brokerage):\n{portfolio_context}"
    )

    def errored(chunk: str) -> bool:
        try:
            return json.loads(chunk[6:]).get("type") == "error"
        except json.JSONDecodeError:
            return False

    # Phase 1 — draft (text stays off the UI)
    yield _sse({"type": "progress", "kind": "phase", "detail": "Drafting analysis"})
    draft_parts: list[str] = []
    async for chunk in _run_stream(client, system, [{"role": "user", "content": ANALYSIS_TASK}],
                                   DRAFT_SEARCHES, text_sink=draft_parts, emit_text=False, suppress_done=True):
        yield chunk
        if errored(chunk):
            return
    draft = "".join(draft_parts)
    if not draft.strip():
        yield _sse({"type": "error", "message": "Draft phase produced no output."})
        return

    # Phase 2 — devil's advocate (also hidden)
    yield _sse({"type": "progress", "kind": "phase", "detail": "Devil's advocate review"})
    critique_parts: list[str] = []
    critic_messages = [{"role": "user", "content": f"DRAFT BRIEFING TO RED-TEAM:\n\n{draft}"}]
    async for chunk in _run_stream(client, CRITIC_SYSTEM + f"\n\nCONTEXT — the owner's live portfolio data:\n{portfolio_context}",
                                   critic_messages, CRITIC_SEARCHES, text_sink=critique_parts,
                                   emit_text=False, suppress_done=True):
        yield chunk
        if errored(chunk):
            return
    critique = "".join(critique_parts)

    # Phase 3 — refine (this is what the user sees)
    yield _sse({"type": "progress", "kind": "phase", "detail": "Refining final report"})
    refine_messages = [
        {"role": "user", "content": ANALYSIS_TASK},
        {"role": "assistant", "content": draft},
        {"role": "user", "content": REFINE_TASK + critique},
    ]
    final_parts: list[str] = []
    async for chunk in _run_stream(client, system, refine_messages, REFINE_SEARCHES,
                                   text_sink=final_parts, emit_text=True, suppress_done=False):
        try:
            payload = json.loads(chunk[6:])
            if payload.get("type") == "done":
                payload["full_text"] = "".join(final_parts)
                yield _sse(payload)
                continue
        except json.JSONDecodeError:
            pass
        yield chunk


async def stream_chat(state, profile: str, portfolio_context: str, last_analysis: str | None, messages: list[dict]) -> AsyncIterator[str]:
    client = _client()
    if client is None:
        yield _sse({"type": "error", "message": "ANTHROPIC_API_KEY is not set — add it to .env and restart the backend."})
        return
    system = (
        "You are the resident portfolio analyst inside qtfolio, the owner's private portfolio terminal, "
        "in an ongoing conversation with the owner. You have their live portfolio data and web search — "
        "search when freshness matters (prices, news, dates), answer directly when it doesn't.\n\n"
        f"INVESTOR PROFILE:\n{profile}\n\n{STYLE}\n\n"
        f"LIVE PORTFOLIO DATA:\n{portfolio_context}"
        + (f"\n\nYOUR MOST RECENT BRIEFING (for continuity):\n{last_analysis[:6000]}" if last_analysis else "")
    )
    convo = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    async for chunk in _run_stream(client, system, convo, CHAT_SEARCHES):
        yield chunk
