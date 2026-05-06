# -*- coding: utf-8 -*-
from importlib.machinery import SourcelessFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

_CACHE_FILE = 'hidden_layout.cpython-312.pyc.2162143401376'

cache_path = Path(__file__).with_name('__pycache__') / _CACHE_FILE
module_name = f"{__package__}._cached_{Path(__file__).stem}"
loader = SourcelessFileLoader(module_name, str(cache_path))
spec = spec_from_loader(module_name, loader)
if spec is None:
    raise ImportError(f"Could not create spec for {cache_path}")
module = module_from_spec(spec)
loader.exec_module(module)
for _name in dir(module):
    if _name.startswith('__') and _name not in {'__all__', '__doc__'}:
        continue
    globals()[_name] = getattr(module, _name)
