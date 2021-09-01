import sys

import asyncio
import contextlib
import datetime
import json
import pathlib
import re
import subprocess

import lazyjson # https://github.com/fenhl/lazyjson

from racetime_bot import RaceHandler, monitor_cmd, can_moderate, can_monitor

DATA = lazyjson.File('/usr/local/share/fenhl/ootr-web.json')
GEN_LOCK = asyncio.Lock()

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

def parse_duration(args):
    if len(args) == 0:
        raise ValueError('Empty duration args')
    duration = datetime.timedelta()
    for arg in args:
        arg = arg.lower()
        while len(arg) > 0:
            match = re.match('([0-9]+)([smh]?)', arg)
            if not match:
                raise ValueError('Unknown duration format')
            duration += datetime.timedelta(**{
                {
                    '': 'minutes',
                    's': 'seconds',
                    'm': 'minutes',
                    'h': 'hours'
                }[match.group(2)]: float(match.group(1))
            })
            arg = arg[len(match.group(0)):]
    return duration

class RandoHandler(RaceHandler):
    """
    RandoBot race handler. Generates seeds, presets, and frustration.
    """
    stop_at = ['cancelled', 'finished']

    def __init__(self, rsl_script_path, output_path, base_uri, **kwargs):
        super().__init__(**kwargs)

        self.rsl_script_path = pathlib.Path(rsl_script_path)
        self.output_path = output_path
        self.base_uri = base_uri
        self.presets = {
            'league': 'Random Settings League (default)',
            'beginner': 'Random Settings for beginners',
            'intermediate': 'a step between Beginner and League',
            'ddr': 'Random Settings DDR',
            'coop': 'Random Settings Co-Op',
            'multiworld': 'Random Settings Multiworld',
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
                self.state['spoiler_log'] = section[len(f'Seed: {self.base_uri}'):].split('.zpf')[0] + '_Spoiler.json'
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
                'I will post the spoiler log after the race.'
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
                duration = parse_duration(args[:sep_idx])
                interval = parse_duration(args[sep_idx + 1:])
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

        await self.send_message('Rolling seed…') #TODO also announce position in queue (#5)
        async with GEN_LOCK:
            await self.roll(preset, world_count, reply_to)

    async def race_data(self, data):
        await super().race_data(data)
        if self.data.get('started_at') is not None:
            if not self.state.get('break_notifications_started') and self.state.get('breaks') is not None:
                self.state['break_notifications_started'] = True
                asyncio.create_task(self.break_notifications(), name=f'break notifications for {self.data.get("name")}')
            with contextlib.suppress(Exception):
                DATA['races'][self.state['file_stem']]['startTime'] = self.data['started_at']
            if self.data.get('status', {}).get('value') in ('finished', 'cancelled'):
                await self.send_spoiler()
        elif self.data.get('status', {}).get('value') == 'finished':
            await self.send_spoiler()
        elif self.data.get('status', {}).get('value') == 'cancelled':
            with contextlib.suppress(Exception):
                (pathlib.Path(self.output_path) / f'{self.state["file_stem"]}.zpf').unlink(missing_ok=True)
                (pathlib.Path(self.output_path) / f'{self.state["file_stem"]}.zpfz').unlink(missing_ok=True)
                (pathlib.Path(self.output_path) / f'{self.state["file_stem"]}_Spoiler.json').unlink(missing_ok=True)
                del DATA['races'][self.state['file_stem']]

    async def roll(self, preset, world_count, reply_to):
        """
        Generate a seed and send it to the race room.
        """
        args = [sys.executable, 'RandomSettingsGenerator.py']
        if preset != 'league':
            args.append(f'--override={preset}_override.json')

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

        patch_files = list((self.rsl_script_path / 'patches').glob('*.zpf')) #TODO parse filename from output
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
        self.state['spoiler_log'] = file_stem + '_Spoiler.json'

        await self.send_message(
            '%(reply_to)s, here is your seed: %(seed_uri)s'
            % {'reply_to': reply_to or 'Okay', 'seed_uri': seed_uri}
        )
        if preset == 'league':
            new_raceinfo = f'Random Settings League | Seed: {seed_uri}'
            overwrite = True
        else:
            new_raceinfo = f'{self.presets[preset]} | Seed: {seed_uri}'
            overwrite = False
        await self.set_raceinfo(new_raceinfo, overwrite, prefix=False)

        with contextlib.suppress(Exception):
            DATA['races'][file_stem] = {'roomSlug': self.data['slug']}
        with contextlib.suppress(Exception):
            with (self.rsl_script_path / 'patches' / self.state['spoiler_log']).open() as f:
                await self.send_message(
                    'The hash is %(file_hash)s.'
                    % {'file_hash': ', '.join(json.load(f)['file_hash'])}
                )

        self.state['seed_rolled'] = True

    async def send_presets(self):
        """
        Send a list of known presets to the race room.
        """
        await self.send_message('Available presets:')
        for name, full_name in self.presets.items():
            await self.send_message(f'{name} – {full_name}')

    async def send_spoiler(self):
        if 'spoiler_log' in self.state and not self.state.get('spoiler_sent', False):
            (self.rsl_script_path / 'patches' / self.state['spoiler_log']).rename(pathlib.Path(self.output_path) / self.state['spoiler_log'])
            spoiler_uri = self.base_uri + self.state['spoiler_log']
            await self.send_message(f'Here is the spoiler log: {spoiler_uri}')
            self.state['spoiler_sent'] = True
            await self.set_raceinfo(f'Spoiler log: {spoiler_uri}', prefix=False)

    def _race_in_progress(self):
        return self.data.get('status').get('value') in ('pending', 'in_progress')
