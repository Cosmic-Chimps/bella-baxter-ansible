# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""Unit tests for the ``jjchiw.bella_baxter.secret`` MODULE (#1500).

The defect: the module called ``client.update_secret(env_id, provider_id, key, value, description)`` and
``client.delete_secret(env_id, provider_id, key)``. ``BaxterClient`` has no ``update_secret`` and no
``delete_secret`` (until #1500), so ``state: present`` and ``state: absent`` both died with ``AttributeError``;
its ``environment_id`` option could not have worked either, because the API addresses environments by slug.
Nothing caught it: the module needs the SDK and a live server to run at all.

What these tests pin is the CALL. The stand-in SDK in ``sdk_standin/bella_baxter`` is built by PARSING the real
``client.py``: it offers exactly the methods the real ``BaxterClient`` defines, with their real signatures. A
call to a method that does not exist is an ``AttributeError``, and a call that does not fit the signature is a
``TypeError`` — so this suite is red against the original module and cannot go green by a fake that happens to
have the right name. (Same idea as the lookup's guard from #1482.)

How to run (from ``apps/sdk/ansible``)::

    python -m pytest tests/unit -q

It works with or without ``ansible-core`` installed:

* in-process tests replace ``ansible.module_utils.basic.AnsibleModule`` with a small stand-in that applies the
  module's own ``argument_spec`` (defaults, aliases, choices, required, ``required_if``);
* when ansible-core IS importable, a second group runs the module as a subprocess through the REAL
  ``AnsibleModule`` (argument spec, aliases, ``required_if``, check mode), exactly as Ansible would.

The SDK source sits beside the collection in the monorepo; the collection is also synced to a repository of its
own, where it does not, and the whole file skips there.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest

COLLECTION = pathlib.Path(__file__).resolve().parents[4]
MODULE = COLLECTION / "plugins" / "modules" / "secret.py"
STANDIN = pathlib.Path(__file__).resolve().parent / "sdk_standin"
SDK_CLIENT = COLLECTION.parent / "python" / "packages" / "sdk" / "src" / "bella_baxter" / "client.py"

pytestmark = pytest.mark.skipif(
    not SDK_CLIENT.exists(), reason="the SDK source is not beside this collection"
)

API_KEY = "bax-0123456789abcdef-" + "ab" * 32
SECRET_VALUE = "s3cr3t-value-that-must-never-be-printed"


# ── a stand-in AnsibleModule (in-process tests) ─────────────────────────────────────────────────────────


class ExitJson(Exception):
    def __init__(self, result):
        super().__init__("exit_json")
        self.result = result


class FailJson(Exception):
    def __init__(self, result):
        super().__init__("fail_json")
        self.result = result


class FakeAnsibleModule:
    """Applies an ``argument_spec`` the way Ansible does, for the parts this module uses."""

    args: dict = {}
    check_mode_value = False
    last_spec: dict = {}

    def __init__(self, argument_spec, supports_check_mode=False, required_if=None, **_):
        type(self).last_spec = argument_spec
        self.check_mode = type(self).check_mode_value
        params = {}
        given = dict(type(self).args)
        for name, spec in argument_spec.items():
            for alias in spec.get("aliases", []):
                if alias in given:
                    given.setdefault(name, given.pop(alias))
        unknown = sorted(set(given) - set(argument_spec))
        if unknown:
            self.fail_json(msg="Unsupported parameters: " + ", ".join(unknown))
        for name, spec in argument_spec.items():
            if name in given:
                params[name] = given[name]
            elif spec.get("required"):
                self.fail_json(msg=f"missing required arguments: {name}")
            else:
                params[name] = spec.get("default")
            if "choices" in spec and params[name] not in spec["choices"]:
                self.fail_json(msg=f"value of {name} must be one of: {', '.join(spec['choices'])}")
        for field, value, needed in required_if or []:
            if params.get(field) == value:
                missing = [n for n in needed if params.get(n) is None]
                if missing:
                    self.fail_json(msg=f"{field} is {value} but all of the following are missing: {missing}")
        self.params = params

    def fail_json(self, **kwargs):
        raise FailJson(kwargs)

    def exit_json(self, **kwargs):
        raise ExitJson(kwargs)


def _read_calls(path: pathlib.Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture()
def sdk_env(tmp_path, monkeypatch):
    """Environment shared by in-process and subprocess runs: where the real client.py is, where calls go."""
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("BELLA_STANDIN_CLIENT_PY", str(SDK_CLIENT))
    monkeypatch.setenv("BELLA_STANDIN_LOG", str(log))
    monkeypatch.delenv("BELLA_STANDIN_BEHAVIOUR", raising=False)
    return log


@pytest.fixture()
def module(sdk_env, monkeypatch):
    """The module, loaded from its file with the stand-in SDK and the stand-in AnsibleModule."""
    for name in ("bella_baxter", "ansible", "ansible.module_utils", "ansible.module_utils.basic"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(STANDIN))

    basic = types.ModuleType("ansible.module_utils.basic")
    basic.AnsibleModule = FakeAnsibleModule
    utils = types.ModuleType("ansible.module_utils")
    utils.basic = basic
    ansible = types.ModuleType("ansible")
    ansible.module_utils = utils
    monkeypatch.setitem(sys.modules, "ansible", ansible)
    monkeypatch.setitem(sys.modules, "ansible.module_utils", utils)
    monkeypatch.setitem(sys.modules, "ansible.module_utils.basic", basic)

    FakeAnsibleModule.args = {}
    FakeAnsibleModule.check_mode_value = False

    spec = importlib.util.spec_from_file_location("bella_secret_module_under_test", MODULE)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def common(**extra):
    return {"baxter_url": "https://baxter.example.com", "api_key": API_KEY, "key": "RDS_PASSWORD", **extra}


def run(module, sdk_env, args, *, check=False, behaviour=None):
    """Run ``run_module`` in-process; returns ("exit" | "fail", result, calls)."""
    if behaviour is not None:
        os.environ["BELLA_STANDIN_BEHAVIOUR"] = json.dumps(behaviour)
    FakeAnsibleModule.args = args
    FakeAnsibleModule.check_mode_value = check
    try:
        module.run_module()
    except ExitJson as done:
        return "exit", done.result, _read_calls(sdk_env)
    except FailJson as failed:
        return "fail", failed.result, _read_calls(sdk_env)
    raise AssertionError("the module neither exited nor failed")


@pytest.fixture(autouse=True)
def _clean_behaviour_env():
    yield
    os.environ.pop("BELLA_STANDIN_BEHAVIOUR", None)


# ── state: present ──────────────────────────────────────────────────────────────────────────────────────


def test_present_calls_set_secret_by_name_with_keywords(module, sdk_env):
    outcome, result, calls = run(
        module,
        sdk_env,
        common(
            value=SECRET_VALUE,
            provider="vault-main",
            project_slug="sandbox",
            environment_slug="dev",
            description="rotated by Ansible",
        ),
    )

    assert outcome == "exit"
    assert result == {"changed": True, "key": "RDS_PASSWORD", "state": "present"}
    (call,) = calls
    assert call["method"] == "set_secret"
    assert call["args"] == ["RDS_PASSWORD", SECRET_VALUE]
    assert call["kwargs"] == {
        "provider": "vault-main",
        "description": "rotated by Ansible",
        "project_slug": "sandbox",
        "env_slug": "dev",
    }


def test_present_without_project_or_environment_leaves_them_to_the_api_key(module, sdk_env):
    outcome, _, calls = run(module, sdk_env, common(value="v", provider="vault-main"))

    assert outcome == "exit"
    (call,) = calls
    assert call["kwargs"]["project_slug"] is None
    assert call["kwargs"]["env_slug"] is None
    assert call["kwargs"]["description"] is None


def test_an_empty_project_or_environment_means_the_key_decides(module, sdk_env):
    # A templated var that rendered empty must not reach the SDK as an empty slug.
    _, _, calls = run(
        module, sdk_env, common(value="v", provider="p", project_slug="", environment_slug="")
    )

    assert calls[0]["kwargs"]["project_slug"] is None
    assert calls[0]["kwargs"]["env_slug"] is None


def test_provider_id_is_still_accepted_as_an_alias_for_provider(module, sdk_env):
    provider_uuid = "550e8400-e29b-41d4-a716-446655440001"

    outcome, _, calls = run(module, sdk_env, common(value="v", provider_id=provider_uuid))

    assert outcome == "exit"
    assert calls[0]["kwargs"]["provider"] == provider_uuid


def test_present_needs_a_value(module, sdk_env):
    outcome, result, calls = run(module, sdk_env, common(provider="p"))

    assert outcome == "fail"
    assert "value" in result["msg"]
    assert calls == []


def test_the_provider_is_required(module, sdk_env):
    outcome, result, calls = run(module, sdk_env, common(value="v"))

    assert outcome == "fail"
    assert "provider" in result["msg"]
    assert calls == []


# ── state: absent ───────────────────────────────────────────────────────────────────────────────────────


def test_absent_calls_delete_secret_and_reports_a_change_when_it_deleted(module, sdk_env):
    outcome, result, calls = run(
        module,
        sdk_env,
        common(state="absent", provider="vault-main", project_slug="sandbox", environment_slug="dev"),
        behaviour={"delete_secret": {"returns": True}},
    )

    assert outcome == "exit"
    assert result == {"changed": True, "key": "RDS_PASSWORD", "state": "absent"}
    (call,) = calls
    assert call["method"] == "delete_secret"
    assert call["args"] == ["RDS_PASSWORD"]
    assert call["kwargs"] == {"provider": "vault-main", "project_slug": "sandbox", "env_slug": "dev"}


def test_absent_on_a_key_that_is_already_gone_reports_no_change(module, sdk_env):
    outcome, result, _ = run(
        module,
        sdk_env,
        common(state="absent", provider="vault-main"),
        behaviour={"delete_secret": {"returns": False}},
    )

    assert outcome == "exit"
    assert result["changed"] is False
    assert result["state"] == "absent"


def test_absent_does_not_need_a_value(module, sdk_env):
    outcome, _, _ = run(
        module, sdk_env, common(state="absent", provider="p"), behaviour={"delete_secret": {"returns": True}}
    )

    assert outcome == "exit"


# ── environment_id is refused with a message that says what to do ──────────────────────────────────────


@pytest.mark.parametrize("state", ["present", "absent"])
def test_environment_id_is_refused_and_names_the_replacement(module, sdk_env, state):
    outcome, result, calls = run(
        module,
        sdk_env,
        common(
            state=state,
            value="v",
            provider="vault-main",
            environment_id="550e8400-e29b-41d4-a716-446655440000",
        ),
    )

    assert outcome == "fail"
    assert "environment_slug" in result["msg"]
    assert "slug" in result["msg"]
    assert "Unsupported" not in result["msg"]  # our message, not Ansible's
    assert calls == []


# ── check mode ──────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("state", ["present", "absent"])
def test_check_mode_makes_no_sdk_call(module, sdk_env, state):
    outcome, result, calls = run(
        module, sdk_env, common(state=state, value="v", provider="vault-main"), check=True
    )

    assert outcome == "exit"
    assert result["changed"] is True
    assert calls == []


# ── failures: the key and the status, never the value ─────────────────────────────────────────────────


def test_a_write_failure_names_the_key_and_status_and_never_echoes_the_value(module, sdk_env):
    echoed = f"the server echoed {SECRET_VALUE} back"
    outcome, result, _ = run(
        module,
        sdk_env,
        common(value=SECRET_VALUE, provider="vault-main"),
        behaviour={"set_secret": {"status": 403, "echo": echoed}},
    )

    assert outcome == "fail"
    assert "RDS_PASSWORD" in result["msg"]
    assert "403" in result["msg"]
    assert "Manager" in result["msg"]
    assert SECRET_VALUE not in json.dumps(result)
    assert "echoed" not in json.dumps(result)


def test_a_delete_failure_names_the_key_and_status(module, sdk_env):
    outcome, result, _ = run(
        module,
        sdk_env,
        common(state="absent", provider="vault-main"),
        behaviour={"delete_secret": {"status": 500, "echo": SECRET_VALUE}},
    )

    assert outcome == "fail"
    assert "RDS_PASSWORD" in result["msg"]
    assert "500" in result["msg"]
    assert SECRET_VALUE not in json.dumps(result)


def test_a_404_that_reaches_the_module_is_an_error_not_an_absence(module, sdk_env):
    # The SDK turns "the key was not there" into False. A 404 that still escapes means the environment or the
    # provider does not exist, which a playbook must hear about (the old module read any text containing
    # "404" as "already absent").
    outcome, result, _ = run(
        module,
        sdk_env,
        common(state="absent", provider="vault-main"),
        behaviour={"delete_secret": {"status": 404}},
    )

    assert outcome == "fail"
    assert "provider" in result["msg"]


# ── the SDK bound ──────────────────────────────────────────────────────────────────────────────────────


def test_an_sdk_without_the_write_methods_is_refused_with_an_upgrade_message(module, sdk_env, monkeypatch):
    import bella_baxter

    # An SDK release that predates set_secret/delete_secret.
    monkeypatch.delattr(bella_baxter.BaxterClient, "set_secret")
    monkeypatch.delattr(bella_baxter.BaxterClient, "delete_secret")

    outcome, result, calls = run(module, sdk_env, common(value="v", provider="p"))

    assert outcome == "fail"
    assert "set_secret/delete_secret" in result["msg"]
    assert "upgrade the SDK" in result["msg"]
    assert calls == []


# ── the SDK surface the module relies on, and its documentation ───────────────────────────────────────


def test_the_stand_in_really_is_the_real_surface(module, sdk_env):
    import bella_baxter  # the stand-in, put first on sys.path by the fixture

    public = {n for n in vars(bella_baxter.BaxterClient) if not n.startswith("_")}

    assert {"get_all_secrets", "set_secret", "delete_secret", "get_key_context"} <= public
    # Nothing the real client lacks: this is the method the module used to call.
    assert "update_secret" not in public
    assert not hasattr(bella_baxter.BaxterClient, "update_secret")


def test_the_real_signatures_are_keyword_only_after_the_secret(module, sdk_env):
    import inspect

    import bella_baxter

    set_secret = inspect.signature(bella_baxter.BaxterClient.set_secret)
    delete_secret = inspect.signature(bella_baxter.BaxterClient.delete_secret)

    assert [p.name for p in set_secret.parameters.values() if p.kind is p.KEYWORD_ONLY] == [
        "provider",
        "project_slug",
        "env_slug",
        "description",
    ]
    assert [p.name for p in delete_secret.parameters.values() if p.kind is p.KEYWORD_ONLY] == [
        "provider",
        "project_slug",
        "env_slug",
    ]


def test_every_option_in_the_argument_spec_is_documented_and_the_reverse(module, sdk_env):
    yaml = pytest.importorskip("yaml")
    run(module, sdk_env, common(value="v", provider="p"))
    documented = yaml.safe_load(module.DOCUMENTATION)["options"]

    assert set(documented) == set(FakeAnsibleModule.last_spec)
    assert documented["provider"]["aliases"] == ["provider_id"]
    assert documented["provider"]["required"] is True
    assert documented["api_key"]["no_log"] is True
    assert documented["value"]["no_log"] is True


def test_the_documentation_says_what_the_api_requires(module):
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(module.DOCUMENTATION)
    text = json.dumps(parsed)

    assert "Manager" in text
    assert "slug" in text
    assert "scope-limited" in text
    assert "set_secret" in text  # the SDK requirement is stated
    assert yaml.safe_load(module.EXAMPLES)
    assert yaml.safe_load(module.RETURN)
    assert "environment_id" not in module.EXAMPLES
    assert "provider_id" not in module.EXAMPLES


# ── the module run the way Ansible runs it (real AnsibleModule; needs ansible-core) ───────────────────


def run_as_ansible(args: dict, sdk_env, *, behaviour=None, check=False) -> tuple:
    """
    Run secret.py in a subprocess, arguments on stdin, through the real ``AnsibleModule``.

    Real Ansible masks every ``no_log`` value in the module's output, wherever it occurs: a one-letter value
    would turn "environment" into "en********ironment" and make message assertions meaningless. These tests
    therefore use the long SECRET_VALUE.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(STANDIN)] + [p for p in sys.path if p])
    if behaviour is not None:
        env["BELLA_STANDIN_BEHAVIOUR"] = json.dumps(behaviour)
    payload = dict(args)
    if check:
        payload["_ansible_check_mode"] = True
    completed = subprocess.run(
        [sys.executable, str(MODULE)],
        input=json.dumps({"ANSIBLE_MODULE_ARGS": payload}),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        raise AssertionError(f"no JSON from the module.\nstdout: {completed.stdout}\nstderr: {completed.stderr}")
    return result, _read_calls(sdk_env)


try:
    import ansible  # noqa: F401
    from ansible.module_utils import basic as _real_basic  # noqa: F401

    HAS_ANSIBLE = True
except ImportError:
    HAS_ANSIBLE = False

real_ansible = pytest.mark.skipif(not HAS_ANSIBLE, reason="ansible-core is not installed")


@real_ansible
def test_real_ansible_present_with_the_provider_id_alias(sdk_env):
    result, calls = run_as_ansible(
        common(value=SECRET_VALUE, provider_id="550e8400-e29b-41d4-a716-446655440001"), sdk_env
    )

    assert result.get("failed") is not True, result
    assert result["changed"] is True
    (call,) = calls
    assert call["method"] == "set_secret"
    assert call["kwargs"]["provider"] == "550e8400-e29b-41d4-a716-446655440001"


@real_ansible
def test_real_ansible_absent_reports_what_the_sdk_returned(sdk_env):
    result, calls = run_as_ansible(
        common(state="absent", provider="vault-main", environment_slug="dev"),
        sdk_env,
        behaviour={"delete_secret": {"returns": False}},
    )

    assert result["changed"] is False
    assert calls[0]["method"] == "delete_secret"
    assert calls[0]["kwargs"]["env_slug"] == "dev"


@real_ansible
def test_real_ansible_refuses_environment_id_with_our_message(sdk_env):
    result, calls = run_as_ansible(
        common(value=SECRET_VALUE, provider="p", environment_id="550e8400-e29b-41d4-a716-446655440000"),
        sdk_env,
    )

    assert result["failed"] is True
    assert "environment_slug" in result["msg"]
    assert "Unsupported" not in result["msg"]
    assert calls == []


@real_ansible
def test_real_ansible_check_mode_makes_no_call(sdk_env):
    result, calls = run_as_ansible(common(value=SECRET_VALUE, provider="p"), sdk_env, check=True)

    assert result["changed"] is True
    assert calls == []


@real_ansible
def test_real_ansible_requires_a_value_to_present(sdk_env):
    result, calls = run_as_ansible(common(provider="p"), sdk_env)

    assert result["failed"] is True
    assert "value" in result["msg"]
    assert calls == []


@real_ansible
def test_real_ansible_never_prints_the_value_or_the_key_in_a_failure(sdk_env):
    result, _ = run_as_ansible(
        common(value=SECRET_VALUE, provider="p"),
        sdk_env,
        behaviour={"set_secret": {"status": 500, "echo": SECRET_VALUE}},
    )

    assert result["failed"] is True
    assert SECRET_VALUE not in json.dumps(result)
    assert API_KEY not in json.dumps(result)
