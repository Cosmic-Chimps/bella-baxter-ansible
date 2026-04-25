# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""
Bella Baxter — ``ssh_ca_key`` lookup plugin.

Fetches the SSH CA public key from a Bella Baxter environment provider.
Use the returned key as the value for ``TrustedUserCAKeys`` in ``sshd_config``
so that any certificate signed by Bella for that environment is trusted.

Requires the ``bella-baxter`` Python package::

    pip install bella-baxter
"""

from __future__ import annotations

DOCUMENTATION = r"""
name: ssh_ca_key
short_description: Fetch the SSH CA public key from Bella Baxter
description:
  - Fetches the SSH Certificate Authority (CA) public key for an environment in Bella Baxter.
  - The returned key can be written to C(/etc/ssh/trusted-user-ca-keys.pem)
    and referenced in C(sshd_config) as C(TrustedUserCAKeys).
  - The Vault/OpenBao provider is selected automatically from the environment.
author:
  - Cosmic Chimps <hello@cosmicchimps.io>
version_added: "0.2.0"
requirements:
  - bella-baxter (Python package, C(pip install bella-baxter))
options:
  project_slug:
    description:
      - Slug of the Bella Baxter project (e.g. C(my-app)).
      - Falls back to C(BELLA_PROJECT_SLUG) environment variable.
    required: true
    type: str
    env:
      - name: BELLA_PROJECT_SLUG
    ini:
      - section: bella_baxter
        key: project_slug
  environment_slug:
    description:
      - Slug of the Bella Baxter environment (e.g. C(production)).
      - Falls back to C(BELLA_ENV_SLUG) environment variable.
    required: true
    type: str
    env:
      - name: BELLA_ENV_SLUG
    ini:
      - section: bella_baxter
        key: environment_slug
  baxter_url:
    description:
      - Base URL of the Bella Baxter API (e.g. C(https://api.bella-baxter.io)).
      - Falls back to C(BELLA_BAXTER_URL) environment variable.
    type: str
    env:
      - name: BELLA_BAXTER_URL
    ini:
      - section: bella_baxter
        key: url
  api_key:
    description:
      - Bella Baxter API key (bax-...).
      - Falls back to C(BELLA_API_KEY) environment variable.
    type: str
    env:
      - name: BELLA_API_KEY
    ini:
      - section: bella_baxter
        key: api_key
"""

EXAMPLES = r"""
# --- Using environment slug ---
- name: Read SSH CA public key
  ansible.builtin.set_fact:
    ssh_ca: "{{ lookup('cosmic_chimps.bella.ssh_ca_key',
                  project_slug='my-app',
                  environment_slug='production',
                  baxter_url='https://api.bella-baxter.io',
                  api_key='bax-abc123') }}"

# --- Using environment variables ---
# export BELLA_BAXTER_URL=https://api.bella-baxter.io
# export BELLA_API_KEY=bax-abc123
# export BELLA_PROJECT_SLUG=my-app
# export BELLA_ENV_SLUG=production

- name: Write CA key from environment variables
  ansible.builtin.set_fact:
    ssh_ca: "{{ lookup('cosmic_chimps.bella.ssh_ca_key',
                  project_slug=lookup('env', 'BELLA_PROJECT_SLUG'),
                  environment_slug=lookup('env', 'BELLA_ENV_SLUG')) }}"
"""

RETURN = r"""
_raw:
  description:
    - List with a single item — the SSH CA public key string in OpenSSH format.
  type: list
  elements: str
"""

import os

from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase
from ansible.utils.display import Display

display = Display()

try:
    from bella_baxter import BaxterClient, BaxterClientOptions
except ImportError as _exc:  # pragma: no cover
    raise AnsibleError(
        "The 'bella-baxter' Python package is required for this lookup plugin. "
        "Install it with: pip install bella-baxter"
    ) from _exc


class LookupModule(LookupBase):
    """Lookup plugin that fetches the SSH CA public key from Bella Baxter."""

    def run(self, terms: list, variables=None, **kwargs) -> list[str]:  # type: ignore[override]
        """
        Return a list with one item — the SSH CA public key.

        :param terms: Unused (required by Ansible lookup API).
        :param variables: Ansible task variables (unused but required by API).
        :param kwargs: Keyword overrides for plugin options.
        :returns: List containing the CA public key string.
        :raises AnsibleError: On missing configuration or API failure.
        """
        self.set_options(var_options=variables, direct=kwargs)

        baxter_url = self._resolve("baxter_url", "BELLA_BAXTER_URL", kwargs)
        api_key = self._resolve("api_key", "BELLA_API_KEY", kwargs)

        project_slug = kwargs.get("project_slug") or self._resolve("project_slug", "BELLA_PROJECT_SLUG", kwargs)
        environment_slug = kwargs.get("environment_slug") or self._resolve("environment_slug", "BELLA_ENV_SLUG", kwargs)

        if not project_slug:
            raise AnsibleError(
                "project_slug is required for the ssh_ca_key lookup plugin. "
                "Set it via the 'project_slug' option or BELLA_PROJECT_SLUG environment variable."
            )
        if not environment_slug:
            raise AnsibleError(
                "environment_slug is required for the ssh_ca_key lookup plugin. "
                "Set it via the 'environment_slug' option or BELLA_ENV_SLUG environment variable."
            )
        if not baxter_url:
            raise AnsibleError(
                "baxter_url is required (set the 'baxter_url' option or "
                "BELLA_BAXTER_URL environment variable)"
            )
        if not api_key:
            raise AnsibleError(
                "api_key is required (set the 'api_key' option or "
                "BELLA_API_KEY environment variable)"
            )

        display.vvv(
            f"[cosmic_chimps.bella.ssh_ca_key] Fetching CA public key "
            f"for project_slug={project_slug} environment_slug={environment_slug}"
        )

        try:
            client = BaxterClient(BaxterClientOptions(baxter_url=baxter_url, api_key=api_key))
            ca_info = client.get_ssh_ca_public_key(project_slug, environment_slug)
        except Exception as exc:
            raise AnsibleError(
                f"[cosmic_chimps.bella.ssh_ca_key] Failed to fetch SSH CA public key: {exc}"
            ) from exc

        display.vvv("[cosmic_chimps.bella.ssh_ca_key] Successfully fetched CA public key")
        return [ca_info.ca_public_key]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve(self, option_name: str, env_var: str, kwargs: dict) -> str:
        """Return option value: kwarg > plugin option > env var."""
        if option_name in kwargs and kwargs[option_name]:
            return kwargs[option_name]
        try:
            val = self.get_option(option_name)
            if val:
                return val
        except KeyError:
            pass
        return os.environ.get(env_var, "")

