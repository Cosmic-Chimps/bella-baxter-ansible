# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""A stand-in ``bella_baxter`` whose ``BaxterClient`` has EXACTLY the methods the real SDK defines (#1500).

The ``secret`` module called ``client.update_secret(...)`` and ``client.delete_secret(...)``. ``BaxterClient``
had neither, and nothing noticed, because a test that fakes the SDK with a hand-written class passes for any
method name the fake happens to have. This stand-in cannot do that: it PARSES the real ``client.py`` and
builds one method per synchronous method found there, with that method's real signature. A call to a name the
SDK does not define is an ``AttributeError``; a call that does not fit the real signature (a positional
argument where the SDK says keyword-only, a missing required one) is a ``TypeError`` — exactly what the real
client would do, without a network.

Configuration is by environment variable, so the same stand-in serves the in-process tests and the module run
as a real Ansible subprocess:

``BELLA_STANDIN_CLIENT_PY``  path of the real ``bella_baxter/client.py`` (required)
``BELLA_STANDIN_LOG``        file that receives one JSON line per call: method, args, kwargs
``BELLA_STANDIN_BEHAVIOUR``  JSON ``{method: {"returns": x}}`` or ``{method: {"status": 403, "echo": "..."}}``;
                             the latter raises an error carrying ``response_status_code`` (as a Kiota
                             ``APIError`` does) whose message ECHOES ``echo``
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import pathlib


class BaxterClientOptions:
    def __init__(self, baxter_url, api_key, **_):
        self.baxter_url = baxter_url
        self.api_key = api_key


class StandInApiError(Exception):
    """Shaped like Kiota's ``APIError``: the status is an attribute, the message is free text."""

    def __init__(self, message, response_status_code):
        super().__init__(message)
        self.message = message
        self.response_status_code = response_status_code


def _signature(node: ast.FunctionDef) -> inspect.Signature:
    a = node.args
    P = inspect.Parameter
    params = []

    positional = list(a.posonlyargs) + list(a.args)
    n_defaults = len(a.defaults)
    first_default = len(positional) - n_defaults
    for index, arg in enumerate(positional):
        kind = P.POSITIONAL_ONLY if index < len(a.posonlyargs) else P.POSITIONAL_OR_KEYWORD
        default = ... if index >= first_default else P.empty
        params.append(P(arg.arg, kind, default=default))
    if a.vararg:
        params.append(P(a.vararg.arg, P.VAR_POSITIONAL))
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        params.append(P(arg.arg, P.KEYWORD_ONLY, default=P.empty if default is None else ...))
    if a.kwarg:
        params.append(P(a.kwarg.arg, P.VAR_KEYWORD))
    return inspect.Signature(params)


def _real_surface() -> dict:
    """name -> signature (``self`` included) of every plain synchronous method of the real BaxterClient."""
    source = pathlib.Path(os.environ["BELLA_STANDIN_CLIENT_PY"]).read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BaxterClient")
    return {
        node.name: _signature(node)
        for node in cls.body
        # Plain sync methods only: properties (`client`) and coroutines are not what a module calls.
        if isinstance(node, ast.FunctionDef) and not node.decorator_list and not node.name.startswith("_")
    }


def _record(method: str, args: tuple, kwargs: dict) -> None:
    path = os.environ.get("BELLA_STANDIN_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"method": method, "args": list(args), "kwargs": kwargs}) + "\n")


def _make(method: str, signature: inspect.Signature):
    def call(self, *args, **kwargs):
        signature.bind(self, *args, **kwargs)  # TypeError, as the real method would raise
        _record(method, args, kwargs)
        behaviour = json.loads(os.environ.get("BELLA_STANDIN_BEHAVIOUR", "{}")).get(method, {})
        if "status" in behaviour:
            raise StandInApiError(
                f"HTTP {behaviour['status']} from the API: {behaviour.get('echo', '')}", behaviour["status"]
            )
        return behaviour.get("returns")

    call.__name__ = method
    call.__signature__ = signature  # so `inspect.signature` shows the REAL parameters
    return call


class BaxterClient:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


for _name, _signature_of in _real_surface().items():
    setattr(BaxterClient, _name, _make(_name, _signature_of))
