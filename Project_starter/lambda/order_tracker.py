"""
Order Tracking Lambda
======================
Handles order and customer lookup.
Supports API Gateway proxy integration, direct tool invocations, and argument wrappers.
"""
import json
from datetime import datetime, timedelta


# ── Sample data ───────────────────────────────────────────────────────────────

def _orders():
    """Return the mock order database."""
    return {
        "ORD-001": {
            "order_id":           "ORD-001",
            "customer_id":       "CUST-123",
            "status":            "SHIPPED",
            "items":             [{"name": "Wireless Headphones Pro", "qty": 1, "price": 89.99}],
            "total":             89.99,
            "tracking_number":   "TRK987654321",
            "carrier":           "UPS",
            "estimated_delivery": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d"),
        },
        "ORD-002": {
            "order_id":     "ORD-002",
            "customer_id":  "CUST-123",
            "status":       "DELIVERED",
            "items":        [{"name": "Kindle Paperwhite", "qty": 1, "price": 139.99}],
            "total":        139.99,
            "tracking_number": "TRK123456789",
            "carrier":      "USPS",
            "delivered_date": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
        },
        "ORD-003": {
            "order_id":     "ORD-003",
            "customer_id":  "CUST-456",
            "status":       "PROCESSING",
            "items": [
                {"name": "Echo Dot 5th Gen", "qty": 2, "price": 49.99},
                {"name": "Smart Plug",        "qty": 1, "price": 24.99},
            ],
            "total":              124.97,
            "estimated_delivery": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
        },
    }


def _customers():
    """Return the mock customer database."""
    return {
        "CUST-123": {"name": "Jane Smith",  "loyalty_points": 4250, "tier": "Gold"},
        "CUST-456": {"name": "Bob Johnson", "loyalty_points": 890,  "tier": "Silver"},
    }


# ── Response helper ───────────────────────────────────────────────────────────
def _response(status_code: int, body: dict) -> dict:
    """Format a Lambda proxy-integration response."""
    return {
        "statusCode": status_code,
        "headers":    {"Content-Type": "application/json"},
        "body":       json.dumps(body),
    }


# ── Handler ───────────────────────────────────────────────────────────────────
def lambda_handler(event, context):
    print(f"=== ORDER TRACKER EVENT ===")
    print(json.dumps(event))

    # ── Resolve tool name from AgentCore Gateway if present ───────────────────
    raw_tool = ""
    if context and getattr(context, "client_context", None) and context.client_context and context.client_context.custom:
        raw_tool = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    tool = raw_tool.split("___", 1)[-1] if "___" in raw_tool else raw_tool

    # Extract parameters from all possible incoming event structures
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}
    arguments = event.get("arguments") or event.get("parameters") or {}

    order_id = (
        path_params.get("order_id") or
        query_params.get("order_id") or
        arguments.get("order_id") or
        event.get("order_id") or
        ""
    )
    if order_id:
        order_id = str(order_id).upper()

    customer_id = (
        path_params.get("customer_id") or
        query_params.get("customer_id") or
        arguments.get("customer_id") or
        event.get("customer_id") or
        ""
    )
    if customer_id:
        customer_id = str(customer_id).upper()

    resource = event.get("resource", "")

    print(f"Tool: {tool} | Resource: {resource} | OrderID: {order_id} | CustomerID: {customer_id}")

    orders    = _orders()
    customers = _customers()

    # ── Route 1: Get Order by ID ──────────────────────────────────────────────
    if tool in ["get-order", "get_order"] or order_id and not ("orders" in tool or resource.endswith("/orders")):
        if not order_id:
            return _response(400, {"error": "Missing order_id"})
        order = orders.get(order_id)
        if not order:
            return _response(404, {"error": f"Order {order_id} not found"})
        return _response(200, order)

    # ── Route 2: Get Customer Orders ──────────────────────────────────────────
    if tool in ["get-customer-orders", "get_customer_orders"] or "orders" in tool or resource == "/customers/{customer_id}/orders":
        if not customer_id:
            return _response(400, {"error": "Missing customer_id"})
        result = [o for o in orders.values() if o["customer_id"] == customer_id]
        if not result:
            return _response(404, {"error": f"No orders found for {customer_id}"})
        return _response(200, {"customer_id": customer_id, "orders": result})

    # ── Route 3: Get Customer Profile ─────────────────────────────────────────
    if tool in ["get-customer", "get_customer"] or resource == "/customers/{customer_id}" or (customer_id and not order_id):
        if not customer_id:
            return _response(400, {"error": "Missing customer_id"})
        customer = customers.get(customer_id)
        if not customer:
            return _response(404, {"error": f"Customer {customer_id} not found"})
        return _response(200, customer)

    # ── Unrecognized request ──────────────────────────────────────────────────
    return _response(400, {"error": "Unrecognized request or missing parameters", "event": event})