"""Bug reporting for MikeyV agents.

Any agent can file a bug. Sr triages and fixes.

Usage:
    from bug_report import file_bug, list_bugs, update_bug

    # File a bug
    file_bug(
        filed_by="Cinco",
        title="Slack messages arriving as background despite gaze",
        symptoms="Messages truncated, delivered as doorbells not foreground",
        steps_to_reproduce=["Set gaze to slack DM", "Receive a message", "Message arrives as background"],
        severity="P2",
    )

    # List open bugs
    for bug in list_bugs(status="open"):
        print(bug["title"])

    # Update a bug
    update_bug("bug_0001", status="investigating", assigned_to="Sr", diagnosis="Room value mismatch")
"""

import json
import os
import time
import secrets
from pathlib import Path

BUGS_DIR = Path(os.path.expanduser("~/agents/bugs"))


def _next_id():
    """Generate next bug ID from existing files."""
    BUGS_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(BUGS_DIR.glob("bug_*.json"))
    if not existing:
        return "bug_0001"
    last = existing[-1].stem  # e.g. "bug_0003"
    num = int(last.split("_")[1]) + 1
    return f"bug_{num:04d}"


def file_bug(filed_by, title, symptoms, steps_to_reproduce=None,
             expected=None, actual=None, severity="P2", context=None):
    """File a new bug report. Returns the bug ID."""
    bug_id = _next_id()
    bug = {
        "id": bug_id,
        "filed_by": filed_by,
        "filed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "severity": severity,
        "title": title,
        "symptoms": symptoms,
        "steps_to_reproduce": steps_to_reproduce or [],
        "expected": expected or "",
        "actual": actual or "",
        "context": context or "",
        "status": "open",
        "assigned_to": None,
        "diagnosis": None,
        "fix_commit": None,
        "verified_by": None,
    }
    path = BUGS_DIR / f"{bug_id}.json"
    with open(path, "w") as f:
        json.dump(bug, f, indent=2)
    
    # Notify Sr via localmail
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from localmail import send_mail
        send_mail(
            from_agent=filed_by,
            to_agent="Sr",
            text=f"[BUG FILED] {bug_id}: {title}\nSeverity: {severity}\nSymptoms: {symptoms}\nFiled by: {filed_by}",
        )
    except Exception:
        pass  # Notification is best-effort
    
    return bug_id


def list_bugs(status=None, severity=None, assigned_to=None):
    """List bugs, optionally filtered. Returns list of bug dicts."""
    BUGS_DIR.mkdir(parents=True, exist_ok=True)
    bugs = []
    for f in sorted(BUGS_DIR.glob("bug_*.json")):
        try:
            with open(f) as fh:
                bug = json.load(fh)
            if status and bug.get("status") != status:
                continue
            if severity and bug.get("severity") != severity:
                continue
            if assigned_to and bug.get("assigned_to") != assigned_to:
                continue
            bugs.append(bug)
        except (json.JSONDecodeError, OSError):
            pass
    return bugs


def update_bug(bug_id, **fields):
    """Update fields on an existing bug. Returns updated bug or None."""
    path = BUGS_DIR / f"{bug_id}.json"
    if not path.exists():
        return None
    with open(path) as f:
        bug = json.load(f)
    
    allowed = {"status", "assigned_to", "diagnosis", "fix_commit",
               "verified_by", "severity", "title", "symptoms",
               "steps_to_reproduce", "expected", "actual", "context"}
    for k, v in fields.items():
        if k in allowed:
            bug[k] = v
    
    bug["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    
    with open(path, "w") as f:
        json.dump(bug, f, indent=2)
    return bug


def get_bug(bug_id):
    """Read a single bug by ID. Returns dict or None."""
    path = BUGS_DIR / f"{bug_id}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def summary():
    """Print a one-line-per-bug summary."""
    bugs = list_bugs()
    if not bugs:
        return "No bugs filed."
    lines = []
    for b in bugs:
        status = b.get("status", "?")
        sev = b.get("severity", "?")
        assigned = b.get("assigned_to", "unassigned")
        lines.append(f"  {b['id']} [{sev}] {status:15s} {assigned:10s} {b['title']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        print(summary())
    else:
        print("Usage: python3 bug_report.py list")
        print("       Import and use file_bug(), list_bugs(), update_bug()")
