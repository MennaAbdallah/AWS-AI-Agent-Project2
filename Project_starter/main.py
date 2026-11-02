"""
Customer Support AI Agent
=========================
An e-commerce support assistant deployed on Amazon Bedrock AgentCore Runtime.

Capabilities
  * Order tracking + refunds        -> AgentCore Gateway (MCP): API Gateway target + Lambda target
  * Product / policy / loyalty Q&A  -> Bedrock Knowledge Base (RAG, Retrieve API)
  * Cross-session memory            -> AgentCore Memory (semantic facts + user preferences)
  * Exact loyalty math              -> AgentCore Code Interpreter (sandboxed Python)
  * Live web lookups                -> AgentCore Browser

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
app = BedrockAgentCoreApp()

# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
GATEWAY_URL = "https://customersupportgateway-fth3piuv5p.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
KB_ID       = "HUODBWMQSV"
REGION      = "us-east-1"
MEMORY_ID   = "CustomerSupportMemory-h66BVABF6A"


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
model_id = "us.amazon.nova-2-lite-v1:0"

# Foundation model used by the Strands agent.
model = BedrockModel(model_id=model_id)

# Client for AgentCore Memory (events + long-term memory record retrieval).
memory_client = MemoryClient(region_name=REGION)

# Client for the Bedrock Knowledge Base Retrieve API.
_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    namespaces: Dict[str, str] = {}
    try:
        strategies = mem_client.get_memory_strategies(memory_id)
        for strategy in strategies:
            strategy_type = strategy.get("memoryStrategyType") or strategy.get("type")
            templates = strategy.get("namespaceTemplates") or strategy.get("namespaces") or []

            if strategy_type and templates:
                namespaces[strategy_type] = templates[0]
    except Exception as e:
        logger.warning("GetMemory restricted by lab IAM policy; falling back to default namespaces: %s", e)

    if not namespaces:
        namespaces = {
            "facts": "cs_agent/{actorId}/facts",
            "preferences": "cs_agent/{actorId}/preferences",
        }

    return namespaces


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
def _plain_text_of(message: dict):
    """Return the text of a plain-text message, or None if it is not plain text."""
    content = message.get("content") or []
    if not content:
        return None
    if any(("toolResult" in block) or ("toolUse" in block) for block in content):
        return None
    texts = [block["text"] for block in content if "text" in block]
    return "\n".join(texts) if texts else None


class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_id = memory_id
        self.memory_client = memory_client
        self.namespaces = get_namespaces(self.memory_client, self.memory_id)
        self._raw_query = ""

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        try:
            messages = event.agent.messages
            if not messages:
                return
            last = messages[-1]

            if last.get("role") != "user":
                return
            user_query = _plain_text_of(last)
            if not user_query:
                return
            self._raw_query = user_query

            memories = []
            for strategy_type, template in self.namespaces.items():
                namespace = template.format(actorId=self.actor_id)
                results = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=namespace,
                    query=user_query,
                    top_k=5,
                )
                for record in results or []:
                    text = (record.get("content") or {}).get("text", "").strip()
                    if text:
                        memories.append(f"[{strategy_type}] {text}")

            if memories:
                context = "\n".join(memories)
                last["content"][0]["text"] = (
                    f"Customer Context:\n{context}\n\n{user_query}"
                )
        except Exception as e:
            logger.warning("Memory retrieval failed: %s", e)

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        try:
            messages = event.agent.messages
            customer_query = ""
            agent_response = ""

            for message in reversed(messages):
                text = _plain_text_of(message)
                if not text:
                    continue
                if message.get("role") == "assistant" and not agent_response:
                    agent_response = text
                elif message.get("role") == "user" and not customer_query:
                    customer_query = text
                if customer_query and agent_response:
                    break

            if self._raw_query:
                customer_query = self._raw_query

            if customer_query and agent_response:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=self.session_id,
                    messages=[
                        (customer_query, "USER"),
                        (agent_response, "ASSISTANT"),
                    ],
                )
        except Exception as e:
            logger.warning("Memory save failed: %s", e)

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register both memory callbacks."""
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    """
    if not KB_ID or KB_ID.startswith("<"):
        return "Knowledge base not configured."

    try:
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )
        results = resp.get("retrievalResults", [])
        if not results:
            return "No relevant information found in the knowledge base."

        chunks = [
            r["content"]["text"]
            for r in results
            if r.get("content", {}).get("text")
        ]
        if not chunks:
            return "No relevant information found in the knowledge base."
        return "\n---\n".join(chunks)
    except Exception as e:
        logger.error("Knowledge base search failed: %s", e)
        return f"Knowledge base search failed: {e}"


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
_TIER_RATES = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}


@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """Calculate the loyalty discount for a customer order using AgentCore Code Interpreter."""
    tier = str(tier).strip().capitalize()
    product_category = str(product_category).strip().lower()
    if tier not in _TIER_RATES:
        return json.dumps({"error": f"Unknown tier '{tier}'. Use Silver, Gold, or Platinum."})
    if product_category not in ("standard", "device", "fresh"):
        product_category = "standard"

    code = f"""
import json, math

loyalty_points = {int(loyalty_points)}
tier = {json.dumps(tier)}
order_total = {float(order_total)}
product_category = {json.dumps(product_category)}

earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}

max_points_by_order = math.floor(order_total * 0.50 * 100)
redeemable = max(0, min(loyalty_points, max_points_by_order))
points_redeemed = (redeemable // 500) * 500
points_discount = points_redeemed / 100.0

subtotal_after_points = order_total - points_discount
tier_discount_pct = tier_rates[tier] * 100
tier_discount = round(subtotal_after_points * tier_rates[tier], 2)

final_total = round(subtotal_after_points - tier_discount, 2)
total_savings = round(points_discount + tier_discount, 2)
points_earned = math.floor(final_total * earn_rates[product_category])
remaining_points = loyalty_points - points_redeemed

result = {{
    "tier": tier,
    "product_category": product_category,
    "order_total": round(order_total, 2),
    "points_redeemed": points_redeemed,
    "points_discount": round(points_discount, 2),
    "tier_discount_pct": tier_discount_pct,
    "tier_discount": tier_discount,
    "final_total": final_total,
    "total_savings": total_savings,
    "points_earned": points_earned,
    "remaining_points": remaining_points,
    "new_points_balance": remaining_points + points_earned,
}}
print(json.dumps(result))
"""

    try:
        with code_session(REGION) as code_client:
            response = code_client.invoke(
                "executeCode",
                {
                    "code": code,
                    "language": "python",
                    "clearContext": True,
                },
            )
            for event in response["stream"]:
                result = event.get("result", event)
                for block in result.get("content", []) if isinstance(result, dict) else []:
                    text = block.get("text")
                    if text:
                        try:
                            return json.dumps(json.loads(text))
                        except ValueError:
                            break
                return json.dumps(result)
        raise RuntimeError("Code Interpreter returned no result")

    except Exception as e:
        logger.warning("Code Interpreter unavailable (%s); using tier-only fallback", e)
        rate = _TIER_RATES[tier]
        tier_discount = round(order_total * rate, 2)
        return json.dumps(
            {
                "tier": tier,
                "product_category": product_category,
                "order_total": round(order_total, 2),
                "points_redeemed": 0,
                "points_discount": 0.0,
                "tier_discount_pct": rate * 100,
                "tier_discount": tier_discount,
                "final_total": round(order_total - tier_discount, 2),
                "total_savings": tier_discount,
                "remaining_points": loyalty_points,
                "note": "Code Interpreter unavailable - only the tier discount was applied.",
            }
        )


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a friendly, accurate customer support assistant for an e-commerce platform.

Tool guidance:
- Order lookups: use the order tracking tools.
- Returns and refunds: use the refund tools.
- Product specs, policies, loyalty tier benefits: call search_knowledge_base.
- Loyalty discount math: always call calculate_loyalty_discount.
- Questions about a live web page: use the browser tool.

Rules:
- Never invent order details, tracking numbers, refund IDs, or policies.
- If a "Customer Context" section is present, honour it.
- Be concise and clear, and finish with the key facts the customer asked for."""


@app.entrypoint
async def invoke(payload, context=None):
    """Main handler called by AgentCore for every incoming request."""
    try:
        user_input = (payload or {}).get("prompt", "").strip()
        actor_id = (payload or {}).get("customer_id") or "anonymous"
        session_id = (payload or {}).get("session_id") or str(uuid.uuid4())

        if not user_input:
            return "Please provide a 'prompt' so I can help you."

        memory_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID,
        )

        agent_core_browser = AgentCoreBrowser(region=REGION)

        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            agent_core_browser.browser,
        ]

        mcp_client = MCPClient(lambda: streamable_http_client(GATEWAY_URL))

        with mcp_client:
            gateway_tools = []
            page = mcp_client.list_tools_sync()
            gateway_tools.extend(page)
            while getattr(page, "pagination_token", None):
                page = mcp_client.list_tools_sync(pagination_token=page.pagination_token)
                gateway_tools.extend(page)
            tools.extend(gateway_tools)

            agent = Agent(
                model=model,
                tools=tools,
                hooks=[memory_hook],
                system_prompt=SYSTEM_PROMPT,
            )
            response = agent(user_input)

        return response.message["content"][0]["text"]

    except Exception as e:
        logger.exception("Agent invocation failed")
        return f"Sorry, something went wrong while handling your request: {e}"


def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    main()