### Image name

photocraft

### Upstream repository

https://github.com/storytold/photocraft

### Project homepage

https://getartcraft.com/apps/photocraft

### Licence

Apache-2.0

### Latest stable release

https://github.com/storytold/photocraft/releases/tag/v0.19.0

### How the image installs it

Release asset

### amd64 release asset

photocraft-.*-linux-x86_64.deb

### arm64 release asset

photocraft-.*-linux-aarch64.deb

### Application type

Desktop application (selkies)

### Runtime and install

Rust, a native desktop program. Each release ships a deb per architecture; install it with apt and start `photocraft`.

### Working repository

_No response_

### Web interface port

_No response_

### Where it writes state

Settings and recent files go to the user's home directory (`~/.config/photocraft`), which is /config on the selkies base.

### Required environment variables

_No response_

### Database

None needed

### First run

Open https://host:3001, and PhotoCraft's window fills the desktop with an empty canvas. Open an image from /config to edit it.

### Screenshot

_No response_

### Maintainers

thelamer

### The Labs bar

- [X] The upstream repository is owned by an organisation, not a personal account
- [X] It has an approved open-source licence
- [X] It cuts stable, versioned releases
- [X] Release asset names stay the same from release to release
- [X] It works with just this container (no companion containers)
- [X] It needs no account with a paid vendor service to work
- [X] Any telemetry is disclosed and can be turned off
