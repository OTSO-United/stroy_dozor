import json
from functools import lru_cache
from .config import CATALOG_PATH


@lru_cache
def catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def class_map():
    return {str(c["id"]): c for c in catalog()["classes"]}


def code_key(code):
    return tuple(int(part) for part in code.split("."))
