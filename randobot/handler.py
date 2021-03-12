import sys

import asyncio
import contextlib
import json
import pathlib
import subprocess

from racetime_bot import RaceHandler, monitor_cmd, can_moderate, can_monitor

GEN_LOCK = asyncio.Lock()

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
        if self.data.get('info', '').startswith(self.base_uri):
            self.state['spoiler_log'] = self.data['info'][len(self.base_uri):].split('.zpf')[0] + '_Spoiler.json'
            self.state['intro_sent'] = True
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
            if preset not in self.presets:
                await self.send_message(
                    'Sorry %(reply_to)s, I don\'t recognise that preset. Use '
                    '!presets to see what is available.'
                    % {'reply_to': reply_to or 'friend'}
                )
                return
            preset = self.preset_aliases.get(args[0], args[0])
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
        if self.data.get('status', {}).get('value') in ('finished', 'cancelled'):
            await self.send_spoiler()

    async def roll(self, preset, world_count, reply_to):
        """
        Generate a seed and send it to the race room.
        """
        args = [sys.executable, 'PlandoRandomSettings.py']
        if preset != 'league':
            args.append(f'--override={preset}_override.json')

        try:
            process = await asyncio.create_subprocess_exec(*args, cwd=self.rsl_script_path)
            if await process.wait() != 0:
                await self.send_message(
                    'Sorry %(reply_to)s, something went wrong while generating the seed. (RSL script crashed, please notify Fenhl or try again)' #TODO read output, give different error messages
                    % {'reply_to': reply_to or 'friend'}
                )
                return
        except subprocess.CalledProcessError:
            await self.send_message(
                'Sorry %(reply_to)s, something went wrong while generating the seed. (RSL script missing, please notify Fenhl)'
                % {'reply_to': reply_to or 'friend'}
            )
            return

        patch_files = list((self.rsl_script_path / 'patches').glob('*.zpf')) #TODO parse filename from output
        if len(patch_files) == 0:
            await self.send_message(
                'Sorry %(reply_to)s, something went wrong while generating the seed. (Patch file not found, please notify Fenhl)'
                % {'reply_to': reply_to or 'friend'}
            )
            return
        elif len(patch_files) > 1:
            await self.send_message(
                'Sorry %(reply_to)s, something went wrong while generating the seed. (Multiple patch files found, please notify Fenhl)'
                % {'reply_to': reply_to or 'friend'}
            )
            return
        file_name = patch_files[0].name
        file_stem = patch_files[0].stem
        patch_files[0].rename(pathlib.Path(self.output_path) / file_name)
        (self.rsl_script_path / 'patches' / f'{file_stem}_Distribution.json').unlink()
        seed_uri = self.base_uri + file_name
        self.state['spoiler_log'] = file_stem + '_Spoiler.json'

        await self.send_message(
            '%(reply_to)s, here is your seed: %(seed_uri)s'
            % {'reply_to': reply_to or 'Okay', 'seed_uri': seed_uri}
        )
        await self.set_raceinfo(seed_uri)

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
            await self.send_message(
                'Here is the spoiler log: %(spoiler_uri)s'
                % {'spoiler_uri': self.base_uri + self.state['spoiler_log']}
            )
            self.state['spoiler_sent'] = True

    def _race_in_progress(self):
        return self.data.get('status').get('value') in ('pending', 'in_progress')
