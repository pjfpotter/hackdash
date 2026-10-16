"""Minimal MCP server (Streamable HTTP, JSON responses) over the Store.

Enough of the protocol for claude.ai custom connectors: initialize, ping,
tools/list and tools/call. No SDK needed.
"""
import json

INSTRUCTIONS = """Hackdash is the user's life dashboard: a retro 80s/90s hacker-movie screen on a TV, run by a Raspberry Pi.
When the user brain-dumps and says "update the dash" (or similar):
1. Call get_dashboard first to see what's there and the item ids.
2. Turn the dump into changes: short imperative todos (under ~40 chars), weekly goals, project updates, ta-das.
3. Things the user has ALREADY done: complete_items if they're on the dash, otherwise log_tada. Each one fires a
   celebration animation on the TV, so log each real accomplishment separately. Boring chores deserve rewards most.
4. Mark dull chores (admin, cleaning, bills, emails) boring=true; they're worth double XP.
The user starts lots of projects and rarely finishes them. Help gently:
- Respect the WIP limit. A new project idea goes in as status "idea" (parked) unless the user is clearly swapping it
  in for an active one. When the limit blocks something, say which active project is closest to shipping.
- Keep each active project's next_step concrete and small, and suggest the next milestone.
- When you reply, mention the project closest to its next milestone in one line. Don't lecture.
Money: only record figures the user actually gives you. Never guess a balance.
Claude token usage has no API: the user reads percentages from claude.ai Settings > Usage and tells you.
Finish with a short summary of what changed on the dash."""

_str = {"type": "string"}
_refs = {"type": "array", "items": _str, "description": "Item ids (e.g. t3fa2) or text that matches the item"}

TOOLS = [
    {"name": "get_dashboard", "description": "Show everything on the dashboard, with item ids.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "add_todos", "description": "Add items to today's to-do list. Unfinished todos carry over to tomorrow.",
     "inputSchema": {"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {
         "type": "object", "required": ["text"], "properties": {
             "text": _str, "boring": {"type": "boolean", "description": "Dull chore; worth double XP when done"}}}}}}},
    {"name": "complete_items", "description": "Tick off todos, weekly goals, milestones or whole projects (a project = shipped). Fires a celebration on the TV.",
     "inputSchema": {"type": "object", "required": ["refs"], "properties": {"refs": _refs}}},
    {"name": "uncomplete_items", "description": "Re-open items ticked by mistake.",
     "inputSchema": {"type": "object", "required": ["refs"], "properties": {"refs": _refs}}},
    {"name": "remove_items", "description": "Delete todos, goals, milestones or projects.",
     "inputSchema": {"type": "object", "required": ["refs"], "properties": {"refs": _refs}}},
    {"name": "log_tada", "description": "Log something the user did that wasn't on the list (the TA-DA list). Fires a celebration.",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {
         "text": _str, "points": {"type": "integer", "description": "XP, default 15. Use 30-60 for dreaded tasks."}}}},
    {"name": "add_goals", "description": "Add goals for this week.",
     "inputSchema": {"type": "object", "required": ["goals"], "properties": {
         "goals": {"type": "array", "items": _str},
         "replace": {"type": "boolean", "description": "Replace this week's goals instead of adding"}}}},
    {"name": "set_progress", "description": "Set percent progress on a goal or project (overrides milestone-based progress).",
     "inputSchema": {"type": "object", "required": ["ref", "percent"], "properties": {
         "ref": _str, "percent": {"type": "integer", "minimum": 0, "maximum": 100}}}},
    {"name": "upsert_project", "description": "Create or update a project. New projects become active only if under the WIP limit, otherwise parked as 'idea'.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string", "description": "Project name, or id of an existing project"},
         "status": {"type": "string", "enum": ["active", "paused", "idea", "done"]},
         "progress": {"type": "integer", "minimum": 0, "maximum": 100},
         "next_step": {"type": "string", "description": "The single next concrete action"},
         "add_milestones": {"type": "array", "items": {"type": "object", "required": ["title"], "properties": {
             "title": _str, "due": {"type": "string", "description": "YYYY-MM-DD"}}}},
         "notes": _str}}},
    {"name": "set_finance", "description": "Record bank balance and spending figures the user gives you.",
     "inputSchema": {"type": "object", "properties": {
         "balance": {"type": "number"}, "currency": {"type": "string", "description": "Symbol, e.g. £"},
         "accounts": {"type": "array", "items": {"type": "object", "required": ["name", "balance"], "properties": {
             "name": _str, "balance": {"type": "number"}}}},
         "budget_spent": {"type": "number"}, "budget_limit": {"type": "number"},
         "budget_label": {"type": "string", "description": "e.g. OCT SPEND"}, "note": _str}}},
    {"name": "set_token_usage", "description": "Record Claude plan usage the user reports (percent of limits used).",
     "inputSchema": {"type": "object", "properties": {
         "session_percent": {"type": "integer"}, "weekly_percent": {"type": "integer"},
         "session_resets_at": {"type": "string", "description": "ISO time or duration like 3h20m"},
         "weekly_resets_at": {"type": "string", "description": "ISO time or duration like 2d4h"},
         "plan": _str, "note": _str}}},
    {"name": "dashboard_settings", "description": "Change display settings.",
     "inputSchema": {"type": "object", "properties": {
         "theme": {"type": "string", "enum": ["cyan", "green", "amber"]},
         "handle": {"type": "string", "description": "Banner text, max 10 chars"},
         "wip_limit": {"type": "integer", "minimum": 1, "maximum": 9},
         "privacy": {"type": "boolean", "description": "Hide money on screen"}}}},
    {"name": "broadcast", "description": "Show a message across the top of the TV for a while.",
     "inputSchema": {"type": "object", "required": ["message"], "properties": {
         "message": _str, "seconds": {"type": "integer", "description": "Default 120"}}}},
]


def _call(store, name, a):
    if name == "get_dashboard":
        return store.render_text()
    if name == "add_todos":
        out = store.add_todos(a["items"])
    elif name == "complete_items":
        out = [store.complete(r) for r in a["refs"]]
    elif name == "uncomplete_items":
        out = [store.uncomplete(r) for r in a["refs"]]
    elif name == "remove_items":
        out = [store.remove(r) for r in a["refs"]]
    elif name == "log_tada":
        out = store.log_tada(a["text"], a.get("points"))
    elif name == "add_goals":
        out = store.add_goals(a["goals"], a.get("replace", False))
    elif name == "set_progress":
        out = store.set_progress(a["ref"], a["percent"])
    elif name == "upsert_project":
        out = store.upsert_project(a["name"], a.get("status"), a.get("progress"), a.get("next_step"),
                                   a.get("add_milestones"), a.get("notes"))
    elif name == "set_finance":
        out = store.set_finance(a.get("balance"), a.get("currency"), a.get("accounts"), a.get("budget_spent"),
                                a.get("budget_limit"), a.get("budget_label"), a.get("note"))
    elif name == "set_token_usage":
        out = store.set_tokens(a.get("session_percent"), a.get("weekly_percent"), a.get("session_resets_at"),
                               a.get("weekly_resets_at"), a.get("note"), a.get("plan"))
    elif name == "dashboard_settings":
        out = store.settings(a.get("theme"), a.get("handle"), a.get("wip_limit"), a.get("privacy"))
    elif name == "broadcast":
        out = store.broadcast(a["message"], a.get("seconds", 120))
    else:
        raise LookupError(f"unknown tool {name}")
    return "\n".join(out) if isinstance(out, list) else str(out)


def handle(store, msg):
    """Handle one JSON-RPC message. Returns a response dict, or None for notifications."""
    mid = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}
    if mid is None:
        return None  # notification (e.g. notifications/initialized)

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "hackdash", "version": "0.1.0"},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        try:
            text = _call(store, params.get("name"), params.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": text}], "isError": False})
        except (LookupError, ValueError, KeyError, TypeError) as e:
            return ok({"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def handle_body(store, body):
    msg = json.loads(body or b"null")
    if isinstance(msg, list):
        out = [r for r in (handle(store, m) for m in msg) if r]
        return out or None
    return handle(store, msg)
