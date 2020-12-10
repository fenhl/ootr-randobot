# RSLbot

This is the [Random Settings League](https://rsl-leaderboard.web.app/) version of [ootr-randobot](https://github.com/deains/ootr-randobot), a [racetime.gg](https://racetime.gg) chat bot application for automatically generating [OoT Randomizer](https://ootrandomizer.com/) seeds in race rooms.

## How to get started

**Note:** This bot is intended for private use only. The code is provided here
for example purposes and transparency, however you may only use the APIs this
bot connects to if you are a trusted enough to be given the keys. It is not
possible to use this bot without suitable API access.

### Requirements

* systemd
* Python 3.7 or greater
* A copy of [Roman's branch](https://github.com/Roman971/OoT-Randomizer) of the OoT Randomizer. By default, the bot expects this to be in `/usr/local/share/fenhl/OoT-Randomizer`, you can use the `--rando_path` option to change this.
    * [the RSL script](https://github.com/matthewkirby/plando-random-settings) at the subdirectory `plando-random-settings`.
    * A ROM of *The Legend of Zelda: Ocarina of Time* (version 1.0, NTSC) at the subpath `oot-ntscu-1.0.z64`.
    * An empty subdirectory `rsl-outputs`, where spoiler logs for ongoing races will be stored.
* A web server that can serve the patch files and spoiler logs from the output directory (which is `/var/www/ootr.fenhl.net/seed` by default and can be changed using `--output_path`).

### Installation

1. Clone the repo
2. If necessary, adjust the paths, username, and/or client ID in the systemd service file `assets/rslbot.service`
3. Save the client secret for the bot as a text file at `assets/client-secret.txt`
4. Enable the service using `sudo systemctl enable --now /path/to/rslbot/assets/rslbot.service`
