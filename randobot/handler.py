import sys

import asyncio
import contextlib
import datetime
import json
import pathlib
import re
import shlex
import subprocess
import time

import aiohttp # PyPI: aiohttp

import lazyjson # https://github.com/fenhl/lazyjson

from racetime_bot import RaceHandler, monitor_cmd, can_moderate, can_monitor

class Session:
    RATE_LIMIT_INTERVAL = 5 # assume all requests have a rate limit of 5 seconds since the rate limit for the version endpoint is affecting subsequent requests to other endpoints

    def __init__(self):
        self.inner = aiohttp.ClientSession(headers={'User-Agent': 'rslbot/2.0.2'}, raise_for_status=True)
        self.last_request = time.monotonic() # assume we just made a request to avoid rate limits after bot restarts

    async def request(self, method, *args, **kwargs):
        now = time.monotonic()
        if now < self.last_request:
            await asyncio.sleep(now - self.last_request)
        resp = self.inner.request(method, *args, **kwargs)
        self.last_request = time.monotonic()
        return resp

    async def get(self, *args, **kwargs):
        return await self.request('GET', *args, **kwargs)

    async def post(self, *args, **kwargs):
        return await self.request('POST', *args, **kwargs)

DATA = lazyjson.File('/usr/local/share/fenhl/ootr-web.json') # database for the seed archive (https://ootr.fenhl.net/seed)
GEN_LOCK = asyncio.Lock()
SESSION = Session()

HASH_EMOJI = {
    'Beans': 'HashBeans',
    'Big Magic': 'HashBigMagic',
    'Bombchu': 'HashBombchu',
    'Boomerang': 'HashBoomerang',
    'Boss Key': 'HashBossKey',
    'Bottled Fish': 'HashBottledFish',
    'Bottled Milk': 'HashBottledMilk',
    'Bow': 'HashBow',
    'Compass': 'HashCompass',
    'Cucco': 'HashCucco',
    'Deku Nut': 'HashDekuNut',
    'Deku Stick': 'HashDekuStick',
    'Fairy Ocarina': 'HashFairyOcarina',
    'Frog': 'HashFrog',
    'Gold Scale': 'HashGoldScale',
    'Heart Container': 'HashHeart',
    'Hover Boots': 'HashHoverBoots',
    'Kokiri Tunic': 'HashKokiriTunic',
    'Lens of Truth': 'HashLensOfTruth',
    'Longshot': 'HashLongshot',
    'Map': 'HashMap',
    'Mask of Truth': 'HashMaskOfTruth',
    'Master Sword': 'HashMasterSword',
    'Megaton Hammer': 'HashHammer',
    'Mirror Shield': 'HashMirrorShield',
    'Mushroom': 'HashMushroom',
    'Saw': 'HashSaw',
    'Silver Gauntlets': 'HashSilvers',
    'Skull Token': 'HashSkullToken',
    'Slingshot': 'HashSlingshot',
    'SOLD OUT': 'HashSoldOut',
    'Stone of Agony': 'HashStoneOfAgony',
}

def natjoin(sequence, default):
    if len(sequence) == 0:
        return str(default)
    elif len(sequence) == 1:
        return str(sequence[0])
    elif len(sequence) == 2:
        return f'{sequence[0]} and {sequence[1]}'
    else:
        return ', '.join(sequence[:-1]) + f', and {sequence[-1]}'

def format_duration(duration):
    parts = []
    hours, duration = divmod(duration, datetime.timedelta(hours=1))
    if hours > 0:
        parts.append(f'{hours} hour{"" if hours == 1 else "s"}')
    minutes, duration = divmod(duration, datetime.timedelta(minutes=1))
    if minutes > 0:
        parts.append(f'{minutes} minute{"" if minutes == 1 else "s"}')
    if duration > datetime.timedelta():
        seconds = duration.total_seconds()
        parts.append(f'{seconds} second{"" if seconds == 1 else "s"}')
    return natjoin(parts, '0 seconds')

def format_breaks(duration, interval):
    return f'{format_duration(duration)} every {format_duration(interval)}'

def parse_duration(args, default):
    if len(args) == 0:
        raise ValueError('Empty duration args')
    duration = datetime.timedelta()
    for arg in args:
        arg = arg.lower()
        while len(arg) > 0:
            match = re.match('([0-9]+)([smh:]?)', arg)
            if not match:
                raise ValueError('Unknown duration format')
            unit = {
                '': default,
                's': 'seconds',
                'm': 'minutes',
                'h': 'hours',
                ':': 'default'
            }[match.group(2)]
            default = {
                'hours': 'minutes',
                'minutes': 'seconds',
                'seconds': 'seconds'
            }[unit]
            duration += datetime.timedelta(**{unit: float(match.group(1))})
            arg = arg[len(match.group(0)):]
    return duration

class RandoHandler(RaceHandler):
    """
    RandoBot race handler. Generates seeds, presets, and frustration.
    """
    stop_at = ['cancelled', 'finished']
    max_status_checks = 10

    def __init__(self, ootr_api_key, rsl_script_path, output_path, base_uri, warning_command, **kwargs):
        super().__init__(**kwargs)

        self.ootr_api_key = ootr_api_key
        self.rsl_script_path = pathlib.Path(rsl_script_path)
        self.output_path = output_path
        self.base_uri = base_uri
        self.warning_command = warning_command
        self.presets = {
            'league': {
                'info': 'Random Settings League',
                'help': 'Random Settings League (default)'
            },
            'beginner': {
                'info': 'Random Settings for beginners',
                'help': 'random settings for beginners, see https://ootr.fenhl.net/static/rsl-beginner-weights.html for details'
            },
            'intermediate': {
                'info': 'Intermediate Random settings',
                'help': 'a step between Beginner and League'
            },
            'ddr': {
                'info': 'Random Settings DDR',
                'help': 'League but always normal damage and with cutscenes useful for tricks in the DDR ruleset'
            },
            'coop': {
                'info': 'Random Settings Co-Op',
                'help': 'random settings Co-Op'
            },
            'multiworld': {
                'info': 'Random Settings Multiworld',
                'help': 'roll with !seed multiworld <worldcount>'
            }
        }
        self.preset_aliases = {
            'rsl': 'league',
            'solo': 'league',
            'co-op': 'coop',
            'mw': 'multiworld',
        }
        self.seed_rolled = False

    def should_stop(self):
        return (
            self.data.get('goal', {}).get('name') != 'Random settings league'
            or self.data.get('goal', {}).get('custom', False)
            or super().should_stop()
        )

    async def begin(self):
        """
        Send introduction messages.
        """
        if self.should_stop():
            return
        asyncio.create_task(self.heartbeat(), name=f'heartbeat for {self.data.get("name")}')
        for section in self.data.get('info', '').split(' | '):
            if section.startswith(f'Seed: {self.base_uri}'):
                self.state['spoiler_log_path'] = section[len(f'Seed: {self.base_uri}'):].split('.zpf')[0] + '_Spoiler.json'
                with (self.rsl_script_path / 'patches' / self.state['spoiler_log_path']).open() as f:
                    self.state['file_hash'] = json.load(f)['file_hash']
                self.state['intro_sent'] = True
                break
            elif section.startswith('Seed: https://ootrandomizer.com/seed/get?id='):
                self.state['seed_id'] = section[len('Seed: https://ootrandomizer.com/seed/get?id='):]
                self.state['intro_sent'] = True
                break
        if not self.state.get('intro_sent') and not self._race_in_progress():
            await self.send_message(
                'Welcome to the OoTR Random Settings League! Create a seed with !seed <preset>'
            )
            await self.send_message(
                'If no preset is selected, default RSL settings will be used. For a list of presets, use !presets'
            )
            await self.send_message(
                'The spoiler log will be available on the seed page after the race.'
            )
            self.state['intro_sent'] = True
        if 'locked' not in self.state:
            self.state['locked'] = False
        if 'fpa' not in self.state:
            self.state['fpa'] = False
        if 'breaks' not in self.state:
            self.state['breaks'] = None

    async def heartbeat(self):
        while not self.should_stop():
            await asyncio.sleep(20)
            await self.ws.send(json.dumps({'action': 'ping'}))

    async def break_notifications(self):
        duration, interval = self.state['breaks']
        await asyncio.sleep((interval - datetime.timedelta(minutes=5)).total_seconds())
        while not self.should_stop():
            asyncio.create_task(self.send_message('@entrants Reminder: Next break in 5 minutes.'))
            await asyncio.sleep(datetime.timedelta(minutes=5).total_seconds())
            if self.should_stop():
                break
            asyncio.create_task(self.send_message(f'@entrants Break time! Please pause for {format_duration(duration)}.'))
            await asyncio.sleep(duration.total_seconds())
            if self.should_stop():
                break
            asyncio.create_task(self.send_message('@entrants Break ended. You may resume playing.'))
            await asyncio.sleep((interval - duration - datetime.timedelta(minutes=5)).total_seconds())

    @monitor_cmd
    async def ex_lock(self, args, message):
        """
        Handle !lock commands.

        Prevent seed rolling unless user is a race monitor.
        """
        self.state['locked'] = True
        await self.send_message(
            'Lock initiated. I will now only roll seeds for race monitors.'
        )

    @monitor_cmd
    async def ex_unlock(self, args, message):
        """
        Handle !unlock commands.

        Remove lock preventing seed rolling unless user is a race monitor.
        """
        if self._race_in_progress():
            return
        self.state['locked'] = False
        await self.send_message(
            'Lock released. Anyone may now roll a seed.'
        )

    async def ex_seed(self, args, message):
        """
        Handle !seed commands.
        """
        if self._race_in_progress():
            return
        await self.roll_and_send(args, message)

    async def ex_presets(self, args, message):
        """
        Handle !presets commands.
        """
        if self._race_in_progress():
            return
        await self.send_presets()

    async def ex_fpa(self, args, message):
        if len(args) == 1 and args[0] in ('on', 'off'):
            if not can_monitor(message):
                resp = 'Sorry %(reply_to)s, only race monitors can do that.'
            elif args[0] == 'on':
                if self.state['fpa']:
                    resp = 'Fair play agreement is already activated.'
                else:
                    self.state['fpa'] = True
                    resp = (
                        'Fair play agreement is now active. @entrants may '
                        'use the !fpa command during the race to notify of a '
                        'crash. Race monitors should enable notifications '
                        'using the bell 🔔 icon below chat.'
                    )
            else:  # args[0] == 'off'
                if not self.state['fpa']:
                    resp = 'Fair play agreement is not active.'
                else:
                    self.state['fpa'] = False
                    resp = 'Fair play agreement is now deactivated.'
        elif self.state['fpa']:
            if self._race_in_progress():
                resp = '@everyone FPA has been invoked by @%(reply_to)s.'
            else:
                resp = 'FPA cannot be invoked before the race starts.'
        else:
            resp = (
                'Fair play agreement is not active. Race monitors may enable '
                'FPA for this race with !fpa on'
            )
        if resp:
            reply_to = message.get('user', {}).get('name', 'friend')
            await self.send_message(resp % {'reply_to': reply_to})

    async def ex_breaks(self, args, message):
        if self._race_in_progress():
            return
        if len(args) == 0:
            if self.state['breaks'] is None:
                await self.send_message('Breaks are currently disabled. Example command to enable: !breaks 5m every 2h30')
            else:
                await self.send_message(f'Breaks are currently set to {format_breaks(*self.state["breaks"])}. Disable with !breaks off')
        elif len(args) == 1 and args[0] == 'off':
            self.state['breaks'] = None
            await self.send_message('Breaks are now disabled.')
        else:
            reply_to = message.get('user', {}).get('name')
            try:
                sep_idx = args.index('every')
                duration = parse_duration(args[:sep_idx], default='minutes')
                interval = parse_duration(args[sep_idx + 1:], default='hours')
            except ValueError:
                await self.send_message(f'Sorry {reply_to or "friend"}, I don\'t recognise that format for breaks. Example commands: !breaks 5m every 2h30, !breaks off')
            else:
                if duration < datetime.timedelta(minutes=1):
                    await self.send_message(f'Sorry {reply_to or "friend"}, minimum break time (if enabled at all) is 1 minute. You can disable breaks entirely with !breaks off')
                elif interval < duration + datetime.timedelta(minutes=5):
                    await self.send_message(f'Sorry {reply_to or "friend"}, there must be a minimum of 5 minutes between breaks since I notify runners 5 minutes in advance.')
                elif duration + interval >= datetime.timedelta(hours=24):
                    await self.send_message(f'Sorry {reply_to or "friend"}, race rooms are automatically closed after 24 hours so these breaks wouldn\'t work.')
                else:
                    self.state['breaks'] = duration, interval
                    await self.send_message(f'Breaks set to {format_breaks(duration, interval)}.')

    async def roll_and_send(self, args, message):
        """
        Read an incoming !seed command, and generate a new seed if valid.
        """
        reply_to = message.get('user', {}).get('name')

        if self.state.get('locked') and not can_monitor(message):
            await self.send_message(
                'Sorry %(reply_to)s, seed rolling is locked. Only race '
                'monitors may roll a seed for this race.'
                % {'reply_to': reply_to or 'friend'}
            )
            return
        if self.state.get('seed_rolled') and not can_moderate(message):
            await self.send_message(
                'Well excuuuuuse me princess, but I already rolled a seed. '
                'Don\'t get greedy!'
            )
            return

        if len(args) >= 1:
            preset = self.preset_aliases.get(args[0].lower(), args[0].lower())
            if preset not in self.presets:
                await self.send_message(
                    'Sorry %(reply_to)s, I don\'t recognise that preset. Use '
                    '!presets to see what is available.'
                    % {'reply_to': reply_to or 'friend'}
                )
                return
        else:
            preset = 'league'
        if preset == 'multiworld':
            if len(args) == 2:
                try:
                    world_count = int(args[1])
                except ValueError:
                    await self.send_message('World count must be a number')
                    return
                if world_count < 2:
                    await self.send_message('World count must be at least 2')
                    return
                if world_count > 15:
                    await self.send_message('Sorry, I can only roll seeds with up to 15 worlds. Please download the RSL script from https://github.com/matthewkirby/plando-random-settings to roll seeds for more than 15 players.')
                    return
            else:
                await self.send_message('Missing world count (e.g. “!seed multiworld 2” for 2 worlds)')
                return
        else:
            if len(args) > 1:
                await self.send_message('Unexpected parameter')
                return
            else:
                world_count = 1

        await self.send_message('Rolling seed…')
        async with GEN_LOCK:
            await self.roll(preset, world_count, reply_to)

    async def race_data(self, data):
        await super().race_data(data)
        if self.data.get('started_at') is not None:
            if not self.state.get('break_notifications_started') and self.state.get('breaks') is not None:
                self.state['break_notifications_started'] = True
                asyncio.create_task(self.break_notifications(), name=f'break notifications for {self.data.get("name")}')
            with contextlib.suppress(Exception):
                DATA['races'][self.data['slug']]['startTime'] = self.data['started_at']
            if self.data.get('status', {}).get('value') in ('finished', 'cancelled'):
                await self.send_spoiler()
        elif self.data.get('status', {}).get('value') == 'finished':
            await self.send_spoiler()
        elif self.data.get('status', {}).get('value') == 'cancelled':
            with contextlib.suppress(Exception):
                del DATA['races'][self.data['slug']]
                (pathlib.Path(self.output_path) / f'{self.state["file_stem"]}.zpf').unlink(missing_ok=True)
                (pathlib.Path(self.output_path) / f'{self.state["file_stem"]}.zpfz').unlink(missing_ok=True)
                (pathlib.Path(self.output_path) / f'{self.state["file_stem"]}_Spoiler.json').unlink(missing_ok=True)

    async def roll(self, preset, world_count, reply_to):
        """
        Generate a seed and send it to the race room.
        """
        generate_locally = world_count != 1 # the ootrandomizer.com API currently does not support generating multiworld seeds

        # update the RSL script
        process = await asyncio.create_subprocess_exec('git', 'pull', cwd=self.rsl_script_path)
        exit_code = await process.wait()
        if exit_code != 0:
            await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (Failed to update the RSL script, please notify Fenhl)')
            return

        # check if randomizer version is available on web
        if not generate_locally:
            resp = await SESSION.get('https://ootrandomizer.com/api/version?branch=devRSL', params={'key': self.ootr_api_key})
            try:
                latest_web_version = (await resp.json())['currentlyActiveVersion']
            except aiohttp.ContentTypeError:
                # this API endpoint is currently returning HTML instead of the expected JSON, fallback to generating locally when that happens
                generate_locally = True
            else:
                with (self.rsl_script_path / 'version.py').open() as local_version_f:
                    for line in local_version_f:
                        if line.startswith('randomizer_version ='):
                            rando_version = line.split("'")[1]
                            base_version = rando_version.split(' ')[0]
                            break
                    else:
                        await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (Failed to check the randomizer version, please notify Fenhl)')
                        return
                if base_version != latest_web_version: # there is no endpoint for checking whether a given version is available on the website, so for now we assume that if the required version isn't the current one, it's not available
                    await asyncio.create_subprocess_exec(*shlex.split(self.warning_command.format('webRandoVersion')))
                    generate_locally = True

        # run the RSL script
        outer_tries = 1 if generate_locally else 5 # when generating locally, retries are already handled by the RSL script
        for _ in range(outer_tries):
            args = [sys.executable, 'RandomSettingsGenerator.py']
            if preset != 'league':
                args.append(f'--override={preset}_override.json')
            if world_count != 1:
                args.append(f'--worldcount={world_count}')
            if not generate_locally:
                args.append('--no_seed')
            try:
                process = await asyncio.create_subprocess_exec(*args, cwd=self.rsl_script_path)
                exit_code = await process.wait()
                if exit_code == 0:
                    pass
                elif exit_code == 1:
                    await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (RSL script crashed, please notify Fenhl)')
                    return
                elif exit_code == 2:
                    await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (Max retries exceeded, please try again or notify Fenhl)')
                    return
                else:
                    await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (Error code {exit_code}, please notify Fenhl)')
                    return
            except subprocess.CalledProcessError:
                await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (RSL script missing, please notify Fenhl)')
                return

            # roll the seed (if compatible with web) or copy it to www-data (if rolled locally)
            if generate_locally:
                patch_files = list((self.rsl_script_path / 'patches').glob('*.zpf' if world_count == 1 else '*.zpfz')) #TODO parse filename from output
                if len(patch_files) == 0:
                    await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (Patch file not found, please notify Fenhl)')
                    return
                elif len(patch_files) > 1:
                    await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (Multiple patch files found, please notify Fenhl)')
                    return
                file_name = patch_files[0].name
                file_stem = patch_files[0].stem
                self.state['file_stem'] = file_stem
                patch_files[0].rename(pathlib.Path(self.output_path) / file_name)
                for extra_output_path in [self.rsl_script_path / 'patches' / f'{file_stem}_Cosmetics.json', self.rsl_script_path / 'patches' / f'{file_stem}_Distribution.json']:
                    if extra_output_path.exists():
                        extra_output_path.unlink()
                seed_uri = self.base_uri + file_name
                self.state['spoiler_log_path'] = file_stem + '_Spoiler.json'
                with (self.rsl_script_path / 'patches' / self.state['spoiler_log_path']).open() as f:
                    self.state['file_hash'] = json.load(f)['file_hash']
                DATA['races'][self.data['slug']] = {
                    'fileStem': file_stem,
                    'weights': preset
                }
            else:
                plando_files = list((self.rsl_script_path / 'data').glob('*.json'))
                if len(plando_files) == 0:
                    await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (Plando file not found, please notify Fenhl)')
                    return
                elif len(plando_files) > 1:
                    await self.send_message(f'Sorry {reply_to or "friend"}, something went wrong while generating the seed. (Multiple plando files found, please notify Fenhl)')
                    return
                with plando_files[0].open() as distribution_f:
                    distribution = json.load(distribution_f)
                plando_files[0].unlink()
                for _ in range(3):
                    resp = await SESSION.post('https://ootrandomizer.com/api/v2/seed/create', params={'key': self.ootr_api_key, 'version': f'devRSL_{base_version}', 'locked': '1'}, json=distribution['settings'])
                    self.state['seed_id'] = str((await resp.json())['id'])
                    seed_uri = f'https://ootrandomizer.com/seed/get?id={self.state["seed_id"]}'
                    for _ in range(self.max_status_checks):
                        resp = await SESSION.get('https://ootrandomizer.com/api/v2/seed/status', params={'key': self.ootr_api_key, 'id': self.state['seed_id']}, raise_for_status=False)
                        if resp.status == 204:
                            continue
                        resp.raise_for_status()
                        seed_status = (await resp.json())['status']
                        if seed_status == 0: # still generating
                            continue
                        elif seed_status == 1: # generated success
                            resp = await SESSION.get('https://ootrandomizer.com/api/v2/seed/details', params={'key': self.ootr_api_key, 'id': self.state['seed_id']})
                            seed_details = await resp.json()
                            self.state['file_hash'] = json.loads(seed_details['spoilerLog'])['file_hash'] # spoiler log is double-JSON-encoded in API response
                            resp = await SESSION.get('https://ootrandomizer.com/api/v2/seed/patch', params={'key': self.ootr_api_key, 'id': self.state['seed_id']})
                            file_name = re.fullmatch('attachment; filename=(.+)', resp.headers['Content-Disposition']).group(1)
                            file_stem = re.fullmatch('attachment; filename=(.+)\\.zpfz?', resp.headers['Content-Disposition']).group(1)
                            self.state['file_stem'] = file_stem
                            with (self.output_path / file_name).open('w') as patch_f:
                                patch_f.write(await resp.content.read())
                            self.state['spoiler_log_path'] = file_stem + '_Spoiler.json'
                            with (self.rsl_script_path / 'patches' / self.state['spoiler_log_path']).open('w') as spoiler_f:
                                spoiler_f.write(seed_details['spoilerLog'])
                            DATA['races'][self.data['slug']] = {
                                'seedID': self.state['seed_id'],
                                'fileStem': file_stem,
                                'weights': preset
                            }
                            break
                        else: # 2 = generated with link (not possible from API), 3 = failed to generate
                            seed_uri = None
                            break
                    else:
                        seed_uri = None # max status checks exceeded
                    if seed_uri is not None:
                        break
                if seed_uri is not None:
                    break
        if seed_uri is None:
            await self.send_message(
                'Sorry, but it looks like the seed failed to generate. Use '
                '!seed to try again.'
            )
            return

        # send seed link
        await self.send_message(
            '%(reply_to)s, here is your seed: %(seed_uri)s'
            % {'reply_to': reply_to or 'Okay', 'seed_uri': seed_uri}
        )
        emoji_hash = ' '.join(HASH_EMOJI.get(item, item) for item in self.state['file_hash'])
        await self.set_bot_raceinfo(f'{self.presets[preset]["info"]}\n{emoji_hash}\n{seed_uri}')
        self.state['preset'] = preset
        self.state['seed_uri'] = seed_uri

        # prevent rolling another seed in this room
        self.state['seed_rolled'] = True

    async def send_presets(self):
        """
        Send a list of known presets to the race room.
        """
        await self.send_message('Available presets:')
        for name, data in self.presets.items():
            await self.send_message(f'{name} – {data["help"]}')

    async def send_spoiler(self):
        if not self.state.get('spoiler_sent', False):
            if 'seed_id' in self.state:
                await SESSION.post('https://ootrandomizer.com/api/v2/seed/unlock', params={'key': self.ootr_api_key, 'id': self.state['seed_id']})
                self.state['spoiler_sent'] = True
            else:
                if 'spoiler_log_path' in self.state:
                    (self.rsl_script_path / 'patches' / self.state['spoiler_log_path']).rename(pathlib.Path(self.output_path) / self.state['spoiler_log_path'])
                    spoiler_uri = self.base_uri + self.state['spoiler_log_path']
                    await self.send_message(f'Here is the spoiler log: {spoiler_uri}')
                    self.state['spoiler_sent'] = True
                    if 'preset' in self.state and 'file_hash' in self.state and 'seed_uri' in self.state:
                        emoji_hash = ' '.join(HASH_EMOJI.get(item, item) for item in self.state['file_hash'])
                        await self.set_bot_raceinfo(f'{self.presets[self.state["preset"]]["info"]}\n{emoji_hash}\n{self.state["seed_uri"]}\nSpoiler log: {spoiler_uri}')

    def _race_in_progress(self):
        return self.data.get('status').get('value') in ('pending', 'in_progress')
