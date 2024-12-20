import datetime
import os
import logging
import yaml
import random
import numpy as np
import torch
import importlib
from collections import OrderedDict
from torch import nn
from typing import Optional

from argparse import Namespace


def strip_ddp_state_dict(state_dict):
    # Create a new state dict without DDP keys
    new_state_dict = OrderedDict()
    for k, v in state_dict.items():
        if k.startswith("module."):
            # Remove "module." prefix
            new_state_dict[k[7:]] = v
        else:
            new_state_dict[k] = v
    return new_state_dict

def init(config, *args, **kwargs):
    assert "type" in config 
    assert "args" in config
    return _init(config['type'], config['args'], *args, **kwargs)

def _init(path: str, config: dict, *args, **kwargs):
    p = path.split(".")
    package = ".".join(p[:-1])
    module = p[-1]
    return getattr(importlib.import_module(package), module)(*args, **kwargs, **config)


def setup_logger(args: Namespace, rank: int, out=True):
    """
    out: Whether to output log into a file. If False, then the log will be written to stdout directly.
    """
    if out:
        now = datetime.datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        log_dir = os.path.join(
            args.log, os.path.basename(args.config).replace(".yaml", "")
        )
        os.makedirs(log_dir, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s,%(name)s,%(levelname)s,%(message)s",
            handlers=[
                logging.FileHandler(f"{log_dir}/{now}.log"),
                logging.StreamHandler(),
            ],
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s,%(name)s,%(levelname)s,%(message)s",
            handlers=[logging.StreamHandler()],
        )
    logger = logging.getLogger()
    logger.info("logger initialized")
    return Logger(logger, rank)


def update_args(args: Namespace, config_file_path: str):
    with open(config_file_path, "r") as f:
        config = yaml.safe_load(f)
    for k, v in config.items():
        args.__setattr__(k, v)
    return args


class Logger:
    def __init__(self, log: logging.Logger, rank: int):
        self.log = log
        self.rank = rank

    def info(self, msg: str):
        if self.rank == 0:
            self.log.info(msg)

    def debug(self, msg: str):
        if self.rank == 0:
            self.log.debug(msg)
        pass

    def warning(self, msg: str):
        self.log.warning(f"rank {self.rank} - {msg}")
        pass

    def error(self, msg: str):
        self.log.error(f"rank {self.rank} - {msg}")
        pass

    def critical(self, msg: str):
        self.log.critical(f"rank {self.rank} - {msg}")

        pass


def setup_seed(seed, rank):
    SEED = int(seed) + rank
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    return SEED


class AttrDict(Namespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def __getattribute__(self, name: str):
        try:
            return super().__getattribute__(name)
        except AttributeError:
            return None

def load_ckpt(
    model: nn.Module, ckpt_path: Optional[str], device="cuda", strict=True, freeze=True
):
    model.to(device)
    if device == "cuda":
        model.cuda(device)
    if ckpt_path:
        ckpt = torch.load(ckpt_path, map_location=device)
        state_dict = strip_ddp_state_dict(ckpt["model_state_dict"])
        ### output missing part
        # missing, _, _, msg = _find_mismatched_keys(model.state_dict(), state_dict)
        # if missing:
        #     print(msg)
        model.load_state_dict(state_dict, strict=strict)
    if freeze:
        for p in model.parameters():
            p.requires_grad = False
    model.eval()
    return model



def make_path(file_path, is_dir=True):
    if is_dir:
        os.makedirs(file_path, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
    return file_path

