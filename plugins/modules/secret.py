#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""
Bella Baxter — ``secret`` Ansible module.

Creates, updates, or deletes secrets in the Bella Baxter secret management
gateway.  The module is idempotent: requesting ``state: absent`` for a key
that does not exist is a no-op (``changed: false``).

Requires the ``bella-baxter`` Python package::

    pip install bella-baxter
"""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: secret
short_description: Manage secrets in Bella Baxter
description:
  - Creates, updates (upserts), or deletes secrets in the Bella Baxter secret
    management gateway.
  - When C(state=absent) and the key does not exist the task reports
    C(changed=false) and makes no API call (idempotent).
  - Supports Ansible check mode (C(--check)) — no API calls are made.
author:
  - Cosmic Chimps <hello@cosmicchimps.io>
version_added: "0.1.0"
requirements:
  - bella-baxter >= 0.1 (Python package, C(pip install bella-baxter))
options:
  baxter_url:
    description:
      - Base URL of the Bella Baxter API (e.g. C(https://baxter.example.com)).
    type: str
    required: true
  api_key:
    description:
      - Bella Baxter API key (bax-...).
    type: str
    required: true
    no_log: true
  environment_id:
    description:
      - UUID of the target Bella Baxter environment.
    type: str
    required: true
  provider_id:
    description:
      - UUID of the target provider within the environment.
    type: str
    required: true
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
    default: ""
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
    environment_id: "550e8400-e29b-41d4-a716-446655440000"
    provider_id: "550e8400-e29b-41d4-a716-446655440001"
    key: "RDS_PASSWORD"
    value: "{{ generated_password }}"
    description: "RDS master password — rotated by Ansible"
    state: present

- name: Store API key with no description
  jjchiw.bella_baxter.secret:
    baxter_url: "{{ baxter_url }}"
    api_key: "{{ bella_api_key }}"
    environment_id: "{{ env_id }}"
    provider_id: "{{ provider_id }}"
    key: "STRIPE_SECRET_KEY"
    value: "sk_live_..."
    state: present

- name: Remove a deprecated secret (idempotent — safe if key already absent)
  jjchiw.bella_baxter.secret:
    baxter_url: "{{ baxter_url }}"
    api_key: "{{ bella_api_key }}"
    environment_id: "{{ env_id }}"
    provider_id: "{{ provider_id }}"
    key: "OLD_LEGACY_KEY"
    state: absent

- name: Dry-run — see what would change without touching the API
  jjchiw.bella_baxter.secret:
    baxter_url: "{{ baxter_url }}"
    api_key: "{{ bella_api_key }}"
    environment_id: "{{ env_id }}"
    provider_id: "{{ provider_id }}"
    key: "DB_PASSWORD"
    value: "newpassword"
    state: present
  check_mode: true
"""

RETURN = r"""
changed:
  description: Whether the module made a change to the secret.
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


def _build_client(module: AnsibleModule) -> BaxterClient:
    """Instantiate a BaxterClient from module parameters."""
    options = BaxterClientOptions(
        baxter_url=module.params["baxter_url"],
        api_key=module.params["api_key"],
    )
    return BaxterClient(options)


def _present(module: AnsibleModule, client: BaxterClient) -> dict:
    """
    Ensure the secret exists with the desired value (upsert).

    Always calls ``update_secret`` (which the Baxter API treats as an upsert),
    so the module always reports ``changed=True`` when not in check mode —
    Bella Baxter does not expose a read-single-secret endpoint that would let
    us do a true idempotency check on the value.
    """
    env_id: str = module.params["environment_id"]
    provider_id: str = module.params["provider_id"]
    key: str = module.params["key"]
    value: str = module.params["value"]
    description: str = module.params.get("description", "")

    if not module.check_mode:
        try:
            client.update_secret(env_id, provider_id, key, value, description)
        except Exception as exc:
            module.fail_json(
                msg=f"Failed to upsert secret '{key}': {exc}",
                key=key,
                state="present",
            )

    return {"changed": True, "key": key, "state": "present"}


def _absent(module: AnsibleModule, client: BaxterClient) -> dict:
    """
    Ensure the secret does not exist.

    A 404 from the API is treated as "already absent" — no change.
    """
    env_id: str = module.params["environment_id"]
    provider_id: str = module.params["provider_id"]
    key: str = module.params["key"]

    if module.check_mode:
        # In check mode we optimistically assume the key exists and would be deleted.
        return {"changed": True, "key": key, "state": "absent"}

    try:
        client.delete_secret(env_id, provider_id, key)
        return {"changed": True, "key": key, "state": "absent"}
    except Exception as exc:
        # Inspect the exception text for a 404-like indicator.
        exc_str = str(exc)
        if "404" in exc_str or "Not Found" in exc_str or "not found" in exc_str.lower():
            # Key already absent — idempotent no-op.
            return {"changed": False, "key": key, "state": "absent"}
        module.fail_json(
            msg=f"Failed to delete secret '{key}': {exc}",
            key=key,
            state="absent",
        )


def run_module() -> None:
    argument_spec = dict(
        baxter_url=dict(type="str", required=True),
        api_key=dict(type="str", required=True, no_log=True),
        environment_id=dict(type="str", required=True),
        provider_id=dict(type="str", required=True),
        key=dict(type="str", required=True),
        value=dict(type="str", default=None, no_log=True),
        description=dict(type="str", default=""),
        state=dict(type="str", default="present", choices=["present", "absent"]),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_if=[
            ("state", "present", ["value"]),
        ],
    )

    if not HAS_BELLA_BAXTER:
        module.fail_json(
            msg=(
                "The 'bella_baxter' Python package is required for this module. "
                "Install it with: pip install bella-baxter\n\n"
                f"Import error details:\n{BELLA_IMPORT_ERROR}"
            )
        )

    state: str = module.params["state"]

    try:
        client = _build_client(module)
    except Exception as exc:
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
