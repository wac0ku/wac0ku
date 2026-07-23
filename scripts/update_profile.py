#!/usr/bin/env python3
"""Auto-update the profile README's progress section from public GitHub activity.

Queries the user's public repositories directly (repos sorted by last push,
then commits/releases per repo since the cutoff) — deliberately NOT the
/events feed, which lags by hours and misses pushes made via integrations.
Rewrites only the block between PROGRESS:START / PROGRESS:END markers in
README.md; the curated build log and roadmap outside the markers are never
touched.

Runs in GitHub Actions (see .github/workflows/update-profile.yml), but works
locally too: PROFILE_USER=<user> python scripts/update_profile.py
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

USER = os.environ.get("PROFILE_USER", "wac0ku")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
README = Path(__file__).resolve().parents[1] / "README.md"
START = "<!-- PROGRESS:START -->"
END = "<!-- PROGRESS:END -->"
WINDOW_DAYS = 30
MAX_REPOS = 10  # only the most recently pushed repos are inspected
# Commits made by this pipeline itself must not count as "progress".
SELF_MARKER = "auto-update progress"

CUTOFF = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)


def api(path: str):
    request = urllib.request.Request(f"https://api.github.com{path}", headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-profile-updater",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except Exception as exc:
        print(f"warn: GET {path} failed: {exc}", file=sys.stderr)
        return None


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def collect() -> dict:
    repos_data = api(f"/users/{USER}/repos?per_page=100&sort=pushed") or []
    stats = {"repos": {}, "releases": [], "new_repos": []}

    active = [r for r in repos_data if parse_ts(r["pushed_at"]) >= CUTOFF]
    for repo in active:
        if parse_ts(repo["created_at"]) >= CUTOFF:
            stats["new_repos"].append(repo["name"])

    for repo in active[:MAX_REPOS]:
        name = repo["name"]
        commits = api(
            f"/repos/{USER}/{name}/commits?per_page=100&since={CUTOFF.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        ) or []
        commits = [c for c in commits
                   if SELF_MARKER not in c["commit"]["message"]]
        if commits:
            latest = commits[0]  # API returns newest first
            stats["repos"][name] = {
                "commits": len(commits),
                "latest_msg": latest["commit"]["message"].splitlines()[0][:80],
                "latest_at": parse_ts(
                    latest["commit"]["committer"]["date"]).strftime("%b %d"),
            }

        for release in (api(f"/repos/{USER}/{name}/releases?per_page=10") or []):
            published = release.get("published_at")
            if published and parse_ts(published) >= CUTOFF:
                stats["releases"].append(
                    f"{name} {release.get('tag_name', '')}".strip())

    return stats


def render(stats: dict) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_commits = sum(r["commits"] for r in stats["repos"].values())

    if not stats["repos"] and not stats["releases"] and not stats["new_repos"]:
        return (f"_Last checked {today} — a quiet {WINDOW_DAYS} days. "
                f"Next update lands here automatically._")

    headline = [f"**{total_commits} commits** across **{len(stats['repos'])} repo(s)**"]
    if stats["releases"]:
        headline.append(f"**{len(stats['releases'])} release(s)** ({', '.join(stats['releases'][:3])})")
    if stats["new_repos"]:
        headline.append(f"new repo(s): {', '.join(stats['new_repos'][:3])}")

    lines = [f"_Last {WINDOW_DAYS} days (updated {today}):_ " + " · ".join(headline), ""]
    ranked = sorted(stats["repos"].items(), key=lambda kv: kv[1]["commits"], reverse=True)
    for name, info in ranked[:5]:
        lines.append(f"- **[{name}](https://github.com/{USER}/{name})** — "
                     f"{info['commits']} commit(s), latest: “{info['latest_msg']}” ({info['latest_at']})")
    return "\n".join(lines)


def main() -> int:
    content = README.read_text(encoding="utf-8")
    if START not in content or END not in content:
        print(f"error: markers {START} / {END} missing in README.md", file=sys.stderr)
        return 1

    section = render(collect())
    head, rest = content.split(START, 1)
    _, tail = rest.split(END, 1)
    updated = f"{head}{START}\n{section}\n{END}{tail}"

    if updated != content:
        README.write_text(updated, encoding="utf-8")
        print("README.md updated")
    else:
        print("no changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
