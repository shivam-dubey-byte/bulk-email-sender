"""
Company research agent — powered by an NVIDIA NIM model (OpenAI-compatible API).

The top-level agent can call two tools:
  - web_search: free DuckDuckGo lookup, no extra API key needed.
  - spawn_subagent: delegates a focused sub-task to a brand-new agent instance
    (fresh message history, depth+1). This is how the orchestrator "creates
    sub-agents as needed" instead of doing everything in one flat context.

Depth and sub-agent-count are capped to keep runs bounded on a free API tier.
"""

import json

from openai import OpenAI

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

RECOMMENDED_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b",  # most capable, built for agentic/tool-calling tasks
    "nvidia/nemotron-3-super-120b-a12b",  # faster, still agentic
    "nvidia/nemotron-3-nano-30b-a3b",     # fastest/cheapest, still agentic-tuned
]

SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused research sub-agent. Answer only the specific task you were given, "
    "using web_search for facts. Be concise and factual. Do not invent facts you couldn't find."
)

ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are a lead research orchestrator. Your job: produce a short, factual 2-4 sentence "
    "blurb about a company, suitable as a personalized opener in a sales/outreach email "
    "(what the company does, its industry, and one notable fact if you can find one). "
    "Use the web_search tool for facts. If it helps, break the work into independent parts "
    "(e.g. 'core business', 'recent news') and delegate each part to spawn_subagent, then "
    "combine the sub-agents' answers into the final blurb yourself. Never invent facts — "
    "if you can't find something, leave it out. Reply with ONLY the final blurb text, no preamble."
)


def _web_search(query: str, max_results: int = 5) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # fallback for older package name

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return "No results found."
    return "\n".join(f"- {r.get('title', '')}: {r.get('body', '')} ({r.get('href', '')})" for r in results)


def _tools_for_depth(depth: int, max_depth: int) -> list:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for facts. Use for finding what a company does, its industry, size, or recent news.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "search query"}},
                    "required": ["query"],
                },
            },
        }
    ]
    if depth < max_depth:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "spawn_subagent",
                    "description": (
                        "Delegate a focused, independent sub-task to a brand-new sub-agent "
                        "(its own fresh context). Use this to split research into parallelizable parts."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"task": {"type": "string", "description": "the specific sub-task to research"}},
                        "required": ["task"],
                    },
                },
            }
        )
    return tools


class ResearchAgent:
    def __init__(self, api_key: str, model: str, max_depth: int = 2, max_subagents: int = 6, max_iterations: int = 6):
        self.client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=api_key)
        self.model = model
        self.max_depth = max_depth
        self.max_subagents = max_subagents
        self.max_iterations = max_iterations
        self.subagent_count = 0

    def _run(self, system_prompt: str, user_prompt: str, depth: int) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tools = _tools_for_depth(depth, self.max_depth)

        for _ in range(self.max_iterations):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.3,
            )
            msg = resp.choices[0].message

            if not msg.tool_calls:
                return (msg.content or "").strip()

            messages.append(msg)
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if name == "web_search":
                    result = _web_search(args.get("query", ""))
                elif name == "spawn_subagent" and depth < self.max_depth and self.subagent_count < self.max_subagents:
                    self.subagent_count += 1
                    result = self._run(SUBAGENT_SYSTEM_PROMPT, args.get("task", ""), depth + 1)
                elif name == "spawn_subagent":
                    result = "Sub-agent budget exhausted — answer directly using web_search instead."
                else:
                    result = f"Unknown tool: {name}"

                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        return "(Research incomplete — hit iteration limit.)"

    def research_company(self, company_name: str, hint: str = "") -> str:
        self.subagent_count = 0
        user_prompt = f"Company: {company_name}. {hint}".strip()
        return self._run(ORCHESTRATOR_SYSTEM_PROMPT, user_prompt, depth=0)
