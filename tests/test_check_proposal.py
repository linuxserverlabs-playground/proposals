import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import check_proposal as cp  # noqa: E402


def fixture(name: str) -> str:
    return (HERE / "fixtures" / name).read_text(encoding="utf-8")


ASSETS = [
    "photocraft-0.19.0-linux-aarch64.AppImage",
    "photocraft-0.19.0-linux-aarch64.deb",
    "photocraft-0.19.0-linux-x86_64.AppImage",
    "photocraft-0.19.0-linux-x86_64.deb",
    "photocraft-0.19.0-linux-x86_64.rpm",
    "SHA256SUMS.txt",
]

PHOTOCRAFT_API = {
    "repos/storytold/photocraft": {
        "full_name": "storytold/photocraft",
        "html_url": "https://github.com/storytold/photocraft",
        "owner": {"login": "storytold", "type": "Organization", "html_url": "https://github.com/storytold"},
        "license": {"spdx_id": "Apache-2.0"},
        "archived": False,
    },
    "repos/storytold/photocraft/releases/latest": {
        "tag_name": "v0.19.0",
        "html_url": "https://github.com/storytold/photocraft/releases/tag/v0.19.0",
        "published_at": "2026-10-20T12:00:00Z",
        "tarball_url": "https://api.github.com/repos/storytold/photocraft/tarball/v0.19.0",
        "assets": [{"name": name} for name in ASSETS],
    },
    "users/thelamer": {"login": "thelamer", "type": "User", "html_url": "https://github.com/thelamer"},
}


class FakeGitHub:
    """Answers GET from a dict of path -> json; a missing path is a 404, an exception value is raised."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path not in self.answers:
            return 404, None
        value = self.answers[path]
        if isinstance(value, Exception):
            raise value
        return 200, value


def by_name(checks):
    return {check.name: check for check in checks}


class ParseTest(unittest.TestCase):
    def test_photocraft_form(self):
        form = cp.parse_form(fixture("photocraft.md"))
        self.assertEqual(form["name"], "photocraft")
        self.assertEqual(form["upstream_repo"], "https://github.com/storytold/photocraft")
        self.assertEqual(form["license"], "Apache-2.0")
        self.assertEqual(form["build_from"], cp.BUILD_ASSET)
        self.assertEqual(form["asset_amd64"], "photocraft-.*-linux-x86_64.deb")
        self.assertEqual(form["app_type"], cp.APP_DESKTOP)
        self.assertEqual(form["web_port"], "")
        self.assertEqual(form["env_vars"], "")
        self.assertEqual(form["maintainers"], "thelamer")
        self.assertEqual(len(form["bar"]), len(cp.BAR))
        self.assertTrue(all(form["bar"].values()))
        self.assertEqual(set(form["bar"]), set(cp.BAR))

    def test_heading_inside_an_answer_stays_in_its_field(self):
        form = cp.parse_form(fixture("webapp.md"))
        self.assertIn("### Install", form["runtime"])
        self.assertIn("node server.js", form["runtime"])
        self.assertEqual(form["web_port"], "")

    def test_unticked_boxes(self):
        form = cp.parse_form(fixture("webapp.md"))
        self.assertFalse(form["bar"]["It cuts stable, versioned releases"])
        self.assertTrue(form["bar"]["Any telemetry is disclosed and can be turned off"])

    def test_crlf_and_empty_body(self):
        form = cp.parse_form(fixture("photocraft.md").replace("\n", "\r\n"))
        self.assertEqual(form["name"], "photocraft")
        self.assertEqual(cp.parse_form(None), {"bar": {}})
        self.assertEqual(cp.parse_form("free text, not a form"), {"bar": {}})

    def test_form_labels_match_the_issue_form(self):
        template = (HERE.parent / ".github" / "ISSUE_TEMPLATE" / "proposal.yml").read_text(encoding="utf-8")
        for label in cp.FIELDS.values():
            self.assertIn(f"label: {label}\n", template)
        for option in cp.BAR:
            self.assertIn(f"- label: {option}\n", template)

    def test_parse_upstream(self):
        self.assertEqual(cp.parse_upstream("https://github.com/storytold/photocraft"),
                         ("github.com", "storytold/photocraft"))
        self.assertEqual(cp.parse_upstream("https://www.github.com/storytold/photocraft.git/"),
                         ("github.com", "storytold/photocraft"))
        self.assertEqual(cp.parse_upstream("github.com/storytold/photocraft/tree/main"),
                         ("github.com", "storytold/photocraft"))
        self.assertEqual(cp.parse_upstream("storytold/photocraft"), ("github.com", "storytold/photocraft"))
        self.assertEqual(cp.parse_upstream("https://codeberg.org/forgejo/forgejo")[0], "codeberg.org")
        self.assertEqual(cp.parse_upstream("https://github.com/storytold"), ("github.com", None))

    def test_parse_logins_and_licences(self):
        self.assertEqual(cp.parse_logins("@alice, bob carol,,"), ["alice", "bob", "carol"])
        self.assertEqual(cp.normalise_licence("GPL-3.0-or-later"), "GPL-3.0")
        self.assertEqual(cp.normalise_licence("agpl-3.0-only"), "AGPL-3.0")


class CheckTest(unittest.TestCase):
    def run_photocraft(self, body=None, answers=None, accepted=False):
        form = cp.parse_form(body or fixture("photocraft.md"))
        gh = FakeGitHub(PHOTOCRAFT_API if answers is None else answers)
        checks, ctx = cp.run_checks(form, gh, accepted=accepted)
        return checks, ctx, gh

    def test_photocraft_meets_the_bar(self):
        checks, ctx, gh = self.run_photocraft()
        failing = [c for c in checks if c.status not in (cp.PASS,)]
        self.assertEqual(failing, [])
        self.assertEqual(cp.decide(checks), cp.LABEL_MEETS)
        for org in cp.NAME_ORGS:
            self.assertIn(f"repos/{org}/docker-photocraft", gh.calls)
        named = by_name(checks)
        self.assertIn("photocraft-0.19.0-linux-x86_64.deb", named["amd64 asset"].detail)
        self.assertIn("photocraft-0.19.0-linux-aarch64.deb", named["arm64 asset"].detail)

    def test_name_taken_in_labs(self):
        answers = dict(PHOTOCRAFT_API)
        answers[f"repos/{cp.LABS_ORG}/docker-photocraft"] = {
            "html_url": f"https://github.com/{cp.LABS_ORG}/docker-photocraft"}
        checks, _, _ = self.run_photocraft(answers=answers)
        name = by_name(checks)["Image name"]
        self.assertEqual(name.status, cp.FAIL)
        self.assertIn("docker-photocraft", name.detail)
        self.assertEqual(cp.decide(checks), cp.LABEL_NEEDS)
        # Once accepted, the Labs repository is the proposal's own.
        checks, ctx, _ = self.run_photocraft(answers=answers, accepted=True)
        self.assertEqual(by_name(checks)["Image name"].status, cp.PASS)
        self.assertTrue(ctx.notes)

    def test_invalid_names(self):
        for bad in ("PhotoCraft", "docker-photocraft", "photo_craft", "-photo"):
            body = fixture("photocraft.md").replace("\nphotocraft\n", f"\n{bad}\n", 1)
            checks, _, _ = self.run_photocraft(body=body)
            self.assertEqual(by_name(checks)["Image name"].status, cp.FAIL, bad)

    def test_personal_account_fails(self):
        answers = copy.deepcopy(PHOTOCRAFT_API)
        answers["repos/storytold/photocraft"]["owner"]["type"] = "User"
        checks, _, _ = self.run_photocraft(answers=answers)
        self.assertEqual(by_name(checks)["Upstream owned by an organisation"].status, cp.FAIL)
        self.assertEqual(cp.decide(checks), cp.LABEL_NEEDS)

    def test_missing_upstream_skips_what_depends_on_it(self):
        answers = {k: v for k, v in PHOTOCRAFT_API.items() if not k.startswith("repos/storytold")}
        checks, _, _ = self.run_photocraft(answers=answers)
        named = by_name(checks)
        self.assertEqual(named["Upstream owned by an organisation"].status, cp.FAIL)
        self.assertEqual(named["Licence"].status, cp.SKIP)
        self.assertEqual(named["Stable release"].status, cp.SKIP)
        self.assertEqual(named["Release assets"].status, cp.SKIP)

    def test_other_host_needs_a_member(self):
        body = fixture("photocraft.md").replace("https://github.com/storytold/photocraft\n",
                                                "https://codeberg.org/storytold/photocraft\n", 1)
        checks, _, _ = self.run_photocraft(body=body)
        named = by_name(checks)
        self.assertEqual(named["Upstream owned by an organisation"].status, cp.MANUAL)
        self.assertEqual(named["Licence"].status, cp.MANUAL)
        self.assertEqual(named["Release assets"].status, cp.MANUAL)
        self.assertEqual(cp.decide(checks), cp.LABEL_MEETS)

    def test_licence(self):
        answers = copy.deepcopy(PHOTOCRAFT_API)
        answers["repos/storytold/photocraft"]["license"] = None
        checks, _, _ = self.run_photocraft(answers=answers)
        self.assertEqual(by_name(checks)["Licence"].status, cp.FAIL)

        answers["repos/storytold/photocraft"]["license"] = {"spdx_id": "MIT"}
        checks, _, _ = self.run_photocraft(answers=answers)
        self.assertEqual(by_name(checks)["Licence"].status, cp.WARN)
        self.assertEqual(cp.decide(checks), cp.LABEL_MEETS)

        answers["repos/storytold/photocraft"]["license"] = {"spdx_id": "NOASSERTION"}
        checks, _, _ = self.run_photocraft(answers=answers)
        self.assertEqual(by_name(checks)["Licence"].status, cp.WARN)

    def test_no_stable_release(self):
        answers = {k: v for k, v in PHOTOCRAFT_API.items() if not k.endswith("releases/latest")}
        checks, _, _ = self.run_photocraft(answers=answers)
        named = by_name(checks)
        self.assertEqual(named["Stable release"].status, cp.FAIL)
        self.assertEqual(named["Release assets"].status, cp.SKIP)

    def test_release_link_names_another_tag(self):
        body = fixture("photocraft.md").replace("tag/v0.19.0", "tag/v0.18.0")
        checks, _, _ = self.run_photocraft(body=body)
        self.assertEqual(by_name(checks)["Stable release"].status, cp.WARN)

    def test_assets(self):
        def asset_checks(amd64, arm64=""):
            body = (fixture("photocraft.md")
                    .replace("photocraft-.*-linux-x86_64.deb", amd64 or "_No response_")
                    .replace("photocraft-.*-linux-aarch64.deb", arm64 or "_No response_"))
            return by_name(self.run_photocraft(body=body)[0])

        named = asset_checks("")
        self.assertEqual(named["amd64 asset"].status, cp.FAIL)
        self.assertEqual(named["arm64 asset"].status, cp.PASS)  # amd64 only is allowed
        self.assertEqual(asset_checks("photocraft-.*-linux-riscv64.deb")["amd64 asset"].status, cp.FAIL)
        self.assertEqual(asset_checks("photocraft-[")["amd64 asset"].status, cp.FAIL)
        self.assertEqual(asset_checks("photocraft-.*-linux-x86_64.*")["amd64 asset"].status, cp.WARN)
        literal = asset_checks("photocraft-0.19.0-linux-x86_64.deb")["amd64 asset"]
        self.assertEqual(literal.status, cp.WARN)
        self.assertIn("photocraft-.*-linux-x86_64.deb", literal.detail)

    def test_source_release_needs_no_assets(self):
        body = (fixture("photocraft.md").replace("\nRelease asset\n", "\nSource release\n")
                .replace("photocraft-.*-linux-x86_64.deb", "_No response_"))
        named = by_name(self.run_photocraft(body=body)[0])
        self.assertEqual(named["Release assets"].status, cp.PASS)
        self.assertNotIn("amd64 asset", named)

    def test_web_application_needs_a_port(self):
        body = fixture("photocraft.md").replace(cp.APP_DESKTOP, cp.APP_WEB)
        self.assertEqual(by_name(self.run_photocraft(body=body)[0])["Web interface"].status, cp.FAIL)
        with_port = body.replace("### Web interface port\n\n_No response_", "### Web interface port\n\n8080")
        self.assertEqual(by_name(self.run_photocraft(body=with_port)[0])["Web interface"].status, cp.PASS)
        bad_port = body.replace("### Web interface port\n\n_No response_", "### Web interface port\n\n99999")
        self.assertEqual(by_name(self.run_photocraft(body=bad_port)[0])["Web interface"].status, cp.FAIL)

    def test_maintainers(self):
        body = fixture("photocraft.md").replace("\nthelamer\n", "\nthelamer, nobody-here, bad_login\n")
        check = by_name(self.run_photocraft(body=body)[0])["Maintainers"]
        self.assertEqual(check.status, cp.FAIL)
        self.assertIn("nobody-here", check.detail)
        self.assertIn("bad_login", check.detail)

    def test_webapp_fixture_collects_every_problem(self):
        answers = {
            "repos/alice/notekeeper": {
                "full_name": "alice/notekeeper",
                "html_url": "https://github.com/alice/notekeeper",
                "owner": {"login": "alice", "type": "User", "html_url": "https://github.com/alice"},
                "license": {"spdx_id": "GPL-3.0"},
            },
            "repos/alice/notekeeper/releases/latest": {
                "tag_name": "v2.0.0",
                "html_url": "https://github.com/alice/notekeeper/releases/tag/v2.0.0",
                "published_at": "2026-09-01T00:00:00Z",
                "assets": [],
            },
            "users/alice": {"login": "alice", "type": "User", "html_url": "https://github.com/alice"},
        }
        checks, _ = cp.run_checks(cp.parse_form(fixture("webapp.md")), FakeGitHub(answers))
        named = by_name(checks)
        self.assertEqual(named["The Labs bar"].status, cp.FAIL)
        self.assertEqual(named["Upstream owned by an organisation"].status, cp.FAIL)
        self.assertEqual(named["Licence"].status, cp.PASS)  # GPL-3.0-or-later is GitHub's GPL-3.0
        self.assertEqual(named["Release assets"].status, cp.PASS)
        self.assertEqual(named["Web interface"].status, cp.FAIL)
        self.assertEqual(named["Maintainers"].status, cp.FAIL)
        self.assertEqual(cp.decide(checks), cp.LABEL_NEEDS)

    def test_api_errors_fail_soft(self):
        answers = dict(PHOTOCRAFT_API)
        answers["repos/storytold/photocraft"] = cp.ApiError("GET repos/storytold/photocraft: HTTP 502")
        answers["users/thelamer"] = cp.ApiError("GET users/thelamer: timed out")
        checks, ctx, _ = self.run_photocraft(answers=answers)
        named = by_name(checks)
        self.assertEqual(named["Upstream owned by an organisation"].status, cp.ERROR)
        self.assertEqual(named["Licence"].status, cp.ERROR)
        self.assertEqual(named["Maintainers"].status, cp.ERROR)
        self.assertEqual(cp.decide(checks), cp.LABEL_NEEDS)
        self.assertIn("could not run", cp.render(checks, ctx, cp.LABEL_NEEDS))


class RenderTest(unittest.TestCase):
    def test_comment_carries_the_marker_and_no_mentions(self):
        body = fixture("photocraft.md").replace("\nthelamer\n", "\n@thelamer\n")
        form = cp.parse_form(body)
        checks, ctx = cp.run_checks(form, FakeGitHub(PHOTOCRAFT_API))
        comment = cp.render(checks, ctx, cp.decide(checks), "https://example.invalid/run")
        self.assertTrue(comment.startswith(cp.MARKER))
        self.assertIn("Meets the bar", comment)
        self.assertNotIn("@thelamer", comment)
        self.assertIn("[thelamer](https://github.com/thelamer)", comment)

    def test_untrusted_text_cannot_break_the_table(self):
        self.assertEqual(cp.code("a|b`c\nd"), "`a/b'c d`")
        self.assertTrue(cp.code("x" * 200).endswith("…`"))

    def test_decide(self):
        self.assertEqual(cp.decide([cp.Check("a", cp.PASS, ""), cp.Check("b", cp.WARN, "")]), cp.LABEL_MEETS)
        self.assertEqual(cp.decide([cp.Check("a", cp.MANUAL, ""), cp.Check("b", cp.SKIP, "")]), cp.LABEL_MEETS)
        self.assertEqual(cp.decide([cp.Check("a", cp.PASS, ""), cp.Check("b", cp.FAIL, "")]), cp.LABEL_NEEDS)
        self.assertEqual(cp.decide([cp.Check("a", cp.ERROR, "")]), cp.LABEL_NEEDS)


if __name__ == "__main__":
    unittest.main()
