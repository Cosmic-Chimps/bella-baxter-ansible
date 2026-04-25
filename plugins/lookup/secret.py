# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""
Bella Baxter — ``secret`` lookup plugin.

Fetches one or more secret values from the Bella Baxter secret management
gateway.  All secrets for the target environment are retrieved in a single
API call (Baxter caches them in Redis), so looking up multiple keys is as
cheap as looking up one.

Requires the ``bella-baxter`` Python package::

    pip install bella-baxter
"""

from __future__ import annotations

DOCUMENTATION = r"""
name: secret
short_description: Fetch secrets from Bella Baxter
description:
  - Reads secret values from the Bella Baxter secret management gateway.
  - All secrets for the target environment are fetched in one API call and
    cached for the lifetime of the lookup. Looking up multiple keys is as
    cheap as looking up one.
  - Credentials are supplied as keyword arguments or via environment variables.
author:
  - Cosmic Chimps <hello@cosmicchimps.io>
version_added: "0.1.0"
requirements:
  - bella-baxter >= 0.1 (Python package, C(pip install bella-baxter))
options:
  _terms:
    description:
      - One or more secret key names to look up (e.g. C(DATABASE_URL)).
    required: true
    type: list
    elements: str
  baxter_url:
    description:
      - Base URL of the Bella Baxter API (e.g. C(https://api.bella-baxter.io)).
      - Falls back to the C(BELLA_BAXTER_URL) environment variable.
    type: str
    env:
      - name: BELLA_BAXTER_URL
    ini:
      - section: bella_baxter
        key: url
  environment_slug:
    description:
      - Slug of the Bella Baxter environment to read from (e.g. C(production)).
      - Falls back to the C(BELLA_ENV_SLUG) environment variable.
    type: str
    env:
      - name: BELLA_ENV_SLUG
    ini:
      - section: bella_baxter
        key: environment_slug
  api_key:
    description:
      - Bella Baxter API key client ID.
      - Falls back to the C(BELLA_API_KEY) environment variable.
    type: str
    env:
      - name: BELLA_API_KEY
    ini:
      - section: bella_baxter
        key: api_key
"""

EXAMPLES = r"""
# --- Using inline keyword arguments ---

- name: Read a single secret
  ansible.builtin.debug:
    msg: "DB host is {{ db_host }}"
  vars:
    db_host: "{{ lookup('cosmic_chimps.bella.secret', 'DB_HOST',
                  baxter_url='https://api.bella-baxter.io',
                  environment_slug='production',
                  api_key='bax-abc123') }}"

# --- Using environment variables (recommended for CI/CD) ---
# export BELLA_BAXTER_URL=https://api.bella-baxter.io
# export BELLA_API_KEY=bax-abc123
# export BELLA_ENV_SLUG=production

- name: Read a single secret via env vars
  ansible.builtin.debug:
    msg: "Password loaded"
  vars:
    db_password: "{{ lookup('cosmic_chimps.bella.secret', 'DATABASE_PASSWORD') }}"

# --- Multiple secrets in one call ---

- name: Fetch several secrets at once
  ansible.builtin.set_fact:
    app_secrets: "{{ lookup('cosmic_chimps.bella.secret',
                      'DB_HOST', 'DB_PORT', 'REDIS_URL',
                      environment_slug='staging',
                      wantlist=True) }}"
  # app_secrets == ['db.staging.internal', '5432', 'redis://...']
"""

RETURN = r"""
_raw:
  description:
    - List of secret values corresponding to the input key names.
    - Values are returned in the same order as the C(_terms) input list.
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
        "The 'bella_baxter' Python package is required for this lookup plugin. "
        "Install it with: pip install bella-baxter"
    ) from _exc


class LookupModule(LookupBase):
    """Lookup plugin that fetches secrets from Bella Baxter."""

    def run(self, terms: list[str], variables=None, **kwargs) -> list[str]:  # type: ignore[override]
        """
        Fetch secret values for the given key names.

        :param terms: List of secret key names to look up.
        :param variables: Ansible task variables (unused but required by API).
        :param kwargs: Keyword overrides for plugin options.
        :returns: List of secret values in the same order as *terms*.
        :raises AnsibleError: On missing configuration or API failure.
        """
        self.set_options(var_options=variables, direct=kwargs)

        baxter_url = self._resolve("baxter_url", "BELLA_BAXTER_URL", kwargs)
        environment_slug = self._resolve("environment_slug", "BELLA_ENV_SLUG", kwargs)
        api_key = self._resolve("api_key", "BELLA_API_KEY", kwargs)

        if not baxter_url:
            raise AnsibleError(
                "baxter_url is required (set the 'baxter_url' option or "
                "BELLA_BAXTER_URL environment variable)"
            )
        if not environment_slug:
            raise AnsibleError(
                "environment_slug is required (set the 'environment_slug' option or "
                "BELLA_ENV_SLUG environment variable)"
            )
        if not api_key:
            raise AnsibleError(
                "api_key is required (set the 'api_key' option or "
                "BELLA_API_KEY environment variable)"
            )
        if not terms:
            return []

        display.vvv(
            f"[cosmic_chimps.bella.secret] Fetching {len(terms)} key(s) "
            f"from environment '{environment_slug}' at {baxter_url}"
        )

        options = BaxterClientOptions(
            baxter_url=baxter_url,
            api_key=api_key,
        )

        try:
            with BaxterClient(options) as client:
                response = client.get_all_secrets(environment_slug)
        except Exception as exc:
            raise AnsibleError(
                f"[cosmic_chimps.bella.secret] Failed to fetch secrets from Bella Baxter: {exc}"
            ) from exc

        secrets = response.secrets
        results: list[str] = []
        missing: list[str] = []

        for key in terms:
            if key in secrets:
                results.append(secrets[key])
            else:
                missing.append(key)

        if missing:
            raise AnsibleError(
                f"[cosmic_chimps.bella.secret] The following secret key(s) were not found "
                f"in environment '{environment_slug}': {missing}"
            )

        display.vvv(
            f"[cosmic_chimps.bella.secret] Successfully resolved {len(terms)} key(s)"
        )
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve(self, option_name: str, env_var: str, kwargs: dict) -> str:
        """
        Return the value for *option_name*, preferring:
        1. Direct kwarg passed by the caller (highest priority)
        2. Plugin option (from ansible.cfg / inventory vars)
        3. Environment variable (lowest priority)
        """
        # Direct kwarg override
        if option_name in kwargs and kwargs[option_name]:
            return kwargs[option_name]
        # Plugin option (may be None if not set)
        try:
            val = self.get_option(option_name)
            if val:
                return val
        except KeyError:
            pass
        # Env var fallback
        return os.environ.get(env_var, "")
