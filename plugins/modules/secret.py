#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""
Bella Baxter — ``secret`` Ansible module.

Creates, updates, or deletes secrets in the Bella Baxter secret management
gateway.  Requesting ``state: absent`` for a key that does not exist is a
no-op (``changed: false``).  ``state: present`` always reports a change: the
API has no single-secret read to compare against.

The module calls ``BaxterClient.set_secret`` / ``delete_secret`` BY NAME; the
guard test in ``tests/unit/plugins/modules/test_secret.py`` checks every call
against the methods the real SDK source defines (#1500).

Requires the ``bella-baxter`` Python package::

    pip install --pre 'bella-baxter>=0.1.1rc138,<0.2'
"""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: secret
short_description: Manage secrets in Bella Baxter
description:
  - Creates, updates (upserts), or deletes secrets in the Bella Baxter secret
    management gateway.
  - Writing needs an API key with the B(Manager) role or higher. A Consumer key
    is read-only and the API answers 403.
  - A scope-limited key can create keys inside its scope. The API refuses to
    update a key such a key may not read yet, and the SDK then creates it
    instead, which also stamps the key's scopes on the new secret.
  - With C(state=present) the module always reports C(changed=true) when it
    writes. The API has no single-secret read, so the value cannot be compared
    first.
  - When C(state=absent) and the key does not exist the API answers 404 and the
    task reports C(changed=false).
  - Supports Ansible check mode (C(--check)) — no API calls are made.
author:
  - Cosmic Chimps <hello@cosmicchimps.io>
version_added: "0.1.0"
requirements:
  - bella-baxter >= 0.1.1rc138, < 0.2 (Python package, C(pip install --pre 'bella-baxter>=0.1.1rc138,<0.2'))
  - A bella-baxter release that provides C(BaxterClient.set_secret) and
    C(BaxterClient.delete_secret), newer than C(0.1.1-preview.139). The module
    fails with a message saying so when the installed SDK lacks them.
options:
  baxter_url:
    description:
      - Base URL of the Bella Baxter API (e.g. C(https://baxter.example.com)).
    type: str
    required: true
  api_key:
    description:
      - Bella Baxter API key (bax-...). Manager role or higher.
    type: str
    required: true
    no_log: true
  project_slug:
    description:
      - Slug of the project that owns the environment.
      - Defaults to the project the API key belongs to.
    type: str
  environment_slug:
    description:
      - Slug of the target environment (e.g. C(production)).
      - The API addresses environments by slug, never by UUID.
      - Defaults to the environment the API key belongs to.
    type: str
  environment_id:
    description:
      - Not supported. The Bella Baxter API addresses environments by slug, so
        the module fails when this is set. Use O(environment_slug).
    type: str
  provider:
    description:
      - The provider that holds the secret, by slug (e.g. C(vault-main)) or by UUID.
    type: str
    required: true
    aliases: [provider_id]
  key:
    description:
      - Name of the secret key (e.g. C(DATABASE_PASSWORD)).
    type: str
    required: true
  value:
    description:
      - Value to store for the secret.
      - Required when C(state=present).
    type: str
    default: null
    no_log: true
  description:
    description:
      - Optional human-readable description for the secret.
    type: str
  state:
    description:
      - C(present) — create or update the secret (upsert).
      - C(absent) — delete the secret; a non-existent key is a no-op.
    type: str
    choices: [present, absent]
    default: present
"""

EXAMPLES = r"""
- name: Store RDS password
  jjchiw.bella_baxter.secret:
    baxter_url: "https://baxter.example.com"
    api_key: "{{ bella_api_key }}"
    project_slug: "my-app"
    environment_slug: "production"
    provider: "vault-main"
    key: "RDS_PASSWORD"
    value: "{{ generated_password }}"
    description: "RDS master password — rotated by Ansible"
    state: present
  no_log: true

- name: Store API key in the API key's own project and environment
  jjchiw.bella_baxter.secret:
    baxter_url: "{{ baxter_url }}"
    api_key: "{{ bella_api_key }}"
    provider: "550e8400-e29b-41d4-a716-446655440001"
    key: "STRIPE_SECRET_KEY"
    value: "sk_live_..."
    state: present
  no_log: true

- name: Remove a deprecated secret (idempotent — safe if key already absent)
  jjchiw.bella_baxter.secret:
    baxter_url: "{{ baxter_url }}"
    api_key: "{{ bella_api_key }}"
    environment_slug: "production"
    provider: "vault-main"
    key: "OLD_LEGACY_KEY"
    state: absent

- name: Dry-run — see what would change without touching the API
  jjchiw.bella_baxter.secret:
    baxter_url: "{{ baxter_url }}"
    api_key: "{{ bella_api_key }}"
    environment_slug: "production"
    provider: "vault-main"
    key: "DB_PASSWORD"
    value: "newpassword"
    state: present
  check_mode: true
"""

RETURN = r"""
changed:
  description:
    - Whether the module made a change to the secret.
    - Always C(true) for C(state=present) when it writes, because the API has no single-secret read to compare against.
    - For C(state=absent), C(false) when the key was already absent.
  returned: always
  type: bool
key:
  description: The secret key that was operated on.
  returned: always
  type: str
state:
  description: The final state of the secret (C(present) or C(absent)).
  returned: always
  type: str
"""

import traceback

from ansible.module_utils.basic import AnsibleModule

try:
    from bella_baxter import BaxterClient, BaxterClientOptions

    HAS_BELLA_BAXTER = True
    BELLA_IMPORT_ERROR = None
except ImportError:
    HAS_BELLA_BAXTER = False
    BELLA_IMPORT_ERROR = traceback.format_exc()


# What the module needs from the SDK. They are called by NAME with keyword arguments, so a signature change
# in the SDK is a test failure here (tests/unit/plugins/modules/test_secret.py reads the real client.py),
# not a playbook that breaks without anyone tracing it to the upgrade.
REQUIRED_SDK_METHODS = ("set_secret", "delete_secret")

SDK_TOO_OLD = (
    "this module needs a bella-baxter release that provides set_secret/delete_secret "
    "(upgrade the SDK: pip install --pre --upgrade bella-baxter)"
)


def _build_client(module: AnsibleModule) -> BaxterClient:
    """Instantiate a BaxterClient from module parameters."""
    options = BaxterClientOptions(
        baxter_url=module.params["baxter_url"],
        api_key=module.params["api_key"],
    )
    return BaxterClient(options)


def _scope(module: AnsibleModule) -> dict:
    """Project and environment as the SDK's keyword arguments. None means: the API key's own."""
    return {
        "project_slug": module.params.get("project_slug") or None,
        "env_slug": module.params.get("environment_slug") or None,
    }


def _failure(action: str, key: str, exc: BaseException) -> str:
    """
    A message that names the key and the HTTP status, nothing else.

    The exception text is left out on purpose: it comes from the server or the transport, and the secret
    value travelled in the same request, so it is not something to interpolate into task output.
    """
    status = getattr(exc, "response_status_code", None)
    detail = f"HTTP {status}" if status is not None else type(exc).__name__
    message = f"Failed to {action} secret '{key}' ({detail})."
    if status == 403:
        message += " Writing secrets needs an API key with the Manager role or higher."
    elif status == 404:
        message += " Check project_slug, environment_slug and provider: one of them does not exist."
    return message


def _present(module: AnsibleModule, client: BaxterClient) -> dict:
    """
    Ensure the secret exists with the desired value (upsert).

    ``client.set_secret`` is an upsert on the API, so the module reports ``changed=True`` whenever it is not
    in check mode: Bella Baxter does not expose a read-single-secret endpoint that would let us do a true
    idempotency check on the value.
    """
    key: str = module.params["key"]

    if not module.check_mode:
        try:
            client.set_secret(
                key,
                module.params["value"],
                provider=module.params["provider"],
                description=module.params.get("description"),
                **_scope(module),
            )
        except Exception as exc:
            module.fail_json(msg=_failure("write", key, exc), key=key, state="present")

    return {"changed": True, "key": key, "state": "present"}


def _absent(module: AnsibleModule, client: BaxterClient) -> dict:
    """
    Ensure the secret does not exist.

    ``client.delete_secret`` answers False when the API said 404 (already absent): no change.
    """
    key: str = module.params["key"]

    if module.check_mode:
        # In check mode we optimistically assume the key exists and would be deleted.
        return {"changed": True, "key": key, "state": "absent"}

    try:
        deleted = client.delete_secret(key, provider=module.params["provider"], **_scope(module))
    except Exception as exc:
        module.fail_json(msg=_failure("delete", key, exc), key=key, state="absent")

    return {"changed": bool(deleted), "key": key, "state": "absent"}


def run_module() -> None:
    argument_spec = dict(
        baxter_url=dict(type="str", required=True),
        api_key=dict(type="str", required=True, no_log=True),
        project_slug=dict(type="str"),
        environment_slug=dict(type="str"),
        environment_id=dict(type="str"),
        provider=dict(type="str", required=True, aliases=["provider_id"]),
        key=dict(type="str", required=True),
        value=dict(type="str", default=None, no_log=True),
        description=dict(type="str"),
        state=dict(type="str", default="present", choices=["present", "absent"]),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_if=[
            ("state", "present", ["value"]),
        ],
    )

    if module.params.get("environment_id"):
        module.fail_json(
            msg=(
                "environment_id is not supported: the Bella Baxter API addresses environments by slug, "
                "not by UUID. Use environment_slug (and project_slug, if it is not the API key's own)."
            )
        )

    if not HAS_BELLA_BAXTER:
        module.fail_json(
            msg=(
                "The 'bella_baxter' Python package is required for this module. "
                "Install it with: pip install --pre 'bella-baxter>=0.1.1rc138,<0.2'\n\n"
                f"Import error details:\n{BELLA_IMPORT_ERROR}"
            )
        )

    if not all(hasattr(BaxterClient, name) for name in REQUIRED_SDK_METHODS):
        module.fail_json(msg=SDK_TOO_OLD)

    state: str = module.params["state"]

    try:
        client = _build_client(module)
    except Exception as exc:
        # Nothing has been sent yet, so there is no secret value in this exception.
        module.fail_json(msg=f"Failed to initialise BaxterClient: {exc}")

    with client:
        if state == "present":
            result = _present(module, client)
        else:
            result = _absent(module, client)

    module.exit_json(**result)


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
