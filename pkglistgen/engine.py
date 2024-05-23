from enum import Enum


class Engine(Enum):
    product_composer = 1
    legacy = 2


ENGINE_NAMES = [member.name for member in list(Engine)]
