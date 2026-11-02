#!/usr/bin/env python3
"""Check a Labs proposal issue against the real upstream and report on the issue.

Run by .github/workflows/check.yml. It reads the event payload (GITHUB_EVENT_PATH), parses the issue form, checks
it against GitHub's REST API with GITHUB_TOKEN, keeps one comment on the issue up to date and labels the issue
meets-bar or needs-info. The issue body is untrusted: it is only ever parsed as data here.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

LABS_ORG = "linuxserverlabs-playground"
# Where a name has to be free, so an image can graduate from Labs without colliding.
NAME_ORGS = (LABS_ORG, "linuxserver-playground", "linuxserver")

MARKER = "<!-- labs-proposal-check -->"
BOT_LOGIN = "github-actions[bot]"
LABEL_PROPOSAL = "proposal"
LABEL_MEETS = "meets-bar"
LABEL_NEEDS = "needs-info"
LABEL_ACCEPTED = "accepted"
LABELS = {
    LABEL_PROPOSAL: ("1d76db", "A proposal for a Labs image"),
    LABEL_MEETS: ("0e8a16", "The automatic check found nothing missing; waiting for a member to try it"),
    LABEL_NEEDS: ("d93f0b", "The automatic check found something missing; see its comment"),
    LABEL_ACCEPTED: ("5319e7", "A member created the image repository and opened its first pull request"),
}

# The form's labels, exactly as .github/ISSUE_TEMPLATE/proposal.yml has them. GitHub renders a submission as
# "### <label>" blocks, so these are what the parser keys on.
FIELDS = {
    "name": "Image name",
    "upstream_repo": "Upstream repository",
    "homepage": "Project homepage",
    "license": "Licence",
    "release_url": "Latest stable release",
    "build_from": "How the image installs it",
    "asset_amd64": "amd64 release asset",
    "asset_arm64": "arm64 release asset",
    "app_type": "Application type",
    "runtime": "Runtime and install",
    "web_port": "Web interface port",
    "state_paths": "Where it writes state",
    "env_vars": "Required environment variables",
    "database": "Database",
    "first_run": "First run",
    "screenshot": "Screenshot",
    "maintainers": "Maintainers",
    "bar": "The Labs bar",
}
LABEL_TO_ID = {label: key for key, label in FIELDS.items()}
REQUIRED = (
    "name", "upstream_repo", "license", "release_url", "build_from", "app_type", "runtime", "state_paths",
    "database", "first_run", "maintainers",
)
BAR = (
    "The upstream repository is owned by an organisation, not a personal account",
    "It has an approved open-source licence",
    "It cuts stable, versioned releases",
    "Release asset names stay the same from release to release",
    "It works with just this container (no companion containers)",
    "It needs no account with a paid vendor service to work",
    "Any telemetry is disclosed and can be turned off",
)
BUILD_ASSET = "Release asset"
BUILD_SOURCE = "Source release"
APP_WEB = "Web application"
APP_DESKTOP = "Desktop application (selkies)"
NO_RESPONSE = "_No response_"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.*?)\s*$")

# OSI-approved licences as GitHub's licence detection names them (it reports GPL-3.0 for both -only and -or-later).
OSI_LICENCES = {
    "0BSD", "AFL-3.0", "AGPL-3.0", "APACHE-2.0", "ARTISTIC-2.0", "BSD-2-CLAUSE", "BSD-3-CLAUSE",
    "BSD-3-CLAUSE-CLEAR", "BSD-4-CLAUSE", "BSL-1.0", "CDDL-1.0", "CECILL-2.1", "ECL-2.0", "EPL-1.0", "EPL-2.0",
    "EUPL-1.1", "EUPL-1.2", "GPL-2.0", "GPL-3.0", "ISC", "LGPL-2.1", "LGPL-3.0", "LPPL-1.3C", "MIT", "MIT-0",
    "MPL-2.0", "MS-PL", "MS-RL", "MULANPSL-2.0", "NCSA", "OFL-1.1", "OSL-3.0", "POSTGRESQL", "UNLICENSE", "UPL-1.0",
    "VIM", "ZLIB",
}

PASS, WARN, FAIL, MANUAL, ERROR, SKIP = "pass", "warn", "fail", "manual", "error", "skip"
RESULT_TEXT = {
    PASS: "Pass",
    WARN: "Warning",
    FAIL: "**Fail**",
    MANUAL: "Member checks",
    ERROR: "Could not run",
    SKIP: "Skipped",
}


# ---------------------------------------------------------------------------------------------------------------
# Parsing


def parse_form(body: str | None) -> dict:
    """Parse an issue form submission into {field id: value}.

    Only "### " headings that are one of the form's labels start a field, so an answer that contains a Markdown
    heading of its own stays in its field. "_No response_" becomes "". The bar becomes {option text: ticked}.
    """
    values: dict = {}
    current = None
    lines: list[str] = []

    def finish():
        if current is not None:
            values[current] = "\n".join(lines).strip()

    for line in (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = re.match(r"^###\s+(.+?)\s*$", line)
        if match and match.group(1) in LABEL_TO_ID:
            finish()
            current = LABEL_TO_ID[match.group(1)]
            lines = []
        elif current is not None:
            lines.append(line)
    finish()

    for key, value in list(values.items()):
        if value == NO_RESPONSE:
            values[key] = ""
    values["bar"] = parse_checkboxes(values.get("bar", ""))
    return values


def parse_checkboxes(text: str) -> dict[str, bool]:
    boxes = {}
    for line in text.split("\n"):
        match = CHECKBOX_RE.match(line)
        if match:
            boxes[match.group(2)] = match.group(1).lower() == "x"
    return boxes


def parse_upstream(url: str) -> tuple[str, str | None]:
    """Return (host, "owner/repo") for a repository URL; "owner/repo" is None when it cannot be read."""
    url = url.strip()
    if re.match(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", url):
        return "github.com", url.removesuffix(".git")
    parsed = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return host, None
    return host, f"{parts[0]}/{parts[1].removesuffix('.git')}"


def parse_logins(text: str) -> list[str]:
    return [part.strip().lstrip("@") for part in re.split(r"[,\s]+", text) if part.strip().lstrip("@")]


def normalise_licence(spdx: str) -> str:
    value = spdx.strip().upper()
    for suffix in ("-ONLY", "-OR-LATER", "+"):
        value = value.removesuffix(suffix)
    return value


# ---------------------------------------------------------------------------------------------------------------
# GitHub


class ApiError(Exception):
    pass


class GitHub:
    """A small REST client. get() returns (status, json); 404 is a normal answer, anything else non-2xx raises."""

    def __init__(self, token: str, api: str = "https://api.github.com"):
        self.token = token
        self.api = api.rstrip("/")

    def request(self, method: str, path: str, data=None, ok=(200, 201, 204), attempts: int = 3):
        url = path if path.startswith("http") else f"{self.api}/{path.lstrip('/')}"
        body = json.dumps(data).encode() if data is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "labs-proposal-check",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return resp.status, json.loads(raw) if raw else None
            except urllib.error.HTTPError as err:
                if err.code in ok or err.code == 404:
                    return err.code, None
                if err.code >= 500 and attempt < attempts:
                    time.sleep(2 * attempt)
                    continue
                raise ApiError(f"{method} {path}: HTTP {err.code}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                if attempt < attempts:
                    time.sleep(2 * attempt)
                    continue
                raise ApiError(f"{method} {path}: {err}") from None
        raise ApiError(f"{method} {path}: gave up")

    def get(self, path: str):
        return self.request("GET", path)


# ---------------------------------------------------------------------------------------------------------------
# Checks


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Context:
    """What one check learns that a later one needs."""

    form: dict
    accepted: bool = False
    repo: dict | None = None
    repo_state: str = ""  # "github", "other-host", "missing", "error"
    release: dict | None = None
    notes: list[str] = field(default_factory=list)


def code(value: str, limit: int = 80) -> str:
    """Show untrusted text inline: one line, no backticks or pipes, truncated, in a code span (no mentions)."""
    value = re.sub(r"\s+", " ", value).replace("`", "'").replace("|", "/").strip()
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return f"`{value}`" if value else "(empty)"


def link(text: str, url: str) -> str:
    return f"[{text}]({url})"


def check_complete(ctx: Context) -> Check:
    missing = [FIELDS[key] for key in REQUIRED if not ctx.form.get(key)]
    if missing:
        return Check("Form complete", FAIL, "Missing: " + ", ".join(missing) + ".")
    return Check("Form complete", PASS, "Every required field has an answer.")


def check_bar(ctx: Context) -> Check:
    boxes = ctx.form.get("bar") or {}
    unticked = [text for text in BAR if not boxes.get(text)]
    if unticked:
        return Check("The Labs bar", FAIL, "Not ticked: " + "; ".join(unticked) + ".")
    return Check("The Labs bar", PASS, f"All {len(BAR)} points ticked.")


def check_name(ctx: Context, gh: GitHub) -> Check:
    name = ctx.form.get("name", "").strip()
    title = "Image name"
    if not name:
        return Check(title, FAIL, "No image name given.")
    if name.startswith("docker-"):
        return Check(title, FAIL, f"{code(name)}: give the name without `docker-`.")
    if not NAME_RE.match(name):
        return Check(title, FAIL, f"{code(name)} is not a valid name: lowercase letters, digits and dashes only.")
    taken, errors = [], []
    for org in NAME_ORGS:
        repo = f"{org}/docker-{name}"
        try:
            status, data = gh.get(f"repos/{repo}")
        except ApiError as err:
            errors.append(f"{repo} ({err})")
            continue
        if status == 404:
            continue
        if org == LABS_ORG and ctx.accepted:
            ctx.notes.append(f"{link(repo, f'https://github.com/{repo}')} was created for this proposal.")
            continue
        url = (data or {}).get("html_url", f"https://github.com/{repo}")
        taken.append(link(repo, url))
    if taken:
        return Check(title, FAIL, f"{code(name)} is taken: " + ", ".join(taken) + ". Choose another name.")
    if errors:
        return Check(title, ERROR, "Could not look up " + ", ".join(errors) + ".")
    return Check(title, PASS, f"{code(name)} is free in Labs, LinuxServer.io and its playground.")


def check_upstream(ctx: Context, gh: GitHub) -> Check:
    title = "Upstream owned by an organisation"
    url = ctx.form.get("upstream_repo", "")
    if not url:
        ctx.repo_state = "missing"
        return Check(title, FAIL, "No upstream repository given.")
    host, slug = parse_upstream(url)
    if host != "github.com":
        ctx.repo_state = "other-host"
        return Check(title, MANUAL, f"{code(url)} is not on GitHub, so a member checks the owner, licence and "
                                    "releases by hand during the trial.")
    if not slug:
        ctx.repo_state = "missing"
        return Check(title, FAIL, f"{code(url)} is not a repository URL (https://github.com/<owner>/<repo>).")
    try:
        status, repo = gh.get(f"repos/{slug}")
    except ApiError as err:
        ctx.repo_state = "error"
        return Check(title, ERROR, f"Could not look up {code(slug)}: {err}.")
    if status == 404 or not repo:
        ctx.repo_state = "missing"
        return Check(title, FAIL, f"{code(slug)} does not exist on GitHub, or is private.")
    ctx.repo, ctx.repo_state = repo, "github"
    owner = repo.get("owner") or {}
    where = link(repo["full_name"], repo["html_url"])
    if repo.get("archived"):
        ctx.notes.append(f"{where} is archived upstream.")
    if owner.get("type") == "Organization":
        return Check(title, PASS, f"{where} belongs to the organisation "
                                  f"{link(owner['login'], owner.get('html_url', ''))}.")
    return Check(title, FAIL, f"{where} belongs to the personal account {code(owner.get('login', ''))}. Labs "
                              "needs an organisation (free to create); transfer the repository to one first.")


def _skip(title: str, ctx: Context) -> Check | None:
    if ctx.repo_state == "other-host":
        return Check(title, MANUAL, "Not on GitHub: a member checks this during the trial.")
    if ctx.repo_state == "error":
        return Check(title, ERROR, "The upstream repository could not be read.")
    if ctx.repo_state != "github":
        return Check(title, SKIP, "Needs a valid upstream repository first.")
    return None


def check_licence(ctx: Context) -> Check:
    title = "Licence"
    skipped = _skip(title, ctx)
    if skipped:
        return skipped
    repo = ctx.repo or {}
    stated = ctx.form.get("license", "").strip()
    detected = (repo.get("license") or {}).get("spdx_id") or ""
    where = f"{repo['html_url']}#license" if repo.get("html_url") else ""
    if not detected:
        return Check(title, FAIL, f"GitHub finds no licence in {link(repo.get('full_name', ''), where)}. Add a "
                                  "LICENSE file with an approved open-source licence.")
    if detected == "NOASSERTION":
        return Check(title, WARN, f"GitHub finds a licence file it cannot identify; a member checks that "
                                  f"{code(stated)} is right.")
    detail = f"GitHub detects {link(detected, where)}"
    if normalise_licence(detected) not in OSI_LICENCES:
        return Check(title, WARN, detail + ", which this check does not know as OSI-approved; a member checks it.")
    if not stated:
        return Check(title, WARN, detail + "; the form does not say which licence.")
    if normalise_licence(stated) != normalise_licence(detected):
        return Check(title, WARN, detail + f", but the form says {code(stated)}. Correct whichever is wrong.")
    return Check(title, PASS, detail + ", as stated.")


def check_release(ctx: Context, gh: GitHub) -> Check:
    title = "Stable release"
    skipped = _skip(title, ctx)
    if skipped:
        return skipped
    slug = ctx.repo["full_name"]
    try:
        status, release = gh.get(f"repos/{slug}/releases/latest")
    except ApiError as err:
        return Check(title, ERROR, f"Could not read the latest release: {err}.")
    if status == 404 or not release:
        return Check(title, FAIL, f"{link(slug, ctx.repo['html_url'] + '/releases')} has no stable release "
                                  "(pre-releases and plain tags do not count). Publish a versioned release.")
    ctx.release = release
    tag = release.get("tag_name", "")
    detail = f"{link(tag, release['html_url'])}, published {release.get('published_at', '')[:10]}"
    stated = ctx.form.get("release_url", "").strip()
    _, stated_slug = parse_upstream(stated) if stated else ("", None)
    stated_tag = stated.rstrip("/").rsplit("/releases/tag/", 1)[1] if "/releases/tag/" in stated else ""
    if stated_slug and stated_slug.lower() != slug.lower():
        return Check(title, WARN, detail + f". The form's release link {code(stated)} is not in the upstream "
                                           "repository.")
    if stated_tag and urllib.parse.unquote(stated_tag) != tag:
        return Check(title, WARN, detail + f". The form names {code(stated_tag)}; the image follows the "
                                           "latest stable release.")
    return Check(title, PASS, detail + ".")


def match_asset(pattern: str, names: list[str]) -> tuple[list[str], str | None]:
    """Assets a form answer names: the exact file name, else a regular expression matched on the whole name."""
    if pattern in names:
        return [pattern], None
    try:
        regex = re.compile(pattern)
    except re.error as err:
        return [], f"not a file name in the release, nor a valid regular expression ({err})"
    return [name for name in names if regex.fullmatch(name)], None


def check_assets(ctx: Context) -> list[Check]:
    build = ctx.form.get("build_from", "")
    if build == BUILD_SOURCE:
        release = ctx.release or {}
        detail = "The image builds from the release's source"
        if release.get("tarball_url"):
            detail += f" ({link('source tarball', release['tarball_url'])})"
        return [Check("Release assets", PASS, detail + ".")]
    if build != BUILD_ASSET:
        return [Check("Release assets", SKIP, "Choose how the image installs the application first.")]
    skipped = _skip("Release assets", ctx)
    if skipped and skipped.status != SKIP:
        return [skipped]
    release = ctx.release
    if skipped or not release:
        return [Check("Release assets", SKIP, "Needs a stable release first.")]
    names = [asset["name"] for asset in release.get("assets", [])]
    version = release.get("tag_name", "").removeprefix("v")
    checks = []
    for key, arch, required in (("asset_amd64", "amd64", True), ("asset_arm64", "arm64", False)):
        title = f"{arch} asset"
        pattern = ctx.form.get(key, "").strip()
        if not pattern:
            if required:
                checks.append(Check(title, FAIL, "A release asset build needs the amd64 asset's name or pattern."))
            else:
                checks.append(Check(title, PASS, "None given: the image will be amd64 only."))
            continue
        matched, error = match_asset(pattern, names)
        if error:
            checks.append(Check(title, FAIL, f"{code(pattern)} is {error}."))
        elif not matched:
            checks.append(Check(title, FAIL, f"{code(pattern)} matches no asset of {release.get('tag_name', '')}."))
        elif len(matched) > 1:
            checks.append(Check(title, WARN, f"{code(pattern)} matches {len(matched)} assets ("
                                             + ", ".join(code(n, 60) for n in matched[:4])
                                             + "); make it match exactly one."))
        elif pattern == matched[0] and version and version in pattern:
            general = code(pattern.replace(version, ".*"))
            checks.append(Check(title, WARN, f"{code(pattern)} exists, but it names this version; write it as "
                                             f"{general} so it matches the next release."))
        else:
            checks.append(Check(title, PASS, f"{code(pattern)} matches {code(matched[0], 60)}."))
    return checks


def check_app_type(ctx: Context) -> Check:
    title = "Web interface"
    app_type = ctx.form.get("app_type", "")
    port = ctx.form.get("web_port", "").strip()
    if app_type == APP_DESKTOP:
        return Check(title, PASS, "Desktop application: the Selkies base serves it on ports 3000 and 3001.")
    if app_type != APP_WEB:
        return Check(title, SKIP, "Choose the application type first.")
    if not port:
        return Check(title, FAIL, "A web application needs its interface port.")
    if not port.isdigit() or not 0 < int(port) < 65536:
        return Check(title, FAIL, f"{code(port)} is not a port number.")
    return Check(title, PASS, f"Port {port}.")


def check_maintainers(ctx: Context, gh: GitHub) -> Check:
    title = "Maintainers"
    logins = parse_logins(ctx.form.get("maintainers", ""))
    if not logins:
        return Check(title, FAIL, "Name at least one GitHub login.")
    found, problems, errors = [], [], []
    for login in logins:
        if not LOGIN_RE.match(login):
            problems.append(f"{code(login)} is not a GitHub login")
            continue
        try:
            status, user = gh.get(f"users/{login}")
        except ApiError as err:
            errors.append(f"{code(login)} ({err})")
            continue
        if status == 404 or not user:
            problems.append(f"{code(login)} is not a GitHub user")
        elif user.get("type") != "User":
            problems.append(f"{code(login)} is an organisation, not a person")
        else:
            # A link, never an @mention: the check should not notify anyone.
            found.append(link(user["login"], user["html_url"]))
    if problems:
        return Check(title, FAIL, "; ".join(problems) + ".")
    if errors:
        return Check(title, ERROR, "Could not look up " + ", ".join(errors) + ".")
    return Check(title, PASS, ", ".join(found) + ".")


def run_checks(form: dict, gh: GitHub, accepted: bool = False) -> tuple[list[Check], Context]:
    ctx = Context(form=form, accepted=accepted)
    checks = [check_complete(ctx), check_bar(ctx), check_name(ctx, gh), check_upstream(ctx, gh), check_licence(ctx),
              check_release(ctx, gh)]
    checks += check_assets(ctx)
    checks += [check_app_type(ctx), check_maintainers(ctx, gh)]
    return checks, ctx


def decide(checks: list[Check]) -> str:
    """meets-bar when nothing failed and every check could run; needs-info otherwise."""
    if any(check.status in (FAIL, ERROR) for check in checks):
        return LABEL_NEEDS
    return LABEL_MEETS


def render(checks: list[Check], ctx: Context, label: str, run_url: str = "") -> str:
    name = ctx.form.get("name", "").strip() or "<name>"
    failed = [c for c in checks if c.status == FAIL]
    errored = [c for c in checks if c.status == ERROR]
    manual = [c for c in checks if c.status == MANUAL]
    lines = [MARKER, "## Proposal check", ""]
    if label == LABEL_MEETS:
        lines.append("**Meets the bar as far as an automatic check can tell.** A member will try the image in a "
                     f"sandbox. If it works, they create `docker-{name}` in Labs and open its first pull request "
                     "from this issue, which builds the image and posts a test report for review.")
        if manual:
            lines += ["", "Some points cannot be checked automatically; the member checks them during the trial."]
    else:
        parts = []
        if failed:
            parts.append(f"{len(failed)} check{'s' if len(failed) != 1 else ''} failed")
        if errored:
            parts.append(f"{len(errored)} could not run")
        lines.append(f"**Needs more information:** {' and '.join(parts)}. Edit the issue to fix what is marked "
                     "below; this comment updates every time you do.")
        if errored:
            lines += ["", "A check that could not run is a problem on our side (GitHub's API did not answer), not "
                          "in your proposal. It runs again on your next edit, or a member can re-run it."]
    lines += ["", "| Check | Result | Details |", "| --- | --- | --- |"]
    for check in checks:
        lines.append(f"| {check.name} | {RESULT_TEXT[check.status]} | {check.detail} |")
    if ctx.notes:
        lines += ["", "Notes:", ""] + [f"- {note}" for note in ctx.notes]
    footer = "Checked against the upstream with GitHub's API. A warning does not block the proposal."
    if run_url:
        footer += f" {link('Run log', run_url)}."
    lines += ["", f"<sub>{footer}</sub>", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# Reporting on the issue


def find_comment(gh: GitHub, repo: str, number: int) -> int | None:
    page = 1
    while True:
        status, comments = gh.get(f"repos/{repo}/issues/{number}/comments?per_page=100&page={page}")
        if status != 200 or not comments:
            return None
        for comment in comments:
            if (comment.get("user") or {}).get("login") == BOT_LOGIN and MARKER in (comment.get("body") or ""):
                return comment["id"]
        if len(comments) < 100:
            return None
        page += 1


def upsert_comment(gh: GitHub, repo: str, number: int, body: str) -> None:
    comment_id = find_comment(gh, repo, number)
    if comment_id:
        gh.request("PATCH", f"repos/{repo}/issues/comments/{comment_id}", {"body": body})
    else:
        gh.request("POST", f"repos/{repo}/issues/{number}/comments", {"body": body})


def ensure_label(gh: GitHub, repo: str, name: str) -> None:
    status, _ = gh.get(f"repos/{repo}/labels/{urllib.parse.quote(name)}")
    if status == 404:
        colour, description = LABELS[name]
        # 422 means another run created it meanwhile.
        gh.request("POST", f"repos/{repo}/labels", {"name": name, "color": colour, "description": description},
                   ok=(201, 422))


def set_label(gh: GitHub, repo: str, number: int, label: str, current: set[str]) -> None:
    other = LABEL_NEEDS if label == LABEL_MEETS else LABEL_MEETS
    if label not in current:
        ensure_label(gh, repo, label)
        gh.request("POST", f"repos/{repo}/issues/{number}/labels", {"labels": [label]})
    if other in current:
        gh.request("DELETE", f"repos/{repo}/issues/{number}/labels/{urllib.parse.quote(other)}")


def load_issue(gh: GitHub, repo: str, event: dict) -> dict | None:
    if event.get("issue"):
        return event["issue"]
    number = str((event.get("inputs") or {}).get("issue", "")).strip()
    if not number.isdigit():
        print("No issue in the event and no issue number given.", file=sys.stderr)
        return None
    status, issue = gh.get(f"repos/{repo}/issues/{number}")
    if status == 404 or not issue or issue.get("pull_request"):
        print(f"Issue {number} not found.", file=sys.stderr)
        return None
    return issue


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    gh = GitHub(os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    run_url = ""
    if os.environ.get("GITHUB_RUN_ID"):
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        run_url = f"{server}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"

    try:
        issue = load_issue(gh, repo, event)
    except ApiError as err:
        print(f"Could not read the issue: {err}", file=sys.stderr)
        return 1
    if issue is None:
        return 1
    labels = {label["name"] for label in issue.get("labels", [])}
    if LABEL_PROPOSAL not in labels:
        print(f"Issue {issue['number']} is not labelled {LABEL_PROPOSAL}; nothing to check.")
        return 0

    form = parse_form(issue.get("body"))
    checks, ctx = run_checks(form, gh, accepted=LABEL_ACCEPTED in labels)
    label = decide(checks)
    for check in checks:
        print(f"{check.status:7} {check.name}: {check.detail}")
    print(f"Result: {label}")
    try:
        upsert_comment(gh, repo, issue["number"], render(checks, ctx, label, run_url))
        set_label(gh, repo, issue["number"], label, labels)
    except ApiError as err:
        print(f"Could not report on issue {issue['number']}: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
