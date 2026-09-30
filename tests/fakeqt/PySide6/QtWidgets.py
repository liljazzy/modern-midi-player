from ._fake import *  # noqa
from . import _fake

_cache = {}


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    if hasattr(_fake, name):
        return getattr(_fake, name)
    if name not in _cache:
        _cache[name] = _fake.make_class(name)
    return _cache[name]
