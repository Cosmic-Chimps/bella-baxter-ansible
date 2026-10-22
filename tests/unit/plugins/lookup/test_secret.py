# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""Unit tests for the ``jjchiw.bella_baxter.secret`` lookup (#1482).

The defect: the lookup called ``client.get_all_secrets(environment_slug)``. The SDK's first positional
parameter is ``project_slug``, so the environment name travelled as the PROJECT and the API answered 404 on
every use, even with a perfectly valid playbook. Nothing tested the call, because the plugin needs the SDK and a
live server to run at all.

What these tests pin is the CALL, not the server: a stand-in ``bella_baxter`` records how the lookup invokes
``get_all_secrets``, and a drift guard checks that stand-in's parameter names against the real SDK source, so the
two cannot silently disagree again.

How to run (from ``apps/sdk/ansible``)::

    python -m pytest tests/unit -q

It works with or without ``ansible-core`` installed: when it is missing, minimal stand-ins for the three
``ansible`` modules the plugin imports are registered. With ansible-core present the real ``LookupBase`` is used.
``ansible-test units`` also collects this file (it is plain pytest and loads the plugin by file path).
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
import sys
import types

import pytest

COLLECTION = pathlib.Path(__file__).resolve().parents[4]
PLUGIN = COLLECTION / "plugins" / "lookup" / "secret.py"
# The SDK source lives beside the collection in the monorepo; the collection is also synced to a repository
# of its own, where this path does not exist and the drift guard skips.
SDK_CLIENT = COLLECTION.parent / "python" / "packages" / "sdk" / "src" / "bella_baxter" / "client.py"

API_KEY = "bax-0123456789abcdef-" + "ab" * 32


# ── stand-ins ────────────────────────────────────────────────────────────────────────────────────────────────


class RecordingClient:
    """What ``BaxterClient`` looks like to the lookup: it records each call as it was made."""

    calls: list[tuple[tuple, dict]] = []
    secrets = {"DATABASE_URL": "postgres://db", "REDIS_URL": "redis://cache"}

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def get_all_secrets(self, *args, **kwargs):
        # Records the call EXACTLY as made. Declaring (project_slug=None, env_slug=None) here would hide the
        # defect: `get_all_secrets("dev")` would simply bind "dev" to project_slug. The real parameter list is
        # checked against the SDK source below instead.
        type(self).calls.append((args, kwargs))
        return types.SimpleNamespace(secrets=dict(type(self).secrets))


class Options:
    def __init__(self, baxter_url, api_key, **_):
        self.baxter_url = baxter_url
        self.api_key = api_key


def _install_ansible_stand_ins() -> None:
    try:
        import ansible  # noqa: F401
        return
    except ImportError:
        pass

    errors = types.ModuleType("ansible.errors")

    class AnsibleError(Exception):
        pass

    errors.AnsibleError = AnsibleError

    lookup = types.ModuleType("ansible.plugins.lookup")

    class LookupBase:
        def __init__(self, loader=None, templar=None, **kwargs):
            pass

        def set_options(self, var_options=None, direct=None):
            pass

        def get_option(self, name):
            return None

    lookup.LookupBase = LookupBase

    display = types.ModuleType("ansible.utils.display")

    class Display:
        def vvv(self, *_args, **_kwargs):
            pass

    display.Display = Display

    for name, module in {
        "ansible": types.ModuleType("ansible"),
        "ansible.errors": errors,
        "ansible.plugins": types.ModuleType("ansible.plugins"),
        "ansible.plugins.lookup": lookup,
        "ansible.utils": types.ModuleType("ansible.utils"),
        "ansible.utils.display": display,
    }.items():
        sys.modules.setdefault(name, module)


@pytest.fixture()
def plugin(monkeypatch):
    """The lookup module, loaded from its file with the SDK replaced by a recording stand-in."""
    _install_ansible_stand_ins()

    sdk = types.ModuleType("bella_baxter")
    sdk.BaxterClient = RecordingClient
    sdk.BaxterClientOptions = Options
    monkeypatch.setitem(sys.modules, "bella_baxter", sdk)
    RecordingClient.calls = []

    # No ambient configuration may leak into a case: each sets exactly what it means to.
    for variable in ("BELLA_BAXTER_URL", "BELLA_API_KEY", "BELLA_PROJECT_SLUG", "BELLA_ENV_SLUG"):
        monkeypatch.delenv(variable, raising=False)

    spec = importlib.util.spec_from_file_location("bella_secret_lookup_under_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_lookup(plugin, *terms, **kwargs):
    lookup = plugin.LookupModule()
    # `set_options`/`get_option` belong to Ansible's plugin loader, which is not running here. The lookup's own
    # resolution (kwarg, then option, then environment variable) is what is under test, and it reads kwargs first.
    lookup.set_options = lambda var_options=None, direct=None: None
    lookup.get_option = lambda name: None
    return lookup.run(list(terms), variables=None, **kwargs)


def common(**extra):
    return {"baxter_url": "https://baxter.example.com", "api_key": API_KEY, **extra}


# ── the call into the SDK ───────────────────────────────────────────────────────────────────────────────────


def test_environment_only_is_passed_as_env_slug_never_as_the_project(plugin):
    result = run_lookup(plugin, "DATABASE_URL", **common(environment_slug="dev"))

    assert result == ["postgres://db"]
    (args, kwargs), = RecordingClient.calls
    assert args == ()
    # The old call bound "dev" to project_slug and sent project_slug="dev", env_slug=None.
    assert kwargs == {"project_slug": None, "env_slug": "dev"}


def test_project_and_environment_are_both_passed_by_name(plugin):
    result = run_lookup(
        plugin, "DATABASE_URL", "REDIS_URL", **common(project_slug="sandbox", environment_slug="dev")
    )

    assert result == ["postgres://db", "redis://cache"]
    (args, kwargs), = RecordingClient.calls
    assert args == ()
    assert kwargs == {"project_slug": "sandbox", "env_slug": "dev"}


def test_the_project_falls_back_to_the_documented_environment_variable(plugin, monkeypatch):
    monkeypatch.setenv("BELLA_PROJECT_SLUG", "my-app")

    run_lookup(plugin, "DATABASE_URL", **common(environment_slug="production"))

    (_, kwargs), = RecordingClient.calls
    assert kwargs == {"project_slug": "my-app", "env_slug": "production"}


def test_an_empty_project_means_the_key_decides(plugin):
    # `project_slug=''` from a templated var that rendered empty must not reach the SDK as an empty project.
    run_lookup(plugin, "DATABASE_URL", **common(project_slug="", environment_slug="dev"))

    (_, kwargs), = RecordingClient.calls
    assert kwargs["project_slug"] is None


def test_a_missing_key_is_named_with_its_environment(plugin):
    with pytest.raises(plugin.AnsibleError) as raised:
        run_lookup(plugin, "NOT_THERE", **common(environment_slug="dev"))

    assert "NOT_THERE" in str(raised.value)
    assert "'dev'" in str(raised.value)


# ── the documentation and the SDK agree with the call ───────────────────────────────────────────────────────


def test_project_slug_is_a_declared_option(plugin):
    # The docs (public-docs/sdks/ansible.md) pass project_slug=; an undeclared option is rejected by Ansible.
    assert re.search(r"^  project_slug:$", plugin.DOCUMENTATION, re.MULTILINE)
    assert "BELLA_PROJECT_SLUG" in plugin.DOCUMENTATION


def test_the_sdk_dependency_is_bounded(plugin):
    assert "bella-baxter >= 0.1.1rc138, < 0.2" in plugin.DOCUMENTATION
    requirements = (COLLECTION / "requirements.txt").read_text(encoding="utf-8")
    assert "bella-baxter>=0.1.1rc138,<0.2" in requirements


@pytest.mark.skipif(not SDK_CLIENT.exists(), reason="the SDK source is not beside this collection")
def test_the_stand_in_has_the_real_sdk_signature():
    """If the SDK renames or reorders these, the lookup's named call is the thing that must follow."""
    tree = ast.parse(SDK_CLIENT.read_text(encoding="utf-8"))
    method = next(
        node
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef) and cls.name == "BaxterClient"
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_all_secrets"
    )
    parameters = [a.arg for a in method.args.args]

    assert parameters == ["self", "project_slug", "env_slug"]
