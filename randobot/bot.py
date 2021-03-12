from racetime_bot import Bot

from .handler import RandoHandler


class RandoBot(Bot):
    """
    RandoBot base class.
    """
    def __init__(self, rsl_script_path, output_path, base_uri, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rsl_script_path = rsl_script_path
        self.output_path = output_path
        self.base_uri = base_uri

    def get_handler_class(self):
        return RandoHandler

    def get_handler_kwargs(self, *args, **kwargs):
        return {
            **super().get_handler_kwargs(*args, **kwargs),
            'rsl_script_path': self.rsl_script_path,
            'output_path': self.output_path,
            'base_uri': self.base_uri,
        }
