#!/usr/bin/env python3
"""
Live GitHub Profile Telemetry & SVG Generator for My Profile.
- Uses GitHub GraphQL API when GITHUB_TOKEN is available (automatic in GitHub Actions).
- Falls back gracefully to GitHub public REST API + public contribution calendar locally.
- Generates dark & light adaptive SVGs:
    * assets/hud-banner-dark.svg
    * assets/hud-banner-light.svg
    * assets/telemetry-dark.svg
    * assets/telemetry-light.svg
    * assets/activity-dark.svg
    * assets/activity-light.svg
- Syncs the adaptive Bash terminal activity SVG into README.md between START_SECTION:activity markers.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "profile.config.json"
README_PATH = ROOT / "README.md"
ASSETS_DIR = ROOT / "assets"

USER_AGENT = "uvxv-profile-telemetry-sync/1.0"


def http_json(
        url: str, 
        token: str | None = None, 
        method: str = "GET", 
        payload: dict | None = None) -> Any:
    """
    The funtion makes an HTTP request to the specified URL and returns the response as a JSON object. 
    It supports optional authentication using a token, allows specifying the HTTP method (default is GET), 
    and can send a JSON payload if provided.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }

    if token:
        # if token is provided, add an Authorization header to the request for authentication
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def format_compact(n: int) -> str:
    """
    A function that formats a given integer into a more compact string representation.
    which means replacing large numbers with a more readable format (e.g., 1000 -> 1K, 1000000 -> 1M).
    """
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def compute_uptime(created_at_iso: str, now: dt.datetime) -> str:
    """
    A function that calculates the uptime of a system or service based on 
    its creation timestamp and the current time.
    """
    try:
        created = dt.datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        delta_days = max(0, (now - created).days)
        years, rem_days = divmod(delta_days, 365)
        if years > 0:
            return f"{years}y {rem_days}d"
        return f"{rem_days}d"
    except Exception:
        return "online"


def fetch_graphql_metrics(username: str, token: str) -> dict[str, Any] | None:
    """
    Fetches GraphQL metrics for a given GitHub username.
    """
    query = """
    query($login: String!) {
      user(login: $login) {
        createdAt
        followers { totalCount }
        repositories(privacy: PUBLIC, first: 100, ownerAffiliations: OWNER, orderBy: {field: PUSHED_AT, direction: DESC}) {
          totalCount
          nodes {
            nameWithOwner
            stargazerCount
            isFork
            pushedAt
            defaultBranchRef {
              name
            }
          }
        }
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    try:
        res = http_json(
            "https://api.github.com/graphql",
            token=token,
            method="POST",
            payload={"query": query, "variables": {"login": username}},
        )
        user = (res or {}).get("data", {}).get("user")
        if not user:
            return None

        repos = user["repositories"]["nodes"] or [] # assign an empty list if the repositories field is None
        total_stars = sum(int(r.get("stargazerCount", 0)) for r in repos)
        non_profile_repos = [
            _repo for _repo in repos if str(_repo.get("nameWithOwner", "")).lower() != f"{username}/{username}".lower()
        ]
        head_candidates = non_profile_repos or repos
        latest_repo = head_candidates[0]["nameWithOwner"] if head_candidates else f"{username}/{username}"
        recent_repos = [
            {
                "repo": r["nameWithOwner"],
                "pushed_at": r.get("pushedAt") or "",
                "branch": ((r.get("defaultBranchRef") or {}).get("name")) or "main",
            }
            for r in non_profile_repos[:15]
            if r.get("nameWithOwner")
        ]

        cal = user["contributionsCollection"]["contributionCalendar"]
        weeks = cal.get("weeks") or []
        weekly_totals = [
            sum(int(d.get("contributionCount", 0)) for d in w.get("contributionDays", []))
            for w in weeks[-16:]
        ]

        return {
            "created_at": user["createdAt"],
            "followers": int(user["followers"]["totalCount"]),
            "public_repos": int(user["repositories"]["totalCount"]),
            "total_stars": total_stars,
            "contributions_1y": int(cal.get("totalContributions", 0)),
            "weekly_series": weekly_totals or [0] * 16,
            "latest_repo": latest_repo,
            "recent_repos": recent_repos,
        }
    except Exception as exc:
        print(f"[warn] GraphQL fetch failed ({exc}), falling back to public REST endpoints.")
        return None


SNAPSHOT_PATH = ASSETS_DIR / "telemetry-snapshot.json"


def load_snapshot() -> dict[str, Any]:
    """
    A function that loads a snapshot of telemetry data from a JSON file.
    which returns a dictionary containing the telemetry data. If the file does not exist or cannot be read,
    it returns a default dictionary with placeholder values.
    """
    if SNAPSHOT_PATH.exists():
        try:
            return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "created_at": "2021-04-11T10:22:16Z",
        "followers": 1,
        "public_repos": 11,
        "total_stars": 0,
        "contributions_1y": 135,
        "weekly_series": [1, 2, 1, 3, 2, 4, 3, 5, 2, 6, 4, 5, 7, 4, 6, 8],
        "latest_repo": "uvxv/KubeShoot",
        "activity_lines": [],
    }


def fetch_rest_metrics(username: str, token: str | None = None) -> dict[str, Any]:
    snap = load_snapshot()
    created_at = snap.get("created_at", "2021-04-11T10:22:16Z")
    followers = int(snap.get("followers", 0))
    public_repos = int(snap.get("public_repos", 0))
    total_stars = int(snap.get("total_stars", 0))
    latest_repo = snap.get("latest_repo", f"{username}/{username}")
    recent_repos: list[dict[str, str]] = []

    try:
        user = http_json(f"https://api.github.com/users/{username}", token=token)
        repos = http_json(f"https://api.github.com/users/{username}/repos?per_page=100&sort=pushed", token=token)
        created_at = user.get("created_at", created_at)
        followers = int(user.get("followers", followers))
        public_repos = int(user.get("public_repos", public_repos))
        total_stars = sum(int(r.get("stargazers_count", 0)) for r in repos if isinstance(r, dict))
        if isinstance(repos, list) and repos:
            non_profile = [
                r
                for r in repos
                if isinstance(r, dict)
                and str(r.get("full_name", "")).lower() != f"{username}/{username}".lower()
            ]
            head_list = non_profile or [r for r in repos if isinstance(r, dict)]
            if head_list and "full_name" in head_list[0]:
                latest_repo = head_list[0]["full_name"]
            recent_repos = [
                {
                    "repo": str(r["full_name"]),
                    "pushed_at": str(r.get("pushed_at") or ""),
                    "branch": str(r.get("default_branch") or "main"),
                }
                for r in non_profile[:15]
                if r.get("full_name")
            ]
    except Exception as exc:
        print(f"[warn] GitHub REST API unavailable or rate-limited ({exc}); using cached snapshot.")

    contributions_1y = int(snap.get("contributions_1y", 0))
    weekly_series: list[int] = []
    try:
        contrib_data = http_json(f"https://github-contributions-api.jogruber.de/v4/{username}?y=last")
        contributions_1y = int((contrib_data.get("total") or {}).get("lastYear", contributions_1y))
        days = contrib_data.get("contributions") or []
        if days:
            last_days = days[-112:]  # 16 weeks
            for idx in range(0, len(last_days), 7):
                chunk = last_days[idx : idx + 7]
                weekly_series.append(sum(int(d.get("count", 0)) for d in chunk))
    except Exception as exc:
        print(f"[warn] Public contribution calendar fallback unavailable ({exc}).")

    if not weekly_series:
        weekly_series = list(snap.get("weekly_series") or [1, 2, 1, 3, 2, 4, 3, 5, 2, 6, 4, 5, 7, 4, 6, 8])

    return {
        "created_at": created_at,
        "followers": followers,
        "public_repos": public_repos,
        "total_stars": total_stars,
        "contributions_1y": contributions_1y,
        "weekly_series": weekly_series[-16:],
        "latest_repo": latest_repo,
        "recent_repos": recent_repos,
    }


def _parse_iso_utc(ts: str) -> dt.datetime | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def fetch_recent_events(
    username: str,
    token: str | None,
    max_items: int = 5,
    recent_repos: list[dict[str, str]] | None = None,
) -> tuple[list[str], str | None]:
    lines: list[str] = []
    latest_event_repo: str | None = None
    candidates: list[tuple[str, str, str, str, str, str]] = []
    latest_repo_event_dt: dict[str, dt.datetime] = {}

    try:
        events = http_json(f"https://api.github.com/users/{username}/events/public?per_page=50", token=token)
        if isinstance(events, list):
            for ev in events:
                ev_type = ev.get("type", "")
                repo_name = (ev.get("repo") or {}).get("name", "")
                created_raw = ev.get("created_at", "")
                payload = ev.get("payload") or {}

                # Skip self-sync commits on the profile repo so telemetry logs stay meaningful
                if repo_name.lower() == f"{username}/{username}".lower() and ev_type == "PushEvent":
                    continue

                tag = ""
                detail = ""
                if ev_type == "PushEvent":
                    ref = (payload.get("ref") or "refs/heads/main").split("/")[-1]
                    tag = "PUSH   "
                    detail = f"pushed updates -> {ref}"
                elif ev_type == "ReleaseEvent":
                    rel = (payload.get("release") or {}).get("tag_name") or "latest"
                    action = payload.get("action") or "published"
                    tag = "RELEASE"
                    detail = f"{action} release {rel}"
                elif ev_type == "CreateEvent":
                    ref_type = payload.get("ref_type") or "repository"
                    ref = payload.get("ref")
                    tag = "CREATE "
                    detail = f"initialized {ref_type}" + (f" ({ref})" if ref else "")
                elif ev_type == "PullRequestEvent":
                    action = payload.get("action") or "updated"
                    pr_num = (payload.get("pull_request") or {}).get("number") or ""
                    tag = "PR     "
                    detail = f"{action} pull request #{pr_num}".strip()
                elif ev_type == "IssuesEvent":
                    action = payload.get("action") or "updated"
                    iss_num = (payload.get("issue") or {}).get("number") or ""
                    tag = "ISSUE  "
                    detail = f"{action} issue #{iss_num}".strip()
                elif ev_type == "ForkEvent":
                    tag = "FORK   "
                    detail = "forked repository workload"
                elif ev_type == "WatchEvent":
                    tag = "STAR   "
                    detail = "starred repository"
                else:
                    continue

                ev_dt = _parse_iso_utc(created_raw)
                if ev_dt and repo_name:
                    prev_dt = latest_repo_event_dt.get(repo_name)
                    if prev_dt is None or ev_dt > prev_dt:
                        latest_repo_event_dt[repo_name] = ev_dt

                extra_key = detail if ev_type in ("ReleaseEvent", "CreateEvent", "PullRequestEvent", "IssuesEvent") else ""
                candidates.append((created_raw, ev_type, repo_name, tag, detail, extra_key))
    except Exception as exc:
        print(f"[warn] Public events fetch failed ({exc}).")

    # Supplement with repository pushed_at timestamps when newer than public event cache or when events aged out
    for r in recent_repos or []:
        repo_name = r.get("repo", "")
        pushed_raw = r.get("pushed_at", "")
        branch = r.get("branch", "main")
        if not repo_name or not pushed_raw or repo_name.lower() == f"{username}/{username}".lower():
            continue
        pushed_dt = _parse_iso_utc(pushed_raw)
        known_dt = latest_repo_event_dt.get(repo_name)
        if pushed_dt and (known_dt is None or (pushed_dt - known_dt).total_seconds() > 60):
            candidates.append(
                (pushed_raw, "PushEvent", repo_name, "PUSH   ", f"pushed updates -> {branch}", "")
            )

    # Strictly sort newest-first so out-of-order GitHub event shards never scramble journalctl logs
    candidates.sort(key=lambda item: item[0], reverse=True)

    seen_keys: set[tuple[str, str, str, str]] = set()
    for created_raw, ev_type, repo_name, tag, detail, extra_key in candidates:
        if repo_name and not latest_event_repo and repo_name.lower() != f"{username}/{username}".lower():
            latest_event_repo = repo_name

        ts_short = created_raw[:16].replace("T", " ") if len(created_raw) >= 16 else "recent          "
        day_key = created_raw[:10]
        dedupe_key = (day_key, ev_type, repo_name, extra_key)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        repo_col = repo_name[:26].ljust(26)
        lines.append(f"{ts_short} UTC  [{tag}]  {repo_col}  {detail}")
        if len(lines) >= max_items:
            break

    return lines, latest_event_repo


def build_sparkline_paths(values: list[int], x0: float, y0: float, width: float, height: float) -> tuple[str, str]:
    pts = values[-16:] if values else [0] * 16
    if len(pts) == 1:
        pts = pts * 2
    max_v = max(max(pts), 1)
    step = width / (len(pts) - 1)

    coords: list[tuple[float, float]] = []
    for i, val in enumerate(pts):
        px = x0 + i * step
        norm = val / max_v
        py = y0 + height - (norm * (height - 4) + 2)
        coords.append((round(px, 1), round(py, 1)))

    line_str = " ".join(f"{cx},{cy}" for cx, cy in coords)
    area_str = (
        f"{coords[0][0]},{round(y0 + height, 1)} "
        + line_str
        + f" {coords[-1][0]},{round(y0 + height, 1)}"
    )
    return line_str, area_str


def render_hud_banner(config: dict[str, Any], metrics: dict[str, Any], sync_str: str, theme: str) -> str:
    """Render the Cyber-Teal Observability & Terminal HUD SVG (920x340)."""
    is_dark = theme == "dark"
    outer_stroke = "#1d4e4a" if is_dark else "#0f766e"
    outer_stroke_opacity = "0.65" if is_dark else "0.85"
    shadow_block = (
        ""
        if is_dark
        else '<feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#0f172a" flood-opacity="0.14"/>'
    )

    username = html.escape(config["username"])
    display_name = html.escape(config["display_name"])
    role = html.escape(config["role"])
    node_host = html.escape(config.get("node_host", f"{username}@node-01"))
    os_label = html.escape(config.get("os_label", "Linux // x86_64"))
    location = html.escape(config.get("location", ""))
    quote = html.escape(config.get("quote", ""))

    uptime = html.escape(metrics["uptime"])
    public_repos = metrics["public_repos"]
    contributions = metrics["contributions_1y"]
    latest_repo_raw = metrics["latest_repo"]
    if len(latest_repo_raw) > 21:
        latest_repo_raw = latest_repo_raw[:20] + "…"
    latest_repo = html.escape(latest_repo_raw)

    # Render stack chips dynamically so spacing never overlaps and right padding is symmetrical
    chips_svg: list[str] = []
    cursor_x = 36.0
    max_chip_x = 580.0
    for item in config.get("stack_tags", []):
        label_raw = str(item.get("label", ""))
        dot_color = html.escape(str(item.get("color", "#2dd4bf")))
        chip_w = max(52.0, round(len(label_raw) * 7.3 + 33.0, 1))
        if cursor_x + chip_w > max_chip_x:
            break
        label_esc = html.escape(label_raw)
        chips_svg.append(
            f'<g transform="translate({cursor_x}, 216)">'
            f'<rect width="{chip_w}" height="26" rx="13" fill="#0d292d" fill-opacity="0.78" stroke="#2dd4bf" stroke-opacity="0.35" stroke-width="1"/>'
            f'<circle cx="14" cy="13" r="3.2" fill="{dot_color}"/>'
            f'<text x="24" y="17" fill="#d1fae5" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11.5" font-weight="600">{label_esc}</text>'
            f"</g>"
        )
        cursor_x = round(cursor_x + chip_w + 8.0, 1)

    # Render pipeline stages dynamically across x = 198 .. 834
    stages: list[str] = [str(s) for s in config.get("pipeline_stages", ["read", "learn", "understand", "commit"])]
    stage_count = max(len(stages), 2)
    rail_start = 198.0
    rail_end = 834.0
    rail_width = round(rail_end - rail_start, 1)
    step_x = (rail_end - rail_start) / (stage_count - 1)

    pipeline_nodes_svg: list[str] = []
    chevrons_svg: list[str] = []
    for idx, stg in enumerate(stages):
        sx = round(rail_start + idx * step_x, 1)
        is_last = idx == stage_count - 1
        stg_esc = html.escape(stg)
        step_num = f"0{idx + 1}"

        ring_stroke = "#34d399" if is_last else "#2dd4bf"
        core_fill = "#34d399" if is_last else "#5eead4"
        label_fill = "#6ee7b7" if is_last else "#cbd5e1"
        badge_bg = "#064e3b" if is_last else "#0b2228"
        badge_border = "#10b981" if is_last else "#1e4047"

        if is_last:
            pipeline_nodes_svg.append(
                f'<circle cx="{sx}" cy="285" r="10.5" fill="#10b981" fill-opacity="0.18" class="pulse-ring"/>'
            )

        pill_text = f"{step_num} · {stg_esc}" + (" ✓" if is_last else "")
        pill_w = max(82.0, round(len(pill_text) * 7.1 + 22.0, 1))
        pill_x = round(sx - pill_w / 2.0, 1)

        pipeline_nodes_svg.append(
            f"<g>"
            f'<circle cx="{sx}" cy="285" r="6.5" fill="#061217" stroke="{ring_stroke}" stroke-width="1.8"/>'
            f'<circle cx="{sx}" cy="285" r="2.6" fill="{core_fill}"/>'
            f'<rect x="{pill_x}" y="297" width="{pill_w}" height="21" rx="6" fill="{badge_bg}" fill-opacity="0.88" stroke="{badge_border}" stroke-width="1"/>'
            f'<text x="{sx}" y="311.5" text-anchor="middle" fill="{label_fill}" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11" font-weight="600">{pill_text}</text>'
            f"</g>"
        )

        if idx < stage_count - 1:
            mid_x = round(sx + step_x / 2.0, 1)
            chevrons_svg.append(
                f'<circle cx="{mid_x}" cy="285" r="8" fill="#051014"/>'
                f'<text x="{mid_x}" y="289" text-anchor="middle" fill="#5eead4" fill-opacity="0.85" font-family="ui-monospace, monospace" font-size="13" font-weight="700">›</text>'
            )

    repo_bar_w = min(242, max(28, int((min(public_repos, 40) / 40) * 242)))
    ops_bar_w = min(242, max(36, int((min(contributions, 400) / 400) * 242)))

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="920" height="340" viewBox="0 0 920 340" role="img" aria-label="{display_name} — {role}">
  <defs>
    <linearGradient id="hudBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#050d11"/>
      <stop offset="48%" stop-color="#08181e"/>
      <stop offset="100%" stop-color="#050e13"/>
    </linearGradient>
    <radialGradient id="auroraTopLeft" cx="18%" cy="14%" r="58%">
      <stop offset="0%" stop-color="#14b8a6" stop-opacity="0.28"/>
      <stop offset="55%" stop-color="#0f766e" stop-opacity="0.09"/>
      <stop offset="100%" stop-color="#050d11" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="auroraBottomRight" cx="85%" cy="88%" r="52%">
      <stop offset="0%" stop-color="#10b981" stop-opacity="0.22"/>
      <stop offset="60%" stop-color="#0d9488" stop-opacity="0.06"/>
      <stop offset="100%" stop-color="#050d11" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="innerFrameGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#2dd4bf" stop-opacity="0.45"/>
      <stop offset="50%" stop-color="#0f766e" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#34d399" stop-opacity="0.38"/>
    </linearGradient>
    <linearGradient id="nameGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#f8fafc"/>
      <stop offset="65%" stop-color="#e2f8f5"/>
      <stop offset="100%" stop-color="#99f6e4"/>
    </linearGradient>
    <linearGradient id="pipeGrad" gradientUnits="userSpaceOnUse" x1="{rail_start}" y1="285" x2="{rail_end}" y2="285">
      <stop offset="0%" stop-color="#0f766e"/>
      <stop offset="50%" stop-color="#14b8a6"/>
      <stop offset="100%" stop-color="#10b981"/>
    </linearGradient>
    <pattern id="telemetryGrid" width="24" height="24" patternUnits="userSpaceOnUse">
      <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#2dd4bf" stroke-opacity="0.045" stroke-width="1"/>
      <circle cx="24" cy="24" r="0.8" fill="#5eead4" fill-opacity="0.14"/>
    </pattern>
    <clipPath id="cardClip">
      <rect x="4" y="4" width="912" height="332" rx="18"/>
    </clipPath>
    {f'<filter id="cardShadow" x="-2%" y="-2%" width="104%" height="106%">{shadow_block}</filter>' if shadow_block else ''}
  </defs>

  <style>
    .cursor {{
      animation: cursorBlink 1.15s steps(2, start) infinite;
    }}
    .led-pulse {{
      animation: ledPulse 2.6s ease-in-out infinite;
    }}
    .pipe-flow {{
      stroke-dasharray: 56 190;
      animation: signalFlow 4.2s linear infinite;
    }}
    .pulse-ring {{
      animation: ledPulse 2.8s ease-in-out infinite;
    }}
    @keyframes cursorBlink {{
      0%, 49% {{ opacity: 1; }}
      50%, 100% {{ opacity: 0; }}
    }}
    @keyframes ledPulse {{
      0%, 100% {{ opacity: 0.35; }}
      50% {{ opacity: 0.95; }}
    }}
    @keyframes signalFlow {{
      from {{ stroke-dashoffset: 246; }}
      to {{ stroke-dashoffset: 0; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .cursor, .led-pulse, .pipe-flow, .pulse-ring {{ animation: none; }}
    }}
  </style>

  <!-- Outer Card Shell -->
  <g {f'filter="url(#cardShadow)"' if shadow_block else ''}>
    <rect x="4" y="4" width="912" height="332" rx="18" fill="url(#hudBg)"/>
  </g>

  <g clip-path="url(#cardClip)">
    <rect x="4" y="4" width="912" height="332" fill="url(#auroraTopLeft)"/>
    <rect x="4" y="4" width="912" height="332" fill="url(#auroraBottomRight)"/>
    <rect x="4" y="4" width="912" height="332" fill="url(#telemetryGrid)"/>

    <!-- Inner Bezel -->
    <rect x="14" y="14" width="892" height="312" rx="12" fill="#040b0e" fill-opacity="0.36" stroke="url(#innerFrameGrad)" stroke-width="1"/>

    <!-- Top Status Strip -->
    <rect x="14" y="14" width="892" height="38" rx="12" fill="#07161b" fill-opacity="0.72"/>
    <line x1="14" y1="52" x2="906" y2="52" stroke="#2dd4bf" stroke-opacity="0.18" stroke-width="1"/>

    <circle cx="34" cy="33" r="7" fill="#10b981" fill-opacity="0.22" class="led-pulse"/>
    <circle cx="34" cy="33" r="3.6" fill="#34d399"/>
    <text x="48" y="37" fill="#94a3b8" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11.5" letter-spacing="0.3">
      <tspan fill="#5eead4" font-weight="700">NODE</tspan> {node_host}   <tspan fill="#334155">|</tspan>   <tspan fill="#5eead4" font-weight="700">SYS</tspan> {os_label}   <tspan fill="#334155">|</tspan>   <tspan fill="#5eead4" font-weight="700">LOC</tspan> {location}
    </text>

    <!-- Live Sync Pill -->
    <rect x="640" y="22" width="252" height="22" rx="11" fill="#062825" fill-opacity="0.85" stroke="#10b981" stroke-opacity="0.42" stroke-width="1"/>
    <circle cx="655" cy="33" r="2.8" fill="#34d399"/>
    <text x="665" y="36.8" fill="#a7f3d0" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="10.2" font-weight="600" letter-spacing="0.2">LIVE SYNC · {html.escape(sync_str)}</text>

    <!-- Left Console Column: Identity & Toolchain -->
    <text x="36" y="82" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="12.5">
      <tspan fill="#34d399" font-weight="700">{username}@sysadmin</tspan><tspan fill="#64748b">:</tspan><tspan fill="#38bdf8" font-weight="700">~</tspan><tspan fill="#94a3b8">$ </tspan><tspan fill="#e2e8f0">whoami --profile</tspan><tspan fill="#2dd4bf" class="cursor"> ▋</tspan>
    </text>

    <text x="36" y="131" fill="url(#nameGrad)" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, Helvetica, Arial, sans-serif" font-size="40" font-weight="800" letter-spacing="-0.8">{display_name}</text>

    <text x="36" y="158" fill="#2dd4bf" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, Helvetica, Arial, sans-serif" font-size="14.5" font-weight="600" letter-spacing="0.2">{role}</text>

    <rect x="36" y="173" width="3" height="22" rx="1.5" fill="#14b8a6"/>
    <text x="48" y="188.5" fill="#94a3b8" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="13" font-style="italic">&quot;{quote}&quot;</text>

    {"".join(chips_svg)}

    <!-- Vertical Console Divider -->
    <line x1="596" y1="66" x2="596" y2="250" stroke="#2dd4bf" stroke-opacity="0.18" stroke-width="1" stroke-dasharray="3 4"/>

    <!-- Right Console Column: SYS.DIAGNOSTICS Live Readout -->
    <text x="616" y="80" fill="#5eead4" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11" font-weight="700" letter-spacing="1.1">SYS.DIAGNOSTICS</text>
    <text x="886" y="80" text-anchor="end" fill="#94a3b8" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11">UPTIME <tspan fill="#d1fae5" font-weight="700">{uptime}</tspan></text>

    <rect x="616" y="90" width="270" height="156" rx="10" fill="#040d11" fill-opacity="0.72" stroke="#2dd4bf" stroke-opacity="0.22" stroke-width="1"/>

    <!-- Row 1: Handle -->
    <text x="630" y="114" fill="#64748b" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11">HOST.UID</text>
    <text x="872" y="114" text-anchor="end" fill="#f0fdfa" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11.5" font-weight="700">@{username}</text>
    <line x1="630" y1="123" x2="872" y2="123" stroke="#1e293b" stroke-width="1"/>

    <!-- Row 2: Public Workloads -->
    <text x="630" y="143" fill="#64748b" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11">WORKLOADS</text>
    <text x="872" y="143" text-anchor="end" fill="#34d399" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11.5" font-weight="700">{public_repos} public repos</text>
    <rect x="630" y="150" width="242" height="3.5" rx="1.75" fill="#0f242a"/>
    <rect x="630" y="150" width="{repo_bar_w}" height="3.5" rx="1.75" fill="#10b981"/>

    <!-- Row 3: Annual Operations -->
    <text x="630" y="175" fill="#64748b" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11">COMMITS.365D</text>
    <text x="872" y="175" text-anchor="end" fill="#2dd4bf" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11.5" font-weight="700">{format_compact(contributions)} ops</text>
    <rect x="630" y="182" width="242" height="3.5" rx="1.75" fill="#0f242a"/>
    <rect x="630" y="182" width="{ops_bar_w}" height="3.5" rx="1.75" fill="#2dd4bf"/>

    <!-- Row 4: Active Target -->
    <text x="630" y="209" fill="#64748b" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11">HEAD.TARGET</text>
    <text x="872" y="209" text-anchor="end" fill="#38bdf8" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11" font-weight="600">{latest_repo}</text>
    <line x1="630" y1="219" x2="872" y2="219" stroke="#1e293b" stroke-width="1"/>

    <!-- Sub-panel Footer -->
    <text x="630" y="236" fill="#10b981" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="10" font-weight="600">● STATE: HEALTHY</text>
    <text x="872" y="236" text-anchor="end" fill="#64748b" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="10">EXIT: 0 (OK)</text>

    <!-- Bottom Pipeline Bus -->
    <rect x="14" y="264" width="892" height="62" fill="#040d11" fill-opacity="0.62"/>
    <line x1="14" y1="264" x2="906" y2="264" stroke="#2dd4bf" stroke-opacity="0.2" stroke-width="1"/>

    <text x="36" y="298" fill="#5eead4" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="11" font-weight="700" letter-spacing="1.1">PIPELINE //</text>
    <text x="36" y="312" fill="#64748b" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="9.5">ITERATIVE LOOP</text>

    <!-- Continuous Pipeline Rail + Animated Signal Wave -->
    <rect x="{rail_start}" y="284" width="{rail_width}" height="2" rx="1" fill="url(#pipeGrad)" fill-opacity="0.55"/>
    <line x1="{rail_start}" y1="285" x2="{rail_end}" y2="285" stroke="#5eead4" stroke-width="2.2" stroke-linecap="round" class="pipe-flow"/>

    {"".join(chevrons_svg)}
    {"".join(pipeline_nodes_svg)}
  </g>

  <!-- Crisp Outer Border -->
  <rect x="4" y="4" width="912" height="332" rx="18" fill="none" stroke="{outer_stroke}" stroke-opacity="{outer_stroke_opacity}" stroke-width="1.5"/>
</svg>
"""


def render_telemetry_strip(metrics: dict[str, Any], theme: str) -> str:
    """Render the 4-Card Live Account Telemetry Strip SVG (920x118) for Dark & Light modes."""
    is_dark = theme == "dark"

    card_bg = "#091318" if is_dark else "#f8fafc"
    card_border = "#18333b" if is_dark else "#cbd5e1"
    accent_primary = "#2dd4bf" if is_dark else "#0d9488"
    accent_emerald = "#34d399" if is_dark else "#059669"
    tag_color = "#5eead4" if is_dark else "#0f766e"
    val_color = "#f0fdfa" if is_dark else "#0f172a"
    lbl_color = "#94a3b8" if is_dark else "#475569"
    pill_bg = "#0d262c" if is_dark else "#e2e8f0"
    pill_txt = "#5eead4" if is_dark else "#0f766e"

    line_pts, area_pts = build_sparkline_paths(
        metrics.get("weekly_series", []), x0=826.0, y0=50.0, width=74.0, height=34.0
    )
    last_xy = line_pts.split(" ")[-1].split(",")
    tip_x, tip_y = last_xy[0], last_xy[1]

    cards_meta = [
        {
            "tag": "NET.PEERS",
            "value": format_compact(metrics["followers"]),
            "label": "Followers",
            "badge": "SYNCED",
            "accent": accent_primary,
        },
        {
            "tag": "SYS.WORKLOADS",
            "value": format_compact(metrics["public_repos"]),
            "label": "Repositories",
            "badge": "PUBLIC",
            "accent": accent_emerald,
        },
        {
            "tag": "REPO.STARGAZERS",
            "value": format_compact(metrics["total_stars"]),
            "label": "Total Stars",
            "badge": "EARNED",
            "accent": accent_primary,
        },
        {
            "tag": "GIT.OPERATIONS",
            "value": format_compact(metrics["contributions_1y"]),
            "label": "Contributions (1Y)",
            "badge": "16W TREND",
            "accent": accent_emerald,
        },
    ]

    cards_svg: list[str] = []
    card_w = 219
    gap = 12
    for idx, c in enumerate(cards_meta):
        cx = 4 + idx * (card_w + gap)
        badge_w = max(56, int(len(c["badge"]) * 6.5 + 16))
        badge_x = cx + card_w - badge_w - 16

        cards_svg.append(
            f"<g>"
            f'<rect x="{cx}" y="4" width="{card_w}" height="110" rx="12" fill="{card_bg}" stroke="{card_border}" stroke-width="1.2"/>'
            f'<rect x="{cx + 18}" y="4" width="52" height="2.5" rx="1.25" fill="{c["accent"]}"/>'
            f'<text x="{cx + 18}" y="28" fill="{tag_color}" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="10" font-weight="700" letter-spacing="0.8">{c["tag"]}</text>'
            f'<rect x="{badge_x}" y="16" width="{badge_w}" height="17" rx="8.5" fill="{pill_bg}"/>'
            f'<text x="{badge_x + badge_w / 2}" y="27.5" text-anchor="middle" fill="{pill_txt}" font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" font-size="9" font-weight="700">{c["badge"]}</text>'
            f'<text x="{cx + 18}" y="70" fill="{val_color}" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Inter, Roboto, sans-serif" font-size="31" font-weight="800" letter-spacing="-0.6">{c["value"]}</text>'
            f'<text x="{cx + 18}" y="96" fill="{lbl_color}" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Inter, Roboto, sans-serif" font-size="12.5" font-weight="500">{c["label"]}</text>'
            f"</g>"
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="920" height="118" viewBox="0 0 920 118" role="img" aria-label="Live GitHub Account Telemetry: Followers, Repositories, Total Stars, Contributions">
  <defs>
    <linearGradient id="sparkFill" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="{accent_emerald}" stop-opacity="0.32"/>
      <stop offset="100%" stop-color="{accent_emerald}" stop-opacity="0.0"/>
    </linearGradient>
  </defs>

  {"".join(cards_svg)}

  <!-- Live 16-Week Contribution Sparkline inside Card 4 -->
  <polygon points="{area_pts}" fill="url(#sparkFill)"/>
  <polyline points="{line_pts}" fill="none" stroke="{accent_emerald}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{tip_x}" cy="{tip_y}" r="3" fill="{accent_emerald}"/>
</svg>
"""


ACTIVITY_LINE_RE = re.compile(
    r"^(?P<ts>.+?\sUTC)\s+\[(?P<tag>[A-Z]+)\s*\]\s+(?P<repo>\S+)\s{2,}(?P<detail>.+)$"
)


def parse_activity_line(line: str) -> dict[str, str] | None:
    match = ACTIVITY_LINE_RE.match(line.strip())
    if not match:
        return None
    return {
        "ts": match.group("ts").strip(),
        "tag": match.group("tag").strip(),
        "repo": match.group("repo").strip(),
        "detail": match.group("detail").strip(),
    }


def format_activity_detail_svg(detail_raw: str, body_color: str, arrow_color: str, ref_color: str, rel_color: str) -> str:
    text = detail_raw.strip()
    if len(text) > 52:
        text = text[:51] + "…"

    if " -> " in text:
        prefix, target = text.split(" -> ", 1)
        return (
            f'<tspan fill="{body_color}">{html.escape(prefix)} </tspan>'
            f'<tspan fill="{arrow_color}" font-weight="700">→ </tspan>'
            f'<tspan fill="{ref_color}" font-weight="600">{html.escape(target)}</tspan>'
        )

    rel_match = re.match(r"^(.*\brelease\s+)(\S+)$", text)
    if rel_match:
        return (
            f'<tspan fill="{body_color}">{html.escape(rel_match.group(1))}</tspan>'
            f'<tspan fill="{rel_color}" font-weight="700">{html.escape(rel_match.group(2))}</tspan>'
        )

    num_match = re.match(r"^(.*?\s)(#\d+)(.*)$", text)
    if num_match:
        return (
            f'<tspan fill="{body_color}">{html.escape(num_match.group(1))}</tspan>'
            f'<tspan fill="{ref_color}" font-weight="700">{html.escape(num_match.group(2))}</tspan>'
            f'<tspan fill="{body_color}">{html.escape(num_match.group(3))}</tspan>'
        )

    paren_match = re.match(r"^(.*?\s)(\([^)]+\))$", text)
    if paren_match:
        return (
            f'<tspan fill="{body_color}">{html.escape(paren_match.group(1))}</tspan>'
            f'<tspan fill="{ref_color}" font-weight="600">{html.escape(paren_match.group(2))}</tspan>'
        )

    return f'<tspan fill="{body_color}">{html.escape(text)}</tspan>'


def render_activity_terminal(
    config: dict[str, Any],
    activity_lines: list[str],
    sync_str: str,
    theme: str,
) -> str:
    """Render a Bash Terminal SVG displaying live systemd-style journalctl GitHub events."""
    is_dark = theme == "dark"
    mono = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace"

    username_raw = str(config.get("username", "uvxv"))
    username = html.escape(username_raw)
    node_host = html.escape(str(config.get("node_host", f"{username_raw}@node-01")))

    rows_input = list(activity_lines) if activity_lines else [f"No recent public events recorded for @{username_raw}."]
    row_count = len(rows_input)
    sep_y = 94 + row_count * 28 + 6
    prompt_y = sep_y + 22
    svg_height = prompt_y + 20
    card_height = svg_height - 8

    # Theme palette
    bg_stop_1 = "#050d11" if is_dark else "#f8fafc"
    bg_stop_2 = "#08161c" if is_dark else "#f1f5f9"
    bg_stop_3 = "#050e13" if is_dark else "#f8fafc"
    outer_border = "#18333b" if is_dark else "#cbd5e1"
    titlebar_bg = "#091a21" if is_dark else "#e2e8f0"
    titlebar_border = "#18333b" if is_dark else "#cbd5e1"
    title_txt = "#94a3b8" if is_dark else "#475569"
    title_icon = "#2dd4bf" if is_dark else "#0d9488"

    status_bg = "#062825" if is_dark else "#d1fae5"
    status_border = "#10b981" if is_dark else "#34d399"
    status_dot = "#34d399" if is_dark else "#059669"
    status_txt = "#a7f3d0" if is_dark else "#065f46"

    prompt_user = "#34d399" if is_dark else "#059669"
    prompt_dir = "#38bdf8" if is_dark else "#0284c7"
    prompt_sym = "#94a3b8" if is_dark else "#475569"
    cmd_bin = "#f8fafc" if is_dark else "#0f172a"
    cmd_flag = "#94a3b8" if is_dark else "#475569"
    cmd_unit = "#2dd4bf" if is_dark else "#0f766e"
    comment_txt = "#64748b" if is_dark else "#64748b"
    divider_stroke = "#1e3a44" if is_dark else "#cbd5e1"

    zebra_bg = "#0b1d24" if is_dark else "#e2e8f0"
    zebra_opacity = "0.52" if is_dark else "0.5"
    ts_main = "#94a3b8" if is_dark else "#475569"
    ts_tz = "#64748b" if is_dark else "#64748b"
    repo_owner_col = "#94a3b8" if is_dark else "#64748b"
    repo_name_col = "#f8fafc" if is_dark else "#0f172a"
    detail_body = "#cbd5e1" if is_dark else "#334155"
    detail_arrow = "#2dd4bf" if is_dark else "#0d9488"
    detail_ref = "#5eead4" if is_dark else "#0f766e"
    detail_rel = "#34d399" if is_dark else "#047857"
    cursor_col = "#2dd4bf" if is_dark else "#0d9488"
    exit_ok_col = "#34d399" if is_dark else "#059669"

    tag_styles_dark: dict[str, tuple[str, str, str]] = {
        "PUSH": ("#082f49", "#0284c7", "#38bdf8"),
        "RELEASE": ("#064e3b", "#10b981", "#34d399"),
        "CREATE": ("#134e4a", "#14b8a6", "#2dd4bf"),
        "PR": ("#3b0764", "#a855f7", "#c084fc"),
        "ISSUE": ("#451a03", "#f59e0b", "#fbbf24"),
        "FORK": ("#1e1b4b", "#6366f1", "#818cf8"),
        "STAR": ("#422006", "#eab308", "#facc15"),
    }
    tag_styles_light: dict[str, tuple[str, str, str]] = {
        "PUSH": ("#e0f2fe", "#7dd3fc", "#0369a1"),
        "RELEASE": ("#d1fae5", "#6ee7b7", "#047857"),
        "CREATE": ("#ccfbf1", "#5eead4", "#0f766e"),
        "PR": ("#f3e8ff", "#d8b4fe", "#7e22ce"),
        "ISSUE": ("#fef3c7", "#fcd34d", "#b45309"),
        "FORK": ("#e0e7ff", "#a5b4fc", "#4338ca"),
        "STAR": ("#fef9c3", "#fde047", "#a16207"),
    }
    tag_palette = tag_styles_dark if is_dark else tag_styles_light
    default_tag_style = ("#0d262c", "#2dd4bf", "#5eead4") if is_dark else ("#e2e8f0", "#94a3b8", "#334155")

    rows_svg: list[str] = []
    for idx, raw_line in enumerate(rows_input):
        ry = 94 + idx * 28
        ty = ry + 16
        if idx % 2 == 0:
            rows_svg.append(
                f'<rect x="16" y="{ry}" width="888" height="24" rx="5" fill="{zebra_bg}" fill-opacity="{zebra_opacity}"/>'
            )

        parsed = parse_activity_line(raw_line)
        if not parsed:
            rows_svg.append(
                f'<text x="26" y="{ty}" fill="{comment_txt}" font-family="{mono}" font-size="12" font-style="italic">{html.escape(raw_line)}</text>'
            )
            continue

        ts_raw = parsed["ts"]
        if ts_raw.endswith(" UTC"):
            ts_dt_part = html.escape(ts_raw[:-4])
            ts_svg = f'<tspan fill="{ts_main}">{ts_dt_part}</tspan><tspan fill="{ts_tz}"> UTC</tspan>'
        else:
            ts_svg = f'<tspan fill="{ts_main}">{html.escape(ts_raw)}</tspan>'

        tag = parsed["tag"]
        t_bg, t_border, t_fg = tag_palette.get(tag, default_tag_style)
        t_bg_op = "0.75" if is_dark else "1.0"
        t_bd_op = "0.55" if is_dark else "0.95"

        repo_raw = parsed["repo"]
        if len(repo_raw) > 26:
            repo_raw = repo_raw[:25] + "…"
        if "/" in repo_raw:
            owner_p, repo_p = repo_raw.split("/", 1)
            repo_svg = (
                f'<tspan fill="{repo_owner_col}">{html.escape(owner_p)}/</tspan>'
                f'<tspan fill="{repo_name_col}" font-weight="700">{html.escape(repo_p)}</tspan>'
            )
        else:
            repo_svg = f'<tspan fill="{repo_name_col}" font-weight="700">{html.escape(repo_raw)}</tspan>'

        detail_svg = format_activity_detail_svg(
            parsed["detail"],
            body_color=detail_body,
            arrow_color=detail_arrow,
            ref_color=detail_ref,
            rel_color=detail_rel,
        )

        rows_svg.append(
            f"<g>"
            f'<text x="26" y="{ty}" font-family="{mono}" font-size="11.8">{ts_svg}</text>'
            f'<rect x="186" y="{ry + 3}" width="82" height="18" rx="4.5" fill="{t_bg}" fill-opacity="{t_bg_op}" stroke="{t_border}" stroke-opacity="{t_bd_op}" stroke-width="1"/>'
            f'<text x="227" y="{ry + 15.5}" text-anchor="middle" fill="{t_fg}" font-family="{mono}" font-size="10.5" font-weight="700">[{html.escape(tag)}]</text>'
            f'<text x="284" y="{ty}" font-family="{mono}" font-size="12">{repo_svg}</text>'
            f'<text x="496" y="{ty}" font-family="{mono}" font-size="12">{detail_svg}</text>'
            f"</g>"
        )

    shadow_def = (
        ""
        if is_dark
        else '<filter id="termShadow" x="-2%" y="-2%" width="104%" height="106%"><feDropShadow dx="0" dy="3" stdDeviation="5" flood-color="#0f172a" flood-opacity="0.08"/></filter>'
    )
    glow_overlay = (
        '<rect x="4" y="4" width="912" height="{h}" fill="url(#termGlow)"/><rect x="4" y="4" width="912" height="{h}" fill="url(#termGrid)"/>'.format(
            h=card_height
        )
        if is_dark
        else ""
    )

    desc_summary = html.escape(" | ".join(rows_input[:3]))

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="920" height="{svg_height}" viewBox="0 0 920 {svg_height}" role="img" aria-label="Bash terminal showing recent GitHub activity for @{username}">
  <title>journalctl -u github@{username}.service — Recent GitHub Activity</title>
  <desc>{desc_summary}</desc>
  <defs>
    <linearGradient id="termBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{bg_stop_1}"/>
      <stop offset="52%" stop-color="{bg_stop_2}"/>
      <stop offset="100%" stop-color="{bg_stop_3}"/>
    </linearGradient>
    <radialGradient id="termGlow" cx="15%" cy="10%" r="60%">
      <stop offset="0%" stop-color="#14b8a6" stop-opacity="0.16"/>
      <stop offset="60%" stop-color="#0f766e" stop-opacity="0.04"/>
      <stop offset="100%" stop-color="#050d11" stop-opacity="0"/>
    </radialGradient>
    <pattern id="termGrid" width="24" height="24" patternUnits="userSpaceOnUse">
      <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#2dd4bf" stroke-opacity="0.035" stroke-width="1"/>
    </pattern>
    <clipPath id="termClip">
      <rect x="4" y="4" width="912" height="{card_height}" rx="14"/>
    </clipPath>
    {shadow_def}
  </defs>

  <style>
    .cursor {{
      animation: termBlink 1.15s steps(2, start) infinite;
    }}
    .unit-pulse {{
      animation: unitPulse 2.6s ease-in-out infinite;
    }}
    @keyframes termBlink {{
      0%, 49% {{ opacity: 1; }}
      50%, 100% {{ opacity: 0; }}
    }}
    @keyframes unitPulse {{
      0%, 100% {{ opacity: 0.35; }}
      50% {{ opacity: 0.95; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .cursor, .unit-pulse {{ animation: none; }}
    }}
  </style>

  <!-- Outer Terminal Window -->
  <g {f'filter="url(#termShadow)"' if not is_dark else ''}>
    <rect x="4" y="4" width="912" height="{card_height}" rx="14" fill="url(#termBg)"/>
  </g>

  <g clip-path="url(#termClip)">
    {glow_overlay}

    <!-- Terminal Window Chrome / Title Bar -->
    <rect x="4" y="4" width="912" height="38" fill="{titlebar_bg}" fill-opacity="0.92"/>
    <rect x="4" y="42" width="912" height="1" fill="{titlebar_border}"/>

    <!-- Window Control Dots -->
    <circle cx="24" cy="23" r="5.5" fill="#ef4444"/>
    <circle cx="42" cy="23" r="5.5" fill="#f59e0b"/>
    <circle cx="60" cy="23" r="5.5" fill="#10b981"/>

    <!-- Window Title -->
    <text x="460" y="27" text-anchor="middle" fill="{title_txt}" font-family="{mono}" font-size="11.5" font-weight="600">
      <tspan fill="{title_icon}" font-weight="700">&gt;_</tspan>  bash — {node_host}: ~ (journalctl)
    </text>

    <!-- Systemd Unit Status Pill -->
    <rect x="752" y="12.5" width="146" height="21" rx="10.5" fill="{status_bg}" fill-opacity="0.9" stroke="{status_border}" stroke-opacity="0.45" stroke-width="1"/>
    <circle cx="767" cy="23" r="5.5" fill="{status_dot}" fill-opacity="0.24" class="unit-pulse"/>
    <circle cx="767" cy="23" r="2.8" fill="{status_dot}"/>
    <text x="777" y="26.8" fill="{status_txt}" font-family="{mono}" font-size="10" font-weight="700" letter-spacing="0.2">active (running)</text>

    <!-- Executed Bash Command -->
    <text x="26" y="69" font-family="{mono}" font-size="12.5">
      <tspan fill="{prompt_user}" font-weight="700">{node_host}</tspan><tspan fill="{prompt_sym}">:</tspan><tspan fill="{prompt_dir}" font-weight="700">~</tspan><tspan fill="{prompt_sym}">$ </tspan><tspan fill="{cmd_bin}" font-weight="700">journalctl</tspan><tspan fill="{cmd_flag}"> -u </tspan><tspan fill="{cmd_unit}" font-weight="600">github@{username}.service</tspan><tspan fill="{cmd_flag}"> -n {len(activity_lines) or 5} --no-pager</tspan>
    </text>
    <text x="894" y="69" text-anchor="end" fill="{comment_txt}" font-family="{mono}" font-size="10.8">-- synced {html.escape(sync_str)} --</text>

    <line x1="26" y1="83" x2="894" y2="83" stroke="{divider_stroke}" stroke-width="1" stroke-dasharray="3 4"/>

    <!-- Journalctl Event Stream -->
    {"".join(rows_svg)}

    <!-- Interactive Prompt & Pager Footer -->
    <line x1="26" y1="{sep_y}" x2="894" y2="{sep_y}" stroke="{divider_stroke}" stroke-opacity="0.65" stroke-width="1"/>
    <text x="26" y="{prompt_y}" font-family="{mono}" font-size="12.5">
      <tspan fill="{prompt_user}" font-weight="700">{node_host}</tspan><tspan fill="{prompt_sym}">:</tspan><tspan fill="{prompt_dir}" font-weight="700">~</tspan><tspan fill="{prompt_sym}">$ </tspan><tspan fill="{cursor_col}" class="cursor">▋</tspan>
    </text>
    <text x="894" y="{prompt_y}" text-anchor="end" fill="{comment_txt}" font-family="{mono}" font-size="10.8">
      lines 1-{row_count}/{row_count} (END)  ·  <tspan fill="{exit_ok_col}" font-weight="700">exit 0</tspan>
    </text>
  </g>

  <!-- Crisp Outer Terminal Border -->
  <rect x="4" y="4" width="912" height="{card_height}" rx="14" fill="none" stroke="{outer_border}" stroke-width="1.3"/>
</svg>
"""


def update_readme_activity(username: str) -> None:
    if not README_PATH.exists():
        return

    content = README_PATH.read_text(encoding="utf-8")
    username_esc = html.escape(username)

    block_lines = [
        "<!-- START_SECTION:activity -->",
        '<div align="center">',
        "  <picture>",
        '    <source media="(prefers-color-scheme: dark)" srcset="./assets/activity-dark.svg" />',
        '    <source media="(prefers-color-scheme: light)" srcset="./assets/activity-light.svg" />',
        f'    <img alt="Recent GitHub Activity — journalctl -u github@{username_esc}.service" src="./assets/activity-dark.svg" width="100%" />',
        "  </picture>",
        "</div>",
        "<!-- END_SECTION:activity -->",
    ]
    replacement = "\n".join(block_lines)

    pattern = re.compile(
        r"<!-- START_SECTION:activity -->.*?<!-- END_SECTION:activity -->",
        re.DOTALL,
    )
    if pattern.search(content):
        updated = pattern.sub(replacement, content)
        if updated != content:
            README_PATH.write_text(updated, encoding="utf-8")


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    username = config["username"]
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
    now = dt.datetime.now(dt.timezone.utc)
    sync_str = now.strftime("%Y-%m-%d %H:%M UTC")

    metrics = None
    if token:
        metrics = fetch_graphql_metrics(username, token)
    if metrics is None:
        metrics = fetch_rest_metrics(username, token=token)

    snap = load_snapshot()
    activity_lines, latest_event_repo = fetch_recent_events(
        username,
        token=token,
        max_items=int(config.get("max_activity_events", 5)),
        recent_repos=metrics.get("recent_repos"),
    )
    if not activity_lines and snap.get("activity_lines"):
        activity_lines = list(snap["activity_lines"])
    if latest_event_repo:
        metrics["latest_repo"] = latest_event_repo

    metrics["uptime"] = compute_uptime(metrics.get("created_at", ""), now)

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(
            {
                "created_at": metrics["created_at"],
                "followers": metrics["followers"],
                "public_repos": metrics["public_repos"],
                "total_stars": metrics["total_stars"],
                "contributions_1y": metrics["contributions_1y"],
                "weekly_series": metrics["weekly_series"],
                "latest_repo": metrics["latest_repo"],
                "activity_lines": activity_lines,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (ASSETS_DIR / "hud-banner-dark.svg").write_text(
        render_hud_banner(config, metrics, sync_str, theme="dark"), encoding="utf-8"
    )
    (ASSETS_DIR / "hud-banner-light.svg").write_text(
        render_hud_banner(config, metrics, sync_str, theme="light"), encoding="utf-8"
    )
    (ASSETS_DIR / "telemetry-dark.svg").write_text(
        render_telemetry_strip(metrics, theme="dark"), encoding="utf-8"
    )
    (ASSETS_DIR / "telemetry-light.svg").write_text(
        render_telemetry_strip(metrics, theme="light"), encoding="utf-8"
    )
    (ASSETS_DIR / "activity-dark.svg").write_text(
        render_activity_terminal(config, activity_lines, sync_str, theme="dark"), encoding="utf-8"
    )
    (ASSETS_DIR / "activity-light.svg").write_text(
        render_activity_terminal(config, activity_lines, sync_str, theme="light"), encoding="utf-8"
    )

    update_readme_activity(username)
    print(
        f"[ok] Synced telemetry for @{username}: "
        f"followers={metrics['followers']}, repos={metrics['public_repos']}, "
        f"stars={metrics['total_stars']}, contributions_1y={metrics['contributions_1y']}, "
        f"uptime={metrics['uptime']}"
    )


if __name__ == "__main__":
    main()
