# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""
Bella Baxter — ``pki_ca`` lookup plugin.

Fetches the PKI CA certificate for a Bella Baxter environment.
Use the returned PEM certificate as your trusted CA when validating TLS
connections made by internal services.

Requires the ``bella-baxter`` Python package::

    pip install bella-baxter
"""

from __future__ import annotations

DOCUMENTATION = r"""
name: pki_ca
short_description: Fetch the PKI CA certificate from Bella Baxter
description:
  - Fetches the PKI Certificate Authority (CA) certificate for an environment
    in Bella Baxter.
  - Returns a dict containing C(certificate), C(ca_chain), and
    C(acme_directory_url).
  - The CA certificate is used to establish trust for TLS certificates issued
    by Bella Baxter via the same environment.
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
- name: Fetch PKI CA info
  ansible.builtin.set_fact:
    pki_ca: "{{ lookup('cosmic_chimps.bella.pki_ca',
                  project_slug='my-app',
                  environment_slug='production',
                  baxter_url='https://api.bella-baxter.io',
                  api_key='bax-abc123') }}"

- name: Write CA certificate to disk so services trust it
  ansible.builtin.copy:
    dest: /usr/local/share/ca-certificates/bella-ca.crt
    content: "{{ pki_ca.certificate }}"
    owner: root
    group: root
    mode: '0644'

- name: Update system trust store
  ansible.builtin.command: update-ca-certificates
  when: ansible_os_family == "Debian"
"""

RETURN = r"""
_raw:
  description:
    - List with a single item — a dict with PKI CA information.
  type: list
  elements: dict
  contains:
    certificate:
      description: PEM-encoded CA certificate.
      type: str
    ca_chain:
      description: Full PEM chain (CA + intermediates).
      type: str
    acme_directory_url:
      description: ACME directory URL for certbot/acme.sh auto-renewal.
      type: str
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
    """Lookup plugin that fetches PKI CA info from Bella Baxter."""

    def run(self, terms: list, variables=None, **kwargs) -> list[dict]:  # type: ignore[override]
        self.set_options(var_options=variables, direct=kwargs)

        baxter_url = self._resolve("baxter_url", "BELLA_BAXTER_URL", kwargs)
        api_key = self._resolve("api_key", "BELLA_API_KEY", kwargs)
        project_slug = kwargs.get("project_slug") or self._resolve("project_slug", "BELLA_PROJECT_SLUG", kwargs)
        environment_slug = kwargs.get("environment_slug") or self._resolve("environment_slug", "BELLA_ENV_SLUG", kwargs)

        if not project_slug:
            raise AnsibleError("project_slug is required. Set it via option or BELLA_PROJECT_SLUG env var.")
        if not environment_slug:
            raise AnsibleError("environment_slug is required. Set it via option or BELLA_ENV_SLUG env var.")
        if not baxter_url:
            raise AnsibleError("baxter_url is required (option or BELLA_BAXTER_URL env var).")
        if not api_key:
            raise AnsibleError("api_key is required (option or BELLA_API_KEY env var).")

        display.vvv(
            f"[cosmic_chimps.bella.pki_ca] Fetching PKI CA for "
            f"project_slug={project_slug} environment_slug={environment_slug}"
        )

        try:
            client = BaxterClient(BaxterClientOptions(baxter_url=baxter_url, api_key=api_key))
            ca_info = client.get_pki_ca(project_slug, environment_slug)
        except Exception as exc:
            raise AnsibleError(
                f"[cosmic_chimps.bella.pki_ca] Failed to fetch PKI CA: {exc}"
            ) from exc

        display.vvv("[cosmic_chimps.bella.pki_ca] Successfully fetched PKI CA")
        return [{
            "certificate": ca_info.certificate,
            "ca_chain": ca_info.ca_chain,
            "acme_directory_url": ca_info.acme_directory_url,
        }]

    def _resolve(self, option_name: str, env_var: str, kwargs: dict) -> str:
        if option_name in kwargs and kwargs[option_name]:
            return kwargs[option_name]
        try:
            val = self.get_option(option_name)
            if val:
                return val
        except KeyError:
            pass
        return os.environ.get(env_var, "")
