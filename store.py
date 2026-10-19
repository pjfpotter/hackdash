"""Hackdash state store: one JSON file, a lock, and an event feed.

Every mutation goes through an op method, which saves the file and emits
events that the dashboard picks up over SSE. Pure stdlib.
"""
import copy
import datetime as dt
import difflib
import json
import os
import re
import secrets
import threading

THEMES = ("cyan", "green", "amber")

# XP thresholds per level, and the rank shown on screen.
LEVELS = [0, 100, 250, 500, 900, 1400, 2000, 2800, 3800, 5000, 6500, 8500]
RANKS = [
    "SCRIPT KIDDIE", "PHONE PHREAK", "BBS LURKER", "CONSOLE COWBOY",
    "NETRUNNER", "SYSOP", "ELITE 31337", "CRASH OVERRIDE", "ACID BURN",
    "ZERO COOL", "THE PLAGUE", "MAINFRAME GOD",
]

POINTS = {"todo": 10, "boring": 25, "tada": 15, "goal": 50, "milestone": 100, "project": 500}


def now():
    return dt.datetime.now().replace(microsecond=0)


def iso(t=None):
    return (t or now()).isoformat()


def today():
    return now().date().isoformat()


def week_id(d=None):
    y, w, _ = (d or now().date()).isocalendar()
    return f"{y}-W{w:02d}"


def new_id(prefix):
    return f"{prefix}{secrets.token_hex(2)}"


def parse_when(value):
    """Accept an ISO timestamp or a relative duration like '2h30m' / 'in 3 hours'."""
    if value in (None, ""):
        return None
    s = str(value).strip().lower()
    m = re.fullmatch(r"(?:in\s+)?(?:(\d+)\s*d(?:ays?)?)?\s*(?:(\d+)\s*h(?:ours?|rs?)?)?\s*(?:(\d+)\s*m(?:in(?:ute)?s?)?)?", s)
    if m and any(m.groups()):
        d, h, mi = (int(x or 0) for x in m.groups())
        return iso(now() + dt.timedelta(days=d, hours=h, minutes=mi))
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if t.tzinfo:
            t = t.astimezone().replace(tzinfo=None)
        return iso(t.replace(microsecond=0))
    except ValueError:
        raise ValueError(f"can't read time {value!r}; use ISO like 2026-10-17T09:00 or a duration like 2h30m")


def level_for(xp):
    lvl = 0
    for i, floor in enumerate(LEVELS):
        if xp >= floor:
            lvl = i
    return lvl


DEFAULT_STATE = {
    "settings": {"theme": "cyan", "handle": "LIFE.OS", "wip_limit": 3, "privacy": False,
                 "currency": "£", "night_start": 23, "night_end": 7},
    "todos": [],
    "goals": [],
    "projects": [],
    "tada": [],
    "xp": 0,
    "activity_days": [],
    "finance": {"balance": None, "currency": None, "updated": None, "source": None,
                "history": [], "accounts": [], "budget": None, "note": ""},
    "tokens": {"plan": "Pro", "session_pct": None, "weekly_pct": None,
               "session_resets": None, "weekly_resets": None, "updated": None, "note": ""},
    "log": [],
    "message": None,
    "focus": None,
}


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.seq = 0
        self.events = []
        self.rev = 0
        self.state = self._load()

    # ---------- persistence ----------
    def _load(self):
        state = copy.deepcopy(DEFAULT_STATE)
        if os.path.exists(self.path):
            with open(self.path) as f:
                saved = json.load(f)
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(state.get(k), dict):
                    state[k].update(v)
                else:
                    state[k] = v
        return state

    def _save(self):
        self._prune()
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)

    def _prune(self):
        s = self.state
        cutoff = (now().date() - dt.timedelta(days=7)).isoformat()
        s["todos"] = [t for t in s["todos"] if not t["done"] or (t["done_at"] or "")[:10] >= cutoff]
        old_week = week_id(now().date() - dt.timedelta(weeks=8))
        s["goals"] = [g for g in s["goals"] if g["week"] >= old_week]
        s["tada"] = s["tada"][-300:]
        s["log"] = s["log"][-150:]
        s["activity_days"] = sorted(set(s["activity_days"]))[-90:]
        s["finance"]["history"] = s["finance"]["history"][-120:]

    # ---------- events ----------
    def _emit(self, ev):
        self.seq += 1
        self.events.append((self.seq, ev))
        self.events = self.events[-200:]
        self.cond.notify_all()

    def wait_events(self, after, timeout=15):
        with self.cond:
            self.cond.wait_for(lambda: self.seq > after, timeout)
            return self.seq, [e for s, e in self.events if s > after]

    def _commit(self, rewards=()):
        self.rev += 1
        self._save()
        for r in rewards:
            self._emit(r)
        self._emit({"type": "state", "rev": self.rev})

    def _log(self, kind, text):
        self.state["log"].append({"at": iso(), "kind": kind, "text": text})

    # ---------- reading ----------
    def snapshot(self):
        with self.lock:
            s = copy.deepcopy(self.state)
            s["rev"] = self.rev
        s["now"] = iso()
        s["week"] = week_id()
        s["today"] = today()
        lvl = level_for(s["xp"])
        s["level"] = {
            "n": lvl + 1, "rank": RANKS[min(lvl, len(RANKS) - 1)],
            "floor": LEVELS[lvl], "next": LEVELS[lvl + 1] if lvl + 1 < len(LEVELS) else None,
        }
        s["streak"] = self._streak(s["activity_days"])
        s["tada_today"] = sum(1 for t in s["tada"] if t["at"][:10] == s["today"])
        s["focus_today"] = sum(t.get("minutes", 0) for t in s["tada"] if t["at"][:10] == s["today"])
        for p in s["projects"]:
            p["pct"] = self._project_pct(p)
        return s

    @staticmethod
    def _streak(days):
        have = set(days)
        d = now().date()
        if d.isoformat() not in have:
            d -= dt.timedelta(days=1)  # streak survives until the end of today
        n = 0
        while d.isoformat() in have:
            n += 1
            d -= dt.timedelta(days=1)
        return n

    @staticmethod
    def _project_pct(p):
        if p.get("progress") is not None:
            return int(p["progress"])
        ms = p.get("milestones") or []
        if ms:
            return round(100 * sum(m["done"] for m in ms) / len(ms))
        return 100 if p["status"] == "done" else 0

    def render_text(self):
        """Compact plain-text view for Claude: shows ids so it can target items."""
        s = self.snapshot()
        cur = s["settings"]["currency"]
        out = [f"HACKDASH {s['now']}  week {s['week']}  rev {s['rev']}"]
        out.append(f"XP {s['xp']}  level {s['level']['n']} {s['level']['rank']}  streak {s['streak']}d")
        out.append("\nTODAY:")
        for t in s["todos"]:
            if not t["done"] or (t["done_at"] or "")[:10] == s["today"]:
                flag = " (boring)" if t.get("boring") else ""
                carried = f" (since {t['created']})" if t["created"] != s["today"] and not t["done"] else ""
                out.append(f"  [{'x' if t['done'] else ' '}] {t['id']} {t['text']}{flag}{carried}")
        out.append(f"\nGOALS {s['week']}:")
        for g in s["goals"]:
            if g["week"] == s["week"] or not g["done"]:
                old = "" if g["week"] == s["week"] else f" (from {g['week']})"
                out.append(f"  [{'x' if g['done'] else ' '}] {g['id']} {g['text']} {g['progress']}%{old}")
        active = [p for p in s["projects"] if p["status"] == "active"]
        out.append(f"\nPROJECTS (active {len(active)}/{s['settings']['wip_limit']} limit):")
        for p in s["projects"]:
            out.append(f"  {p['id']} {p['name']} [{p['status']}] {p['pct']}% next: {p.get('next') or '-'}")
            for m in p.get("milestones", []):
                due = f" due {m['due']}" if m.get("due") else ""
                out.append(f"      [{'x' if m['done'] else ' '}] {m['id']} {m['title']}{due}")
        f = s["finance"]
        if f["balance"] is not None:
            out.append(f"\nBALANCE {f['currency'] or cur}{f['balance']:,.2f} (updated {f['updated']}, {f['source']})")
        tk = s["tokens"]
        out.append(f"TOKENS session {tk['session_pct']}% weekly {tk['weekly_pct']}% (updated {tk['updated']})")
        fo = s["focus"]
        if fo:
            out.append(f"FOCUS {fo['status']}: {fo['minutes']}m {fo['task']} (ends {fo['ends_at']})")
        out.append(f"FOCUS TODAY {s['focus_today']} min")
        out.append("\nRECENT TA-DAS: " + "; ".join(t["text"] for t in s["tada"][-5:]))
        return "\n".join(out)

    # ---------- finding things ----------
    def _candidates(self, kinds):
        s = self.state
        if "todo" in kinds:
            for t in s["todos"]:
                yield "todo", t, None, t["text"]
        if "goal" in kinds:
            for g in s["goals"]:
                yield "goal", g, None, g["text"]
        for p in s["projects"]:
            if "project" in kinds:
                yield "project", p, None, p["name"]
            if "milestone" in kinds:
                for m in p.get("milestones", []):
                    yield "milestone", m, p, m["title"]

    def find(self, ref, kinds=("todo", "goal", "milestone", "project"), prefer_open=True):
        ref = str(ref).strip()
        r = ref.lower()
        cands = list(self._candidates(kinds))
        for c in cands:
            if c[1]["id"].lower() == r:
                return c
        best, best_score = None, 0.0
        for c in cands:
            label = c[3].lower()
            if label == r:
                score = 1.0
            elif r in label or label in r:
                score = 0.8 + 0.1 * min(len(r), len(label)) / max(len(r), len(label), 1)
            else:
                score = difflib.SequenceMatcher(None, r, label).ratio() * 0.85
            is_done = c[1].get("done") or c[1].get("status") == "done"
            if prefer_open and is_done:
                score -= 0.15
            if score > best_score:
                best, best_score = c, score
        if best and best_score >= 0.5:
            return best
        raise LookupError(f"nothing on the dash matches {ref!r}")

    def find_project(self, ref):
        return self.find(ref, kinds=("project",), prefer_open=False)[1]

    # ---------- reward helpers ----------
    def _award(self, kind, text, points):
        before = level_for(self.state["xp"])
        self.state["xp"] += points
        after = level_for(self.state["xp"])
        self.state["activity_days"].append(today())
        self.state["tada"].append({"id": new_id("d"), "text": text, "points": points, "at": iso(), "kind": kind})
        ev = {"type": "reward", "kind": kind, "text": text, "points": points, "xp": self.state["xp"]}
        if after > before:
            ev["levelup"] = {"n": after + 1, "rank": RANKS[min(after, len(RANKS) - 1)]}
        return ev

    # ---------- ops ----------
    def add_todos(self, items):
        with self.lock:
            added = []
            for it in items:
                if isinstance(it, str):
                    it = {"text": it}
                text = str(it.get("text", "")).strip()
                if not text:
                    continue
                t = {"id": new_id("t"), "text": text[:80], "boring": bool(it.get("boring")),
                     "created": today(), "done": False, "done_at": None}
                self.state["todos"].append(t)
                added.append(t)
            if added:
                self._log("todo", "queued " + ", ".join(t["text"] for t in added))
                self._commit()
            return [f"todo {t['id']}: {t['text']}" for t in added]

    def complete(self, ref):
        with self.lock:
            kind, obj, parent, label = self.find(ref)
            if obj.get("done") or obj.get("status") == "done":
                return f"{label} was already done"
            if kind == "project":
                obj["status"] = "done"
                obj["progress"] = 100
                obj["shipped_at"] = iso()
                ev = self._award("project", obj["name"], POINTS["project"])
                self._log("ship", f"SHIPPED {obj['name']}")
            else:
                obj["done"] = True
                obj["done_at"] = iso()
                if kind == "goal":
                    obj["progress"] = 100
                pts = POINTS["boring"] if obj.get("boring") else POINTS[kind]
                rkind = "boring" if obj.get("boring") else kind
                text = f"{parent['name']}: {label}" if parent else label
                ev = self._award(rkind, text, pts)
                self._log(kind, f"done {text} +{pts}xp")
                if parent:
                    parent["updated"] = iso()
            self._commit([ev])
            return f"done: {label} (+{ev['points']} xp)" + (f" LEVEL UP -> {ev['levelup']['rank']}" if "levelup" in ev else "")

    def uncomplete(self, ref):
        with self.lock:
            kind, obj, _, label = self.find(ref, prefer_open=False)
            if kind == "project":
                obj["status"] = "active"
                obj["progress"] = None
            else:
                obj["done"] = False
                obj["done_at"] = None
            self._log(kind, f"reopened {label}")
            self._commit()
            return f"reopened: {label}"

    def remove(self, ref):
        with self.lock:
            kind, obj, parent, label = self.find(ref, prefer_open=False)
            if kind == "todo":
                self.state["todos"].remove(obj)
            elif kind == "goal":
                self.state["goals"].remove(obj)
            elif kind == "project":
                self.state["projects"].remove(obj)
            else:
                parent["milestones"].remove(obj)
            self._log("rm", f"removed {label}")
            self._commit()
            return f"removed {kind}: {label}"

    def log_tada(self, text, points=None):
        with self.lock:
            text = str(text).strip()[:80]
            pts = int(points) if points else POINTS["tada"]
            pts = max(1, min(pts, 200))
            ev = self._award("tada", text, pts)
            self._log("tada", f"TA-DA {text} +{pts}xp")
            self._commit([ev])
            return f"ta-da logged: {text} (+{pts} xp)"

    def add_goals(self, goals, replace=False):
        with self.lock:
            wk = week_id()
            if replace:
                self.state["goals"] = [g for g in self.state["goals"] if g["week"] != wk]
            added = []
            for text in goals:
                text = str(text).strip()
                if text:
                    g = {"id": new_id("g"), "text": text[:80], "week": wk, "progress": 0, "done": False, "done_at": None}
                    self.state["goals"].append(g)
                    added.append(g)
            self._log("goal", f"{len(added)} goal(s) set for {wk}")
            self._commit()
            return [f"goal {g['id']}: {g['text']}" for g in added]

    def set_progress(self, ref, percent):
        with self.lock:
            kind, obj, _, label = self.find(ref, kinds=("goal", "project"))
            pct = max(0, min(100, int(percent)))
            obj["progress"] = pct
            if kind == "project":
                obj["updated"] = iso()
            self._log(kind, f"{label} -> {pct}%")
            self._commit()
        if kind == "goal" and pct == 100:
            return self.complete(obj["id"])
        return f"{label} at {pct}%"

    def upsert_project(self, name, status=None, progress=None, next_step=None, add_milestones=None, notes=None):
        with self.lock:
            msgs = []
            try:
                p = self.find_project(name)
            except LookupError:
                p = {"id": new_id("p"), "name": str(name).strip()[:40], "status": "idea", "progress": None,
                     "next": "", "notes": "", "milestones": [], "created": iso(), "updated": iso()}
                self.state["projects"].append(p)
                msgs.append(f"new project {p['id']}: {p['name']}")
                if status is None:
                    status = "active"
            if status:
                status = {"parked": "idea", "park": "idea", "pause": "paused", "shipped": "done"}.get(status, status)
                if status not in ("active", "paused", "idea", "done"):
                    raise ValueError("status must be active, paused, idea or done")
                if status == "done" and p["status"] != "done":
                    self._commit()
                    msgs.append(self.complete(p["id"]))
                    return msgs
                if status == "active" and p["status"] != "active":
                    active = [q for q in self.state["projects"] if q["status"] == "active"]
                    limit = self.state["settings"]["wip_limit"]
                    if len(active) >= limit:
                        status = "idea"
                        msgs.append(f"WIP limit {limit} reached, so {p['name']} is parked as an idea. "
                                    f"Finish or pause one of: {', '.join(q['name'] for q in active)}")
                p["status"] = status
            if progress is not None:
                p["progress"] = max(0, min(100, int(progress)))
            if next_step is not None:
                p["next"] = str(next_step)[:80]
            if notes is not None:
                p["notes"] = str(notes)[:200]
            for m in add_milestones or []:
                if isinstance(m, str):
                    m = {"title": m}
                p["milestones"].append({"id": new_id("m"), "title": str(m["title"])[:50],
                                        "due": m.get("due"), "done": False, "done_at": None})
            p["updated"] = iso()
            self._log("proj", f"{p['name']} [{p['status']}]")
            self._commit()
            msgs.append(f"project {p['id']} {p['name']}: {p['status']}, {self._project_pct(p)}%")
            return msgs

    def set_finance(self, balance=None, currency=None, accounts=None, budget_spent=None,
                    budget_limit=None, budget_label=None, note=None, source="manual"):
        with self.lock:
            f = self.state["finance"]
            if currency:
                f["currency"] = str(currency)[:3]
            if balance is not None:
                f["balance"] = round(float(balance), 2)
                f["history"].append([iso(), f["balance"]])
            if accounts is not None:
                f["accounts"] = [{"name": str(a["name"])[:20], "balance": round(float(a["balance"]), 2)} for a in accounts]
            if budget_limit is not None or budget_spent is not None:
                b = f["budget"] or {"label": "SPEND", "spent": 0, "limit": 0}
                if budget_limit is not None:
                    b["limit"] = float(budget_limit)
                if budget_spent is not None:
                    b["spent"] = float(budget_spent)
                f["budget"] = b
            if budget_label and f["budget"]:
                f["budget"]["label"] = str(budget_label)[:16]
            if note is not None:
                f["note"] = str(note)[:60]
            f["updated"] = iso()
            f["source"] = source
            if source == "manual":
                self._log("bank", "balance updated")
            self._commit()
            return f"finance updated ({f['currency'] or self.state['settings']['currency']}{f['balance']})"

    def set_tokens(self, session_pct=None, weekly_pct=None, session_resets=None, weekly_resets=None, note=None, plan=None):
        with self.lock:
            t = self.state["tokens"]
            if session_pct is not None:
                t["session_pct"] = max(0, min(100, int(session_pct)))
            if weekly_pct is not None:
                t["weekly_pct"] = max(0, min(100, int(weekly_pct)))
            if session_resets is not None:
                t["session_resets"] = parse_when(session_resets)
            if weekly_resets is not None:
                t["weekly_resets"] = parse_when(weekly_resets)
            if note is not None:
                t["note"] = str(note)[:60]
            if plan:
                t["plan"] = str(plan)[:12]
            t["updated"] = iso()
            self._log("tok", f"claude usage {t['session_pct']}% / {t['weekly_pct']}%")
            self._commit()
            return f"token usage: session {t['session_pct']}%, weekly {t['weekly_pct']}%"

    def settings(self, theme=None, handle=None, wip_limit=None, privacy=None, currency=None):
        with self.lock:
            st = self.state["settings"]
            if theme:
                if theme not in THEMES:
                    raise ValueError(f"theme must be one of {', '.join(THEMES)}")
                st["theme"] = theme
            if handle:
                st["handle"] = re.sub(r"[^A-Za-z0-9._ -]", "", str(handle)).upper()[:10] or st["handle"]
            if wip_limit is not None:
                st["wip_limit"] = max(1, min(9, int(wip_limit)))
            if privacy is not None:
                st["privacy"] = bool(privacy)
            if currency:
                st["currency"] = str(currency)[:3]
            self._commit()
            return f"settings: {st}"

    # ---------- focus timer (Tears in Rain) ----------
    def focus_start(self, minutes=25, task=""):
        with self.lock:
            m = max(1, min(180, int(minutes or 25)))
            task = str(task or "").strip()[:80]
            self.state["focus"] = {"task": task, "minutes": m, "started": iso(), "status": "running",
                                   "ends_at": iso(now() + dt.timedelta(minutes=m)), "remaining": m * 60}
            self._log("focus", f"focus {m}m {task}".strip())
            self._commit()
            return f"focus: {m} min{' on ' + task if task else ''}, ends {self.state['focus']['ends_at'][11:16]}"

    def focus_pause(self):
        with self.lock:
            f = self.state["focus"]
            if not f or f["status"] != "running":
                return "no focus session running"
            f["remaining"] = max(1, int((dt.datetime.fromisoformat(f["ends_at"]) - now()).total_seconds()))
            f["status"], f["ends_at"] = "paused", None
            self._commit()
            return f"focus paused, {f['remaining'] // 60}m{f['remaining'] % 60:02d}s left"

    def focus_resume(self):
        with self.lock:
            f = self.state["focus"]
            if not f or f["status"] != "paused":
                return "no paused focus session"
            f["status"], f["ends_at"] = "running", iso(now() + dt.timedelta(seconds=f["remaining"]))
            self._commit()
            return f"focus resumed, ends {f['ends_at'][11:16]}"

    def focus_stop(self):
        with self.lock:
            f = self.state["focus"]
            if not f:
                return "no focus session to stop"
            self.state["focus"] = None
            self._log("focus", "focus stopped early")
            self._commit()
            return "focus stopped (no XP, but no judgement either)"

    def focus_tick(self):
        """Called every second by the server; finishes a session whose time is up."""
        with self.lock:
            f = self.state["focus"]
            if not f or f["status"] != "running" or now() < dt.datetime.fromisoformat(f["ends_at"]):
                return
            self.state["focus"] = None
            label = f"{f['minutes']}m focus" + (f": {f['task']}" if f["task"] else "")
            ev = self._award("focus", label, max(5, f["minutes"]))
            self.state["tada"][-1]["minutes"] = f["minutes"]
            self._log("focus", f"FOCUS DONE {label} +{ev['points']}xp")
            self._commit([ev])

    def broadcast(self, text, seconds=120):
        with self.lock:
            seconds = max(5, min(int(seconds or 120), 86400))
            self.state["message"] = {"text": str(text)[:120], "until": iso(now() + dt.timedelta(seconds=seconds))}
            self._log("msg", str(text)[:60])
            self._commit()
            return "message on screen"
