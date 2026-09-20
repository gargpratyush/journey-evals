"""Dependency-free graph-shaped fixture for the agent evaluation prototype."""


class SupportGraph:
    def stream(self, state, *, stream_mode):
        if stream_mode != "values":
            raise ValueError("This fixture exposes the LangGraph values-stream contract")
        messages = list(state.get("messages") or [])
        yield {"messages": messages}
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "lookup_policy", "args": {"topic": "refund"}}],
        })
        yield {"messages": messages}
        messages.append({
            "role": "tool",
            "name": "lookup_policy",
            "tool_call_id": "call-1",
            "content": "Orders may be refunded within 30 days.",
            "status": "completed",
        })
        yield {"messages": messages}
        messages.append({
            "role": "assistant",
            "content": "The order is eligible for a refund because it is within 30 days.",
        })
        yield {"messages": messages}


graph = SupportGraph()
