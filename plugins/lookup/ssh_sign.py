# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""
Bella Baxter — ``ssh_sign`` lookup plugin.

Signs an SSH public key using a Bella Baxter SSH role, returning the
signed certificate (OpenSSH certificate format).

Requires the ``bella-baxter`` Python package::

    pip install bella-baxter
"""

from __future__ import annotations

DOCUMENTATION = r"""
name: ssh_sign
short_description: Sign an SSH public key via Bella Baxter
description:
  - Submits an SSH public key to the Bella Baxter signing endpoint and
    returns the resulting signed certificate.
  - The certificate can be written to C(<key>-cert.pub) and used with
    C(ssh -i <key> -o CertificateFile=<key>-cert.pub user@host).
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
  role_name:
    description:
      - Name of the SSH role to use for signing.
    required: true
    type: str
  public_key:
    description:
      - Contents of the SSH public key to sign (e.g. from C(lookup('file', '~/.ssh/id_ed25519.pub'))).
    required: true
    type: str
  ttl:
    description:
      - Requested certificate TTL (e.g. C(1h), C(30m)). Uses the role default when omitted.
    type: str
  valid_principals:
    description:
      - Comma-separated list of principals to embed in the certificate
        (e.g. C(ec2-user,ubuntu)). Uses the role's C(allowed_users) when omitted.
    type: str
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
# --- Sign the control node's SSH key and store the cert ---
- name: Sign SSH public key via Bella Baxter
  ansible.builtin.set_fact:
    signed_cert: "{{ lookup('jjchiw.bella_baxter.ssh_sign',
                      project_slug='my-app',
                      environment_slug='production',
                      role_name='ops-team',
                      public_key=lookup('file', '~/.ssh/id_ed25519.pub'),
                      ttl='1h',
                      baxter_url='https://api.bella-baxter.io',
                      api_key='bax-abc123') }}"

- name: Write the signed certificate to disk
  ansible.builtin.copy:
    dest: ~/.ssh/id_ed25519-cert.pub
    content: "{{ signed_cert }}"
    mode: '0600'
"""

RETURN = r"""
_raw:
  description:
    - List with a single item — the signed SSH certificate string.
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
    """Lookup plugin that signs an SSH public key via Bella Baxter."""

    def run(self, terms: list, variables=None, **kwargs) -> list[str]:  # type: ignore[override]
        """
        Sign an SSH public key and return the signed certificate.

        :param terms: Unused (required by Ansible lookup API).
        :param variables: Ansible task variables (unused but required by API).
        :param kwargs: Plugin options (see DOCUMENTATION above).
        :returns: List containing the signed certificate string.
        :raises AnsibleError: On missing configuration or API failure.
        """
        self.set_options(var_options=variables, direct=kwargs)

        baxter_url = self._resolve("baxter_url", "BELLA_BAXTER_URL", kwargs)
        api_key = self._resolve("api_key", "BELLA_API_KEY", kwargs)

        project_slug = kwargs.get("project_slug") or self._resolve("project_slug", "BELLA_PROJECT_SLUG", kwargs)
        environment_slug = kwargs.get("environment_slug") or self._resolve("environment_slug", "BELLA_ENV_SLUG", kwargs)
        role_name = kwargs.get("role_name") or ""
        public_key = kwargs.get("public_key") or ""
        ttl = kwargs.get("ttl") or None
        valid_principals = kwargs.get("valid_principals") or None

        # Validate required params
        for name, val in [
            ("project_slug", project_slug),
            ("environment_slug", environment_slug),
            ("role_name", role_name),
            ("public_key", public_key),
        ]:
            if not val:
                raise AnsibleError(f"{name} is required for the ssh_sign lookup plugin.")

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
            f"[jjchiw.bella_baxter.ssh_sign] Signing key for role='{role_name}' "
            f"project_slug={project_slug} environment_slug={environment_slug}"
        )

        try:
            client = BaxterClient(BaxterClientOptions(baxter_url=baxter_url, api_key=api_key))
            signed = client.sign_ssh_key(
                role_name=role_name,
                public_key=public_key.strip(),
                ttl=ttl,
                valid_principals=valid_principals,
                project_slug=project_slug,
                env_slug=environment_slug,
            )
        except Exception as exc:
            raise AnsibleError(
                f"[jjchiw.bella_baxter.ssh_sign] Failed to sign SSH key: {exc}"
            ) from exc

        display.vvv("[jjchiw.bella_baxter.ssh_sign] SSH key signed successfully")
        return [signed.signed_key]

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

