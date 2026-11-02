# Propose a LinuxServer Labs image

[LinuxServer Labs](https://linuxserverlabs-playground.github.io/site/) publishes container images of open-source,
self-hosted applications that are new or not yet well known. The images are built, tested and released on the same
pipeline as LinuxServer.io's, follow the same container design, and live at
`ghcr.io/linuxserverlabs-playground/<name>`. An image that does well in Labs can later move to LinuxServer.io.

Anyone can propose an application, including its own developers. This repository is where proposals are filed.

**[Open a proposal](https://github.com/linuxserverlabs-playground/proposals/issues/new?template=proposal.yml)**

## What qualifies

The bar for the software is low, but it is firm. An application qualifies when:

- **its source repository belongs to an organisation,** on GitHub (preferred) or another git host, never a personal
  account. Creating an organisation is free, and moving the repository to one keeps its history, issues and stars;
- **it has an approved open-source licence,** detected by GitHub from a `LICENSE` file;
- **it cuts stable, versioned releases.** The image follows the latest stable release, so pre-releases, tags without a
  release, or "build the main branch" are not enough. A release with ready-built files attached is best; building
  from the release's source code is fine when the project needs it;
- **release asset names stay the same** from one release to the next, apart from the version, so the image can find
  them automatically;
- **it is self-hosted and works with just its own container:** a web interface on a port, or a desktop program that
  Labs runs in the browser on its [Selkies](https://github.com/selkies-project/selkies) base. It needs an embedded
  database (SQLite or similar), no database, or a server database the image can run itself. No companion containers;
- **it does not depend on a paid vendor service.** An application whose main purpose is a vendor's hosted service is
  out. One that calls a hosted API is fine when the endpoint is configurable, for example an AI app that also works
  against Ollama or any OpenAI-compatible server;
- **any telemetry is disclosed and can be turned off;**
- **the name is free** in Labs and in LinuxServer.io, so the image never has to be renamed.

amd64 only is allowed; arm64 is welcome when the project ships it.

## How to propose

Fill in the [proposal form](https://github.com/linuxserverlabs-playground/proposals/issues/new?template=proposal.yml).
It asks for what a team member needs to build and try the image without having to ask you anything: the upstream
repository and its latest release, which release file the image downloads for each architecture, how the application
is installed and started, its port, where it writes its data, and what a first start looks like.

Two answers trip people up most often:

- **Release assets** can be written as the exact file name in the latest release or as a regular expression. Use `.*`
  where the version appears (`myapp-.*-linux-amd64.tar.gz`), so the same answer matches the next release too.
- **Maintainers** are GitHub logins, comma separated, without `@`. They are credited on the image's page and
  mentioned when the image needs attention. It is credit, not access: changes to a Labs image always go through
  pull requests.

If you work with an AI coding agent, the [LinuxServer harness](https://github.com/linuxserver-playground/harness)
can walk you through making your application easy to self-host and can fill in the form for you.

## What happens next

1. **The automatic check.** As soon as you submit, a check reads the form and compares it with the real upstream:
   who owns the repository, the licence GitHub detects, the latest stable release and its asset names, whether the
   name is taken, and that the maintainers exist. It posts one comment listing what passed and what is missing, and
   labels the issue `meets-bar` or `needs-info`. Edit the issue to fix anything it flags; the check runs again on
   every edit and updates the same comment. Upstreams that are not on GitHub get what the check can see, and a team
   member checks the rest by hand.
2. **A trial.** A team member builds the image from your answers and runs it in a throwaway sandbox, following your
   first-run steps. This is where an application that is not really self-hosted, or needs more than its own
   container, shows. Questions come back to you as comments on the issue.
3. **A pending repository.** When the trial works, the member creates `docker-<name>` in the Labs organisation and
   opens its first pull request from your proposal, crediting you. The issue is labelled `accepted`, and the Labs
   site lists the image as pending approval.
4. **Review.** That pull request builds the image, tests it, and posts a report with screenshots, the test results
   and a pull command, so the team can review it and anyone can try it. You can follow along, comment, or improve
   the image with pull requests from a fork into that branch.
5. **Published.** A team member merges the pull request. The image is released to
   `ghcr.io/linuxserverlabs-playground/<name>`, gets its page on the Labs site, and is rebuilt automatically for
   every new upstream release from then on.

Not every proposal goes ahead. When one does not meet the bar, the comment or a team member says why, and the issue
is closed; you are welcome to propose again once the reason is gone.

## Staying in Labs

The bar to get in is low, so an image that stops working leaves again. When releases stop building (a renamed or
missing asset, a broken build) or upstream stops releasing, Labs opens an issue on the image's repository and
mentions its maintainers. An image that stays broken is archived; its page stays up with an archived notice.

## Labels

| Label | Meaning |
| --- | --- |
| `proposal` | Added by the form. Only issues with it are checked. |
| `meets-bar` | The automatic check found nothing missing. Waiting for a team member to try it. |
| `needs-info` | The automatic check found something missing, or could not run. Its comment says what. |
| `accepted` | A team member created the image repository and opened its first pull request. |

## Questions

Ask on [Discord](https://linuxserver.io/discord), or read the
[LinuxServer.io documentation](https://linuxserver-playground.github.io/site/docs/) to see how the images work.
