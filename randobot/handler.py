import asyncio
import pathlib
import subprocess

from racetime_bot import RaceHandler, monitor_cmd, can_moderate, can_monitor

NUM_RANDO_RANDO_TRIES = 20
NUM_TRIES_PER_SETTINGS = 3

class RandoHandler(RaceHandler):
    """
    RandoBot race handler. Generates seeds, presets, and frustration.
    """
    stop_at = ['cancelled', 'finished']

    def __init__(self, rando_path, output_path, base_uri, **kwargs): #TODO take zsr
        super().__init__(**kwargs)

        self.rando_path = rando_path
        self.output_path = output_path
        self.base_uri = base_uri
        #self.zsr = zsr
        #self.presets = zsr.load_presets() #TODO add support for co-op/multiworld?
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
        if not self.state.get('intro_sent') and not self._race_in_progress():
            await self.send_message(
                'Welcome to the OoTR Random Settings League! Create a seed with !seed'
            )
            #await self.send_message(
            #    'If no preset is selected, default RSL settings will be used. ' #TODO
            #    'Use !spoilerseed to generate a seed with a spoiler log.' #TODO
            #)
            #await self.send_message( #TODO
            #    'For a list of presets, use !presets'
            #)
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
        await self.roll_and_send(args, message, True)

    #async def ex_spoilerseed(self, args, message):
    #    """
    #    Handle !race commands.
    #    """
    #    if self._race_in_progress():
    #        return
    #    await self.roll_and_send(args, message, False)

    #async def ex_presets(self, args, message): #TODO
    #    """
    #    Handle !presets commands.
    #    """
    #    if self._race_in_progress():
    #        return
    #    await self.send_presets()

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

    async def roll_and_send(self, args, message, encrypt):
        """
        Read an incoming !seed or !race command, and generate a new seed if
        valid.
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

        await self.roll(
            preset=args[0] if args else 'rsl',
            encrypt=encrypt,
            reply_to=reply_to,
        )

    async def roll(self, preset, encrypt, reply_to):
        """
        Generate a seed and send it to the race room.
        """
        #if preset not in self.presets:
        if preset != 'rsl': #TODO
            await self.send_message(
                #'Sorry %(reply_to)s, I don\'t recognise that preset. Use '
                #'!presets to see what is available.'
                'Sorry %(reply_to)s, I can currently only roll rsl seeds.' #TODO
                % {'reply_to': reply_to or 'friend'}
            )
            return

        for _ in range(NUM_RANDO_RANDO_TRIES):
            try:
                process = await asyncio.create_subprocess_exec('python3', 'PlandoRandomSettings.py', cwd=pathlib.Path(self.rando_path) / 'plando-random-settings')
                if await process.wait() != 0:
                    continue
            except subprocess.CalledProcessError:
                continue
            else:
                for _ in range(NUM_TRIES_PER_SETTINGS):
                    try:
                        process = await asyncio.create_subprocess_exec('python3', 'OoTRandomizer.py', f'--settings={pathlib.Path(__file__).parent / "settings.json"}', cwd=pathlib.Path(self.rando_path))
                        if await process.wait() != 0:
                            continue
                    except subprocess.CalledProcessError:
                        continue
                    else:
                        break
                else:
                    continue
                break
        else:
            await self.send_message(
                'Sorry %(reply_to)s, I couldn\'t generate the seed. (Tried %(outer_tries)d settings each %(inner_tries)d times)'
                % {'reply_to': reply_to or 'friend', 'outer_tries': NUM_RANDO_RANDO_TRIES, 'inner_tries': NUM_TRIES_PER_SETTINGS}
            )
            return

        patch_files = list((pathlib.Path(self.rando_path) / 'rsl-outputs').glob('*.zpf')) #TODO parse filename from output
        if len(patch_files) == 0:
            await self.send_message(
                'Sorry %(reply_to)s, something went wrong while generating the seed. (Patch file not found)'
                % {'reply_to': reply_to or 'friend'}
            )
            return
        elif len(patch_files) > 1:
            await self.send_message(
                'Sorry %(reply_to)s, something went wrong while generating the seed. (Multiple patch files found)'
                % {'reply_to': reply_to or 'friend'}
            )
            return
        filename = patch_files[0].name
        patch_files[0].rename(pathlib.Path(self.output_path) / filename)
        for unused_file in (pathlib.Path(self.rando_path) / 'rsl-outputs').glob(f'{patch_files[0].stem}_*.json'):
            unused_file.unlink()
        seed_uri = self.base_uri + filename

        await self.send_message(
            '%(reply_to)s, here is your seed: %(seed_uri)s'
            % {'reply_to': reply_to or 'Okay', 'seed_uri': seed_uri}
        )
        await self.set_raceinfo(seed_uri)

        self.state['seed_rolled'] = True

    async def send_presets(self):
        """
        Send a list of known presets to the race room.
        """
        await self.send_message('Available presets:')
        for name, full_name in self.presets.items():
            await self.send_message(f'{name} – {full_name}')

    def _race_in_progress(self):
        return self.data.get('status').get('value') in ('pending', 'in_progress')
