import sys
import os
from importlib import import_module

sys.path.append("..")
sys.path.append("../lib")

from utils.params import params
from atlas_utils.presenteragent import presenter_channel
from atlas_utils.acl_image import AclImage

def load_model_processor(model_name):
        """Loads ModelProcessor
        :param:
            + detector_name - Key name of detection model, i.e. face_detection, indoor_depth_estimation
        Returns
            A ModelProcessor class and its parameters for initialization
        """
        for task, task_models in params['task'].items():
            if model_name not in task_models:
                continue
            
            model_info = task_models[model_name]
            processor = model_info["model_processor"]
            MP = import_module(f"model_processors.{processor}")
            MP = getattr(MP, "ModelProcessor")
            return MP, model_info

def init_presenter_server():
    PRESENTER_SERVER_CONF = params['presenter_server_conf']
    chan = presenter_channel.open_channel(PRESENTER_SERVER_CONF)
    if chan is None:
        raise Exception("Open presenter channel failed")
    return chan
