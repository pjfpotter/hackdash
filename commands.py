"""Plain-text commands for the phone remote.

Forgiving on purpose: dictation (Wispr Flow etc.) adds capitals, full stops
and spells "todo" as "to do". One command per line, or separated by ';'.
"""
import re

HELP = """COMMANDS (one per line):
todo buy milk, call mum      add todos (comma separated)
boring do tax return         add a boring todo (double XP)
done milk                    tick off a todo / goal / milestone / project
undo milk                    un-tick it
tada emptied the dishwasher  log a TA-DA (anything you did)
goal ship hackdash v1        add weekly goal(s)
goal hackdash 50%            set goal progress
project hackdash 40% next: flash the sd card
project garden bot parked    statuses: active paused parked done
milestone hackdash: on the tv
balance 1234.56              bank balance
tokens 40 70                 claude session% weekly%
tokens weekly 70 resets 2d4h
theme amber                  cyan green amber
privacy on                   hide money on screen
say back in 5                show a message on the TV
focus 25 write about page    Tears in Rain focus timer on the TV
pause / resume / stop        control the focus timer
delete milk                  delete an item
anything else                becomes a todo"""


def _clean(line):
    line = line.strip().strip("\"'").strip()
    line = re.sub(r"[.!?]+$", "", line)
    return line


def _split_items(rest):
    return [x.strip() for x in re.split(r",|\band also\b", rest) if x.strip()]


def run(store, text):
    replies = []
    for raw in re.split(r"[\n;]+", text or ""):
        line = _clean(raw)
        if not line:
            continue
        try:
            out = run_line(store, line)
        except (LookupError, ValueError) as e:
            out = f"?? {e}"
        if isinstance(out, list):
            replies.extend(out)
        else:
            replies.append(out)
    return replies or ["nothing to do. say 'help' for commands"]


def run_line(store, line):
    m = re.match(r"(?i)^(to[\s-]?dos?|add|task|boring|done|did|finished|complete[d]?|tick|undo|undone|untick|uncheck|"
                 r"ta[\s-]?dah?|goals?|project|milestone|balance|bank|tokens?|usage|theme|privacy|say|focus|pomodoro|pomo|pause|resume|abort|stop|"
                 r"delete|help)\b[\s:,-]*(.*)$", line)
    if not m:
        # bare text: treat as a todo, the most common thing to dictate
        return store.add_todos([line])
    verb = re.sub(r"[\s-]", "", m.group(1).lower())
    rest = m.group(2).strip()

    if verb in ("todo", "todos", "add", "task"):
        return store.add_todos(_split_items(rest))
    if verb == "boring":
        return store.add_todos([{"text": t, "boring": True} for t in _split_items(rest)])
    if verb in ("done", "did", "finished", "complete", "completed", "tick"):
        return [store.complete(r) for r in _split_items(rest)]
    if verb in ("undo", "undone", "untick", "uncheck"):
        return store.uncomplete(rest)
    if verb in ("tada", "tadah"):
        return store.log_tada(rest or "something boring")
    if verb in ("goal", "goals"):
        pm = re.match(r"(.+?)\s+(\d{1,3})\s*%$", rest)
        if pm:
            return store.set_progress(pm.group(1), int(pm.group(2)))
        return store.add_goals(_split_items(rest))
    if verb == "project":
        return _project(store, rest)
    if verb == "milestone":
        name, _, title = rest.partition(":")
        if not title:
            raise ValueError("say: milestone <project>: <milestone>")
        return store.upsert_project(name.strip(), add_milestones=[title.strip()])
    if verb in ("balance", "bank"):
        num = re.search(r"-?[\d,]+(?:\.\d+)?", rest)
        if not num:
            raise ValueError("say: balance 1234.56")
        return store.set_finance(balance=float(num.group().replace(",", "")))
    if verb in ("token", "tokens", "usage"):
        return _tokens(store, rest)
    if verb == "theme":
        return store.settings(theme=rest.lower().split()[0] if rest else "cyan")
    if verb == "privacy":
        return store.settings(privacy=rest.lower() not in ("off", "no", "false", "0"))
    if verb in ("focus", "pomodoro", "pomo"):
        fm = re.match(r"(\d{1,3})\s*(?:m|mins?|minutes?)?\b[\s:,-]*(.*)$", rest, re.I)
        minutes, task = (int(fm.group(1)), fm.group(2)) if fm else (25, rest)
        return store.focus_start(minutes, re.sub(r"(?i)^(?:on|for)\s+", "", task))
    if verb == "pause":
        return store.focus_pause()
    if verb == "resume":
        return store.focus_resume()
    if verb in ("stop", "abort"):
        if verb == "stop" and rest and not re.match(r"(?i)(focus|timer|pomo|pomodoro)\b", rest):
            return store.add_todos([line])  # "stop at the shop" is a todo
        return store.focus_stop()
    if verb == "say":
        return store.broadcast(rest)
    if verb == "delete":
        return store.remove(rest)
    return HELP


def _project(store, rest):
    kw = {}
    nm = re.search(r"\bnext\b[\s:,-]*(.+)$", rest, re.I)
    if nm:
        kw["next_step"] = nm.group(1).strip()
        rest = rest[:nm.start()].strip()
    pm = re.search(r"(\d{1,3})\s*(%|percent)", rest)
    if pm:
        kw["progress"] = int(pm.group(1))
        rest = (rest[:pm.start()] + rest[pm.end():]).strip()
    sm = re.search(r"\b(active|paused|pause|parked|park|idea|done|shipped)\s*$", rest, re.I)
    if sm:
        kw["status"] = sm.group(1).lower()
        rest = rest[:sm.start()].strip()
    name = rest.strip(" ,:-")
    if not name:
        raise ValueError("which project?")
    return store.upsert_project(name, **kw)


def _tokens(store, rest):
    kw = {}
    low = rest.lower()
    rm = re.search(r"(session|weekly)?\s*resets?\s+(?:in\s+)?([\w: -]+)$", low)
    which_reset = None
    if rm:
        which_reset = rm.group(1) or "weekly"
        kw[f"{which_reset}_resets"] = rm.group(2).strip()
        low = low[:rm.start()]
    named = dict(re.findall(r"(session|weekly)\s*(\d{1,3})", low))
    if named:
        if "session" in named:
            kw["session_pct"] = int(named["session"])
        if "weekly" in named:
            kw["weekly_pct"] = int(named["weekly"])
    else:
        nums = [int(n) for n in re.findall(r"\d{1,3}", low)]
        if nums:
            kw["session_pct"] = nums[0]
        if len(nums) > 1:
            kw["weekly_pct"] = nums[1]
    if not kw:
        raise ValueError("say: tokens 40 70 (session% weekly%)")
    return store.set_tokens(**kw)
