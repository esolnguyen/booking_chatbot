"""Intent extraction -- parses natural language into structured search parameters."""

import json
from datetime import date

from app.agents.llm import sync_chat
from app.mock.seed_data import AIRPORT_TO_CITY, CITY_TO_AIRPORTS

SYSTEM_PROMPT = """You are a search intent parser for a travel assistant.
Today's date is {today}.

Known airport codes and cities:
{airport_list}

Extract search parameters from the user's latest message. Use conversation history for context on follow-up questions (e.g., "anything cheaper?" refers to the same route).

Rules:
- Resolve city names to IATA airport codes when possible (e.g., "Sydney" -> "SYD", "Ho Chi Minh City" / "Saigon" / "HCMC" -> "SGN")
- Resolve relative dates: "tomorrow" -> actual date, "next Friday" -> actual date, etc.
- If the user asks about "flights AND hotels" or "trip options", set search_type to "both"
- If the user's message is general chat (greetings, thanks, etc.), set search_type to "general"
- For time preferences, use 24h format HH:MM
- Only set fields you can confidently extract; leave others as null

Respond with ONLY this JSON (no markdown, no explanation):
{{
    "search_type": "flight" | "hotel" | "both" | "general",
    "origin": "IATA code or null",
    "destination": "IATA code or null",
    "destination_city": "city name or null",
    "departure_date": "YYYY-MM-DD or null",
    "return_date": "YYYY-MM-DD or null",
    "arrival_time_min": "HH:MM or null",
    "arrival_time_max": "HH:MM or null",
    "departure_time_min": "HH:MM or null",
    "departure_time_max": "HH:MM or null",
    "max_price": number or null,
    "stops": number or null,
    "preferences": []
}}"""


def _build_airport_list() -> str:
    lines = []
    for code, city in sorted(AIRPORT_TO_CITY.items()):
        lines.append(f"  {code} = {city}")
    return "\n".join(lines)


def extract_intent(
    user_message: str,
    conversation_history: list[dict],
    provider: str | None = None,
) -> dict:
    """Extract structured search intent from a user message."""
    system = SYSTEM_PROMPT.format(
        today=date.today().isoformat(),
        airport_list=_build_airport_list(),
    )

    messages = [{"role": "system", "content": system}]
    # Include recent conversation for follow-up context (last 6 turns)
    for m in conversation_history[-6:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_message})

    try:
        content = sync_chat(messages, temperature=0.0, provider=provider).strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        return json.loads(content)
    except Exception:
        return {"search_type": "general"}
