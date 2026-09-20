#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""
Bella Baxter — ``pki_role`` Ansible module.

Creates or deletes PKI roles in a Bella Baxter environment.
The module is idempotent: requesting C(state=absent) for a role that does
not exist is a no-op (C(changed=false)).

Requires the ``bella-baxter`` Python package::

    pip install --pre bella-baxter
"""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: pki_role
short_description: Manage PKI roles in Bella Baxter
description:
  - Creates or deletes PKI roles in a Bella Baxter environment.
  - The module is idempotent for both C(state=present) and C(state=absent).
  - PKI roles control which common names can be issued certificates and with
    what constraints (TTL, key type, allowed domains).
  - Supports Ansible check mode (C(--check)) — no API calls are made.
author:
  - Cosmic Chimps <hello@cosmicchimps.io>
version_added: "0.2.0"
requirements:
  - bella-baxter >= 0.2 (Python package, C(pip install --pre bella-baxter))
options:
  baxter_url:
    description:
      - Base URL of the Bella Baxter API (e.g. C(https://api.bella-baxter.io)).
    type: str
    required: true
  api_key:
    description:
      - Bella Baxter API key (bax-...).
    type: str
    required: true
    no_log: true
  project_slug:
    description:
      - Slug of the Bella Baxter project (e.g. C(my-app)).
    type: str
    required: true
  environment_slug:
    description:
      - Slug of the Bella Baxter environment (e.g. C(production)).
    type: str
    required: true
  name:
    description:
      - Name of the PKI role (e.g. C(internal-services)).
    type: str
    required: true
  allowed_domains:
    description:
      - Comma-separated list of allowed domains (e.g. C(internal,svc.cluster.local)).
      - Required when C(state=present).
    type: str
  allow_subdomains:
    description:
      - Whether to allow subdomains of C(allowed_domains).
    type: bool
    default: true
  allow_any_name:
    description:
      - Whether to allow any common name. Use with caution.
    type: bool
    default: false
  allow_localhost:
    description:
      - Whether to allow C(localhost) as a common name.
    type: bool
    default: false
  default_ttl:
    description:
      - Default certificate TTL (e.g. C(720h), C(30d)).
    type: str
    default: "720h"
  max_ttl:
    description:
      - Maximum certificate TTL (e.g. C(8760h), C(1y)).
    type: str
    default: "8760h"
  key_type:
    description:
      - Key type for issued certificates.
    type: str
    choices: [rsa, ec]
    default: rsa
  key_bits:
    description:
      - Key size in bits (e.g. C(2048) for RSA, C(256) for EC).
    type: int
  state:
    description:
      - C(present) — create the role if it does not exist.
      - C(absent) — delete the role if it exists.
    type: str
    choices: [present, absent]
    default: present
"""

EXAMPLES = r"""
- name: Create PKI role for internal services
  jjchiw.bella_baxter.pki_role:
    baxter_url: https://api.bella-baxter.io
    api_key: "{{ lookup('env', 'BELLA_API_KEY') }}"
    project_slug: my-app
    environment_slug: production
    name: internal-services
    allowed_domains: internal,svc.cluster.local
    allow_subdomains: true
    default_ttl: 720h
    max_ttl: 8760h
    state: present

- name: Remove a PKI role
  jjchiw.bella_baxter.pki_role:
    baxter_url: https://api.bella-baxter.io
    api_key: "{{ lookup('env', 'BELLA_API_KEY') }}"
    project_slug: my-app
    environment_slug: production
    name: old-role
    state: absent
"""

RETURN = r"""
role:
  description: The PKI role details (only present when C(state=present) and the role was created or already existed).
  type: dict
  returned: when state=present
  contains:
    name:
      description: Role name.
      type: str
    allowed_domains:
      description: Allowed domains.
      type: str
    allow_subdomains:
      description: Whether subdomains are allowed.
      type: bool
    default_ttl:
      description: Default TTL.
      type: str
    max_ttl:
      description: Maximum TTL.
      type: str
    key_type:
      description: Key type.
      type: str
"""

from ansible.module_utils.basic import AnsibleModule

try:
    from bella_baxter import BaxterClient, BaxterClientOptions, PkiCreateRoleRequest
    HAS_SDK = True
except ImportError:
    HAS_SDK = False


def run_module() -> None:
    module_args = dict(
        baxter_url=dict(type="str", required=True),
        api_key=dict(type="str", required=True, no_log=True),
        project_slug=dict(type="str", required=True),
        environment_slug=dict(type="str", required=True),
        name=dict(type="str", required=True),
        allowed_domains=dict(type="str", default=None),
        allow_subdomains=dict(type="bool", default=True),
        allow_any_name=dict(type="bool", default=False),
        allow_localhost=dict(type="bool", default=False),
        default_ttl=dict(type="str", default="720h"),
        max_ttl=dict(type="str", default="8760h"),
        key_type=dict(type="str", default="rsa", choices=["rsa", "ec"]),
        key_bits=dict(type="int", default=None),
        state=dict(type="str", default="present", choices=["present", "absent"]),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    if not HAS_SDK:
        module.fail_json(
            msg="The 'bella-baxter' Python package is required. Install it with: pip install --pre bella-baxter"
        )

    baxter_url = module.params["baxter_url"]
    api_key = module.params["api_key"]
    project_slug = module.params["project_slug"]
    environment_slug = module.params["environment_slug"]
    name = module.params["name"]
    state = module.params["state"]

    if state == "present" and not module.params["allowed_domains"] and not module.params["allow_any_name"]:
        module.fail_json(msg="allowed_domains is required when state=present and allow_any_name=false.")

    if module.check_mode:
        module.exit_json(changed=True, msg=f"Would have set PKI role '{name}' to state={state} (check mode)")

    client = BaxterClient(BaxterClientOptions(baxter_url=baxter_url, api_key=api_key))

    try:
        existing_roles = client.list_pki_roles(project_slug, environment_slug)
        role_names = {r.name for r in existing_roles if r.name}
    except Exception as exc:
        module.fail_json(msg=f"Failed to list PKI roles: {exc}")

    if state == "absent":
        if name not in role_names:
            module.exit_json(changed=False, msg=f"PKI role '{name}' does not exist")
        try:
            client.delete_pki_role(name, project_slug, environment_slug)
        except Exception as exc:
            module.fail_json(msg=f"Failed to delete PKI role '{name}': {exc}")
        module.exit_json(changed=True, msg=f"PKI role '{name}' deleted")

    # state == "present"
    if name in role_names:
        module.exit_json(changed=False, msg=f"PKI role '{name}' already exists")

    try:
        body = PkiCreateRoleRequest(
            name=name,
            allowed_domains=module.params["allowed_domains"],
            allow_subdomains=module.params["allow_subdomains"],
            allow_any_name=module.params["allow_any_name"],
            allow_localhost=module.params["allow_localhost"],
            default_ttl=module.params["default_ttl"],
            max_ttl=module.params["max_ttl"],
            key_type=module.params["key_type"],
            key_bits=module.params["key_bits"],
        )
        created = client.create_pki_role(body, project_slug, environment_slug)
    except Exception as exc:
        module.fail_json(msg=f"Failed to create PKI role '{name}': {exc}")

    module.exit_json(
        changed=True,
        msg=f"PKI role '{name}' created",
        role={
            "name": created.name,
            "allowed_domains": created.allowed_domains,
            "allow_subdomains": created.allow_subdomains,
            "default_ttl": created.default_ttl,
            "max_ttl": created.max_ttl,
            "key_type": created.key_type,
        },
    )


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
