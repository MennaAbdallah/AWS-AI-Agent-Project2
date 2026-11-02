"""
E-commerce Support AI Agent - Main Application
=============================================
Runs the Bedrock AgentCore runtime server and connects to MCP Gateway tools
with decorated local e-commerce tools for automated execution.
"""
import os
import asyncio
import json
from bedrock_agentcore import BedrockAgentCoreApp
from strands.agent import Agent
from strands.tools.mcp import MCPClient

try:
    from strands.tools import tool
except ImportError:
    # Fallback no-op decorator if @tool is imported differently
    def tool(func):
        return func

# Initialize the Bedrock AgentCore App at module level
app = BedrockAgentCoreApp()

@tool
def get_order_status(order_id: str) -> str:
    """Look up the status, tracking details, and shipping carrier for an e-commerce order by order ID."""
    orders = {
        "ORD-001": {"status": "Shipped", "item": "Wireless Headphones", "eta": "Tomorrow", "carrier": "FedEx", "tracking": "FX-987654321"},
        "ORD-002": {"status": "Processing", "item": "Mechanical Keyboard", "eta": "3 Business Days", "carrier": "Pending", "tracking": "N/A"},
        "ORD-003": {"status": "Delivered", "item": "USB-C Hub", "eta": "Delivered Yesterday", "carrier": "UPS", "tracking": "UPS-123456789"}
    }
    order = orders.get(order_id.upper(), {"status": "Unknown", "item": "Unknown item", "eta": "N/A", "carrier": "N/A", "tracking": "N/A"})
    return json.dumps(order)

@tool
def process_refund(order_id: str, reason: str = "Customer request") -> str:
    """Process a refund for a specified order ID and return the refund confirmation details."""
    return json.dumps({
        "status": "Success",
        "order_id": order_id,
        "refund_id": f"REF-{hash(order_id) % 100000:05d}",
        "reason": reason,
        "message": f"Refund for order {order_id} has been successfully processed."
    })

@app.entrypoint
async def invoke(payload: dict) -> dict:
    """
    Main entrypoint invoked by the AgentCore cloud runtime server.
    """
    prompt = payload.get("prompt", "")
    customer_id = payload.get("customer_id", "CUST-123")
    session_id = payload.get("session_id", "default-session")

    gateway_url = os.environ.get("MCP_GATEWAY_URL") or os.environ.get("GATEWAY_URL") or "http://localhost:8080/mcp"

    tools = [get_order_status, process_refund]

    try:
        async def fetch_mcp():
            client = MCPClient(url=gateway_url)
            if hasattr(client, "get_tools"):
                return client.get_tools()
            return []

        mcp_tools = await asyncio.wait_for(fetch_mcp(), timeout=2.0)
        if mcp_tools:
            tools = mcp_tools + tools
    except Exception as e:
        print(f"MCP Gateway offline ({e}), using reliable local e-commerce tools.")

    try:
        # Initialize the agent with decorated tools
        agent = Agent(
            tools=tools,
            system_prompt=(
                f"You are an expert e-commerce customer support AI assistant for customer {customer_id}. "
                "You have access to tools such as `get_order_status` and `process_refund`. "
                "Whenever a user asks about order status, tracking, or refunds, you MUST invoke the appropriate tool to retrieve live data rather than simulating it in text."
            )
        )
        
        # Invoke the agent asynchronously
        response = await agent(prompt) if asyncio.iscoroutinefunction(agent.__call__) else agent(prompt)
        return {"response": str(response)}
    except Exception as e:
        print(f"Error during agent invocation: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    # Start the AgentCore runtime server
    app.run()