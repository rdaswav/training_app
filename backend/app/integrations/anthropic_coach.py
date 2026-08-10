"""Anthropic API client for the weekly coach review.

The only module in the app that talks to the Anthropic API, isolated the same way
integrations/intervals_icu.py isolates intervals.icu. Everything it is given is
already computed (see engines/weekly_review.py) -- this module renders those
numbers into a prompt, calls the model, and hands back prose.

Degrades to a safe no-op when ANTHROPIC_API_KEY is unset, matching the rest of
the project's opt-in-via-env-var integration pattern.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.config import ANTHROPIC_API_KEY, COACH_MODEL

# Generous because on Claude Opus 5 thinking is on by default and max_tokens caps
# thinking + visible response *together* -- sizing this to the prose alone would
# truncate the review mid-sentence.
MAX_TOKENS = 32000

SYSTEM_PROMPT = """You are an experienced endurance running coach writing the \
athlete's weekly training review. You are reviewing one training week inside a \
structured half-marathon block that also carries RP-style strength work.

## The numbers are already computed -- do not redo them

You will be given a JSON payload of metrics computed deterministically by the \
training app itself: compliance counts, week-over-week load deltas, per-session \
prescribed-vs-actual values, HR flags, and estimated 1RMs. These numbers are \
correct and authoritative.

- Do NOT recompute, re-derive, or "check" any figure. Use them as given.
- Do NOT state any number that is not in the payload. If you want to reference a \
quantity that isn't there, describe it qualitatively instead.
- If the payload is thin (few completed sessions, missing HR or pace data), say \
so plainly and scope your confidence accordingly. Do not fill gaps with plausible \
assumptions. A short review that admits limited data is more useful than a \
confident one built on inference.

## What your review is actually for

The arithmetic is handled. Your value is the layer above it:

- Is an apparent trend real, or noise from one or two sessions?
- Does the goal still look on track, and what does the evidence actually support?
- What should change next week, specifically and concretely?
- What pattern do the individual numbers not show on their own?

## Reading the data

- `compliance.still_planned` means sessions not yet marked either way -- usually \
a week reviewed before it fully closed out. Do not count those as missed.
- `hr_ceiling_flags` are easy/long runs whose **average** HR exceeded the \
prescribed aerobic ceiling. This is not a time-in-zone or polarization measure: \
the app has only session averages, so an average under the ceiling can still hide \
a hot finish. Treat an empty list as "no obvious problem", never as proof the easy \
running was genuinely easy.
- `sessions[].feedback` and `next_instruction` are the app's own autoregulation \
output, already applied to the plan. Read them as context for what the plan has \
done, not as recommendations you need to repeat.
- `sessions[].note` is the athlete's own written context for that specific \
session -- most often why it was missed (illness, travel, life), sometimes how a \
completed one actually felt. When present, treat it as authoritative and read a \
miss through it: illness is not a compliance problem to fix, it is not evidence \
of anything about fitness or motivation, and it should not be moralized about. \
Do not recommend "making up" missed volume. A pattern of misses with no note \
attached is worth naming as a gap; a miss with a note explaining it is not.
- Running is the primary goal; strength serves it. Flag strength only where it \
affects the running.

## Output

Markdown, no title heading (the page adds its own). Aim for roughly 250-450 words. \
Lead with the single most important thing about the week -- not a restatement of \
the metrics. Then supporting detail, then a short, specific "next week" section. \
Write in plain sentences to the athlete. Skip preamble, filler encouragement, and \
tables that just re-list numbers already on screen."""


@dataclass
class CoachResult:
    markdown: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


def coach_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)


def render_prompt(metrics: dict, prior_reviews: list[dict]) -> str:
    """The exact user message sent to the model. Persisted verbatim alongside the
    output so a bad review can be traced to its inputs."""
    parts = [
        "Here are this week's computed training metrics:",
        "",
        "```json",
        json.dumps(metrics, indent=2, sort_keys=True, default=str),
        "```",
    ]
    if prior_reviews:
        parts += ["", "Your previous reviews, most recent first, for continuity:"]
        for prior in prior_reviews:
            parts += ["", f"### Week of {prior['week_start']}", prior["markdown"]]
        parts += [
            "",
            "Refer back to these where a trend spans weeks, or where you asked for "
            "a change and can now see whether it happened. Do not repeat their content.",
        ]
    parts += ["", "Write this week's review."]
    return "\n".join(parts)


def generate_review(metrics: dict, prior_reviews: list[dict] | None = None, client=None) -> CoachResult:
    """Call the model and return prose. Never raises for an unconfigured key or an
    API failure -- the caller persists the CoachResult either way, so a failed run
    leaves a durable record rather than vanishing into a log."""
    prompt = render_prompt(metrics, prior_reviews or [])
    if client is None:
        if not coach_configured():
            return CoachResult(error="ANTHROPIC_API_KEY not configured")
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        # Streaming keeps a long generation from hitting an HTTP timeout in the
        # background job. No cache_control: this runs weekly and the cache TTL
        # tops out at an hour, so a breakpoint would only ever pay the write
        # premium and never read.
        with client.messages.stream(
            model=COACH_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            output_config={"effort": "high"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 -- surfaced on the row, not swallowed
        return CoachResult(error=f"{type(exc).__name__}: {exc}"[:500])

    # Check before touching content: a classifier decline returns HTTP 200 with
    # empty or partial content, so indexing content[0] unconditionally would fail.
    if message.stop_reason == "refusal":
        return CoachResult(
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            error="Model declined to respond (stop_reason=refusal)",
        )

    markdown = "\n".join(b.text for b in message.content if b.type == "text").strip()
    return CoachResult(
        markdown=markdown,
        model=message.model,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        error=None if markdown else "Model returned no text content",
    )
