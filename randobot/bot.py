from racetime_bot import Bot

from .handler import RandoHandler
#from .zsr import ZSR


class RandoBot(Bot):
    """
    RandoBot base class.
    """
    def __init__(self, rsl_script_path, output_path, base_uri, *args, **kwargs): #TODO take ootr_api_key
        super().__init__(*args, **kwargs)
        self.rsl_script_path = rsl_script_path
        self.output_path = output_path
        self.base_uri = base_uri
        #self.zsr = ZSR(ootr_api_key)

    def get_handler_class(self):
        return RandoHandler

    def get_handler_kwargs(self, *args, **kwargs): #TODO pass ootr_api_key
        return {
            **super().get_handler_kwargs(*args, **kwargs),
            'rsl_script_path': self.rsl_script_path,
            'output_path': self.output_path,
            'base_uri': self.base_uri,
            #'zsr': self.zsr,
        }
