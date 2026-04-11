#!/usr/bin/env python3
"""
Projects Dashboard -- Five-column agent control room.
=====================================================
Each agent gets its own column showing health, context, expandable
projects, and a todo list. Built with Textual for interactivity.

Usage:
  python3 projects_dashboard.py                        # Launch TUI
  python3 projects_dashboard.py --update Sr key=value  # Update assignment data
  python3 projects_dashboard.py --todo Sr add "Fix the bug"  # Add todo
  python3 projects_dashboard.py --todo Sr done 0       # Complete todo #0
  python3 projects_dashboard.py --todo Sr rm 0         # Remove todo #0

Data sources:
  ~/agents/assignments.json   -- per-agent projects (array)
  ~/agents/<Agent>/todos.json -- per-agent todo list
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Static, Collapsible


# ============================================================================
# PATHS
# ============================================================================

AGENTS_HOME = Path(os.path.expanduser("~/agents"))
ASSIGNMENTS_FILE = AGENTS_HOME / "assignments.json"


AGENT_ORDER = ["Sr", "Jr", "Trip", "Q", "Cinco"]
AGENT_ROLES = {
    "Sr": "Infra Lead",
    "Jr": "Team Manager",
    "Trip": "Explorer",
    "Q": "Arena",
    "Cinco": "Comms",
}

# ============================================================================
# DATA COLLECTION
# ============================================================================

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def load_assignments():
    return load_json(ASSIGNMENTS_FILE) or {"agents": {}, "updated": "unknown"}


def load_todos(agent_name):
    """Load per-agent todo list from ~/agents/<Agent>/todos.json"""
    path = AGENTS_HOME / agent_name / "todos.json"
    data = load_json(path)
    if isinstance(data, list):
        return data
    return []


def save_todos(agent_name, todos):
    path = AGENTS_HOME / agent_name / "todos.json"
    with open(path, "w") as f:
        json.dump(todos, f, indent=2)



def get_agent_projects(name):
    """Get projects for an agent from assignments.json.

    Supports both old format (single assignment string) and new format
    (projects array). Returns list of project dicts.
    """
    assignments = load_assignments()
    info = assignments.get("agents", {}).get(name, {})

    # New format: projects array
    projects = info.get("projects")
    if isinstance(projects, list) and projects:
        return projects

    # Old format: single assignment string -> convert to one-project list
    assignment = info.get("assignment", "")
    if assignment:
        return [{
            "name": assignment,
            "phase": info.get("phase", "--"),
            "status": info.get("status", "unknown"),
            "notes": info.get("notes", ""),
            "last_commit": info.get("last_commit", ""),
        }]
    return []


def get_agent_data(name):
    """Collect all data for one agent."""
    data = {"name": name, "role": AGENT_ROLES.get(name, "?")}

    # Projects
    data["projects"] = get_agent_projects(name)

    # Overall status (from first project or assignment-level)
    assignments = load_assignments()
    info = assignments.get("agents", {}).get(name, {})
    data["status"] = info.get("status", "unknown")

    # Todos
    data["todos"] = load_todos(name)

    return data





# ============================================================================
# TEXTUAL WIDGETS
# ============================================================================

STATE_BADGES = {
    "on_track": "[green]\u25cf[/]",
    "blocked": "[red bold]\u25cf BLOCKED[/]",
    "needs_input": "[yellow]\u25cf Needs Input[/]",
    "done": "[dim]\u2713 Done[/]",
    "paused": "[dim]\u25cb Paused[/]",
}


def _proj_title(name, state):
    """Format project Collapsible title with state badge."""
    badge = STATE_BADGES.get(state, "")
    if badge:
        return f"{name}  {badge}"
    return name


def _format_step(step):
    """Format a single plan step as Rich markup."""
    if isinstance(step, str):
        return f"  \u25cb {step}"
    step_text = step.get("text", str(step))
    step_status = step.get("status", "pending")
    if step_status == "done":
        return f"  [green]\u2713[/] [dim strikethrough]{step_text}[/]"
    elif step_status in ("in_progress", "active"):
        return f"  [cyan bold]\u25b6[/] {step_text}"
    else:
        return f"  [dim]\u25cb[/] {step_text}"


def _format_todo(item):
    """Format a single todo item as Rich markup."""
    if isinstance(item, str):
        text, done = item, False
    elif isinstance(item, dict):
        text = item.get("text", item.get("task", str(item)))
        done = item.get("done", False)
    else:
        text, done = str(item), False

    if done:
        return f"  [green]\u2713[/] [dim strikethrough]{text}[/]"
    else:
        return f"  [yellow]\u25cb[/] {text}"


class AgentColumn(Static):
    """A single agent's status column.

    On first mount, builds the full widget tree with stable IDs.
    On refresh, updates content in place — preserving Collapsible
    expanded/collapsed state.
    """

    def __init__(self, agent_name: str, **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self._built = False  # True after first full build
        self._prev_structure = None  # Track structure for rebuild detection

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id=f"scroll-{self.agent_name}")

    def on_mount(self) -> None:
        self._full_build()

    def _structure_key(self, data):
        """Return a hashable key representing the widget structure.
        If this changes, we need a full rebuild (projects added/removed, etc.)."""
        proj_names = tuple(p.get("name", "") for p in data["projects"])
        proj_plan_lens = tuple(len(p.get("plan", [])) for p in data["projects"])
        todo_len = len(data["todos"])
        return (proj_names, proj_plan_lens, todo_len)

    def _full_build(self):
        """Build the full widget tree from scratch."""
        try:
            self._do_full_build()
        except Exception:
            pass  # Survive bad data or widget race — next refresh will retry

    def _do_full_build(self):
        """Inner build — separated so _full_build can catch exceptions."""
        data = get_agent_data(self.agent_name)
        scroll = self.query_one(f"#scroll-{self.agent_name}")
        scroll.remove_children()
        a = self.agent_name

        projects = data["projects"]
        if projects:
            for pi, proj in enumerate(projects):
                pname = proj.get("name", "Untitled")
                pgoal = proj.get("goal", "")
                pstatus = proj.get("status", "")
                plan = proj.get("plan", [])

                proj_children = []

                if pgoal:
                    proj_children.append(Static(
                        f"[bold]Goal:[/] {pgoal}",
                        id=f"{a}-proj-{pi}-goal", classes="project-goal"
                    ))

                if pstatus:
                    proj_children.append(Static(
                        f"[bold]Status:[/] {pstatus}",
                        id=f"{a}-proj-{pi}-status", classes="project-status"
                    ))

                if plan:
                    plan_widgets = []
                    for si, step in enumerate(plan):
                        plan_widgets.append(Static(
                            _format_step(step),
                            id=f"{a}-proj-{pi}-step-{si}", classes="plan-step"
                        ))
                    n_done = sum(1 for s in plan if isinstance(s, dict) and s.get("status") in ("done",))
                    plan_collapsible = Collapsible(
                        *plan_widgets,
                        title=f"Plan ({n_done}/{len(plan)})",
                        id=f"{a}-proj-{pi}-plan",
                        collapsed=True
                    )
                    proj_children.append(plan_collapsible)

                pstate = proj.get("state", "")
                proj_collapsible = Collapsible(
                    *proj_children,
                    title=_proj_title(pname, pstate),
                    id=f"{a}-proj-{pi}",
                    collapsed=False
                )
                scroll.mount(proj_collapsible)
        else:
            scroll.mount(Static("[dim]No projects assigned[/]", id=f"{a}-no-proj"))

        # Todos
        todos = data["todos"]
        n_todo = len(todos)
        n_done = sum(1 for t in todos if (isinstance(t, dict) and t.get("done")))
        n_open = n_todo - n_done
        todo_title = f"Todo ({n_open} open)" if n_open else ("Todo (all done)" if n_todo else "Todo")

        todo_widgets = []
        if todos:
            for ti, item in enumerate(todos):
                todo_widgets.append(Static(
                    _format_todo(item),
                    id=f"{a}-todo-{ti}", classes="todo-item"
                ))
        else:
            todo_widgets.append(Static("[dim]No todos[/]", id=f"{a}-no-todos"))

        todo_collapsible = Collapsible(
            *todo_widgets, title=todo_title,
            id=f"{a}-todos", collapsed=False
        )
        scroll.mount(todo_collapsible)

        self._prev_structure = self._structure_key(data)
        self._built = True

    def refresh_data(self) -> None:
        """Update content in place, preserving collapse state.
        Falls back to full rebuild if structure changed."""
        try:
            data = get_agent_data(self.agent_name)
        except Exception:
            return  # Bad data read, skip this refresh cycle

        if not self._built:
            try:
                self._full_build()
            except Exception:
                pass
            return

        # Check if structure changed (projects added/removed, plan steps changed)
        new_structure = self._structure_key(data)
        if new_structure != self._prev_structure:
            # Defer rebuild to after current render completes to avoid race
            self.call_after_refresh(self._full_build)
            return

        # In-place update — only change text content, leave Collapsibles alone
        a = self.agent_name
        projects = data["projects"]

        for pi, proj in enumerate(projects):
            pname = proj.get("name", "Untitled")
            pstatus = proj.get("status", "")
            pstate = proj.get("state", "")
            plan = proj.get("plan", [])

            # Update project title (state badge)
            try:
                proj_coll = self.query_one(f"#{a}-proj-{pi}", Collapsible)
                proj_coll.title = _proj_title(pname, pstate)
            except Exception:
                pass

            # Update status text
            try:
                status_widget = self.query_one(f"#{a}-proj-{pi}-status", Static)
                status_widget.update(f"[bold]Status:[/] {pstatus}")
            except Exception:
                pass

            # Update plan steps
            for si, step in enumerate(plan):
                try:
                    step_widget = self.query_one(f"#{a}-proj-{pi}-step-{si}", Static)
                    step_widget.update(_format_step(step))
                except Exception:
                    pass

            # Update plan title (progress count)
            try:
                n_done = sum(1 for s in plan if isinstance(s, dict) and s.get("status") in ("done",))
                plan_coll = self.query_one(f"#{a}-proj-{pi}-plan", Collapsible)
                plan_coll.title = f"Plan ({n_done}/{len(plan)})"
            except Exception:
                pass

        # Update todos
        todos = data["todos"]
        for ti, item in enumerate(todos):
            try:
                todo_widget = self.query_one(f"#{a}-todo-{ti}", Static)
                todo_widget.update(_format_todo(item))
            except Exception:
                pass

        # Update todo title
        try:
            n_todo = len(todos)
            n_done = sum(1 for t in todos if (isinstance(t, dict) and t.get("done")))
            n_open = n_todo - n_done
            todo_title = f"Todo ({n_open} open)" if n_open else ("Todo (all done)" if n_todo else "Todo")
            todo_coll = self.query_one(f"#{a}-todos", Collapsible)
            todo_coll.title = todo_title
        except Exception:
            pass


class ProjectsDashboard(App):
    """Five-column agent control room."""

    CSS = """
    Screen {
        layout: horizontal;
    }

    #header-bar {
        dock: top;
        height: 3;
        background: $surface;
        padding: 0 1;
        content-align: center middle;
    }

    #footer-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 1;
    }

    #columns {
        height: 1fr;
    }

    .agent-column {
        width: 1fr;
        border: round $primary;
        margin: 0 0;
        padding: 0 1;
    }

    .agent-column.sr { border: round $success; }
    .agent-column.jr { border: round $warning; }
    .agent-column.trip { border: round $accent; }
    .agent-column.q { border: round $error; }
    .agent-column.cinco { border: round $primary; }

    .project-goal { margin: 0 0 0 1; }
    .project-status { margin: 0 0 0 1; }
    .plan-step { margin: 0; }
    .todo-item { margin: 0; }

    VerticalScroll {
        height: 1fr;
    }

    Collapsible {
        margin: 0;
        padding: 0;
    }

    CollapsibleTitle {
        padding: 0;
    }


    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        now = datetime.now(timezone(timedelta(hours=-7)))
        yield Static(
            f" [bold cyan]Projects Dashboard[/]  [dim]{now.strftime('%Y-%m-%d %H:%M:%S PT')}[/]",
            id="header-bar"
        )
        with Horizontal(id="columns"):
            for name in AGENT_ORDER:
                col = AgentColumn(name, classes=f"agent-column {name.lower()}")
                col.border_title = f" {name} -- {AGENT_ROLES.get(name, '?')} "
                yield col
        yield Static("[dim] q: quit | r: refresh | auto-refresh 10s [/]", id="footer-bar")

    def on_mount(self) -> None:
        self.set_interval(10, self.action_refresh)

    def action_refresh(self) -> None:
        try:
            now = datetime.now(timezone(timedelta(hours=-7)))
            header = self.query_one("#header-bar", Static)
            header.update(
                f" [bold cyan]Projects Dashboard[/]  [dim]{now.strftime('%Y-%m-%d %H:%M:%S PT')}[/]"
            )
            for col in self.query(AgentColumn):
                col.refresh_data()
        except Exception:
            pass  # Never let a refresh crash the app


# ============================================================================
# CLI COMMANDS
# ============================================================================

def update_assignment(agent_name, updates):
    """Update agent assignment data in assignments.json."""
    data = load_assignments()
    if agent_name not in data.get("agents", {}):
        data.setdefault("agents", {})[agent_name] = {}
    for kv in updates:
        if "=" not in kv:
            print(f"Invalid: {kv} (expected key=value)")
            continue
        key, value = kv.split("=", 1)
        data["agents"][agent_name][key] = value
    data["updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(ASSIGNMENTS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Updated {agent_name}: {', '.join(updates)}")


def handle_todo(agent_name, action, args):
    """Manage per-agent todo list.

    Actions:
      add <text>   -- add a new todo item
      done <index> -- mark item as done
      undone <index> -- mark item as not done
      rm <index>   -- remove item
      list         -- show all items
    """
    todos = load_todos(agent_name)

    if action == "add":
        text = " ".join(args) if args else "untitled"
        todos.append({"text": text, "done": False})
        save_todos(agent_name, todos)
        print(f"Added todo #{len(todos)-1} for {agent_name}: {text}")

    elif action == "done":
        if not args:
            print("Usage: --todo <Agent> done <index>")
            return
        idx = int(args[0])
        if 0 <= idx < len(todos):
            if isinstance(todos[idx], str):
                todos[idx] = {"text": todos[idx], "done": True}
            else:
                todos[idx]["done"] = True
            save_todos(agent_name, todos)
            print(f"Marked #{idx} done for {agent_name}")
        else:
            print(f"Index {idx} out of range (0-{len(todos)-1})")

    elif action == "undone":
        if not args:
            print("Usage: --todo <Agent> undone <index>")
            return
        idx = int(args[0])
        if 0 <= idx < len(todos):
            if isinstance(todos[idx], dict):
                todos[idx]["done"] = False
            save_todos(agent_name, todos)
            print(f"Marked #{idx} undone for {agent_name}")
        else:
            print(f"Index {idx} out of range (0-{len(todos)-1})")

    elif action == "rm":
        if not args:
            print("Usage: --todo <Agent> rm <index>")
            return
        idx = int(args[0])
        if 0 <= idx < len(todos):
            removed = todos.pop(idx)
            save_todos(agent_name, todos)
            text = removed.get("text", removed) if isinstance(removed, dict) else removed
            print(f"Removed #{idx} from {agent_name}: {text}")
        else:
            print(f"Index {idx} out of range (0-{len(todos)-1})")

    elif action == "list":
        if not todos:
            print(f"{agent_name}: no todos")
            return
        for i, item in enumerate(todos):
            if isinstance(item, str):
                print(f"  [{i}] \u25cb {item}")
            elif isinstance(item, dict):
                marker = "\u2713" if item.get("done") else "\u25cb"
                print(f"  [{i}] {marker} {item.get('text', '?')}")
    else:
        print(f"Unknown action: {action}. Use: add, done, undone, rm, list")


# ============================================================================
# MAIN
# ============================================================================

def main():
    import argparse
    p = argparse.ArgumentParser(description="Projects Dashboard (Textual TUI)")
    p.add_argument("--update", nargs="+", metavar=("AGENT", "KEY=VALUE"),
                   help="Update assignment: --update Sr status=active")
    p.add_argument("--todo", nargs="+", metavar="ARG",
                   help="Manage todos: --todo Sr add 'Fix bug' | --todo Sr done 0 | --todo Sr list")
    args = p.parse_args()

    if args.update:
        agent_name = args.update[0]
        updates = args.update[1:]
        if not updates:
            print("Usage: --update <Agent> key=value ...")
            sys.exit(1)
        update_assignment(agent_name, updates)
        return

    if args.todo:
        if len(args.todo) < 2:
            print("Usage: --todo <Agent> <action> [args...]")
            sys.exit(1)
        agent_name = args.todo[0]
        action = args.todo[1]
        rest = args.todo[2:]
        handle_todo(agent_name, action, rest)
        return

    app = ProjectsDashboard()
    app.run()


if __name__ == "__main__":
    main()
