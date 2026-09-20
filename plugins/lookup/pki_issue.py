# -*- coding: utf-8 -*-
# Copyright (c) 2025 Cosmic Chimps <hello@cosmicchimps.io>
# MIT License

"""
Bella Baxter — ``pki_issue`` lookup plugin.

Issues a TLS certificate from a Bella Baxter PKI environment.
Returns a dict containing the certificate, private key, CA chain, and
metadata — everything you need to configure a service for TLS.

Requires the ``bella-baxter`` Python package::

    pip install --pre bella-baxter
"""

from __future__ import annotations

DOCUMENTATION = r"""
name: pki_issue
short_description: Issue a TLS certificate via Bella Baxter PKI
description:
  - Issues a TLS certificate from the Bella Baxter PKI for a given common name.
  - Returns a dict containing C(certificate), C(private_key), C(ca_chain),
    C(serial_number), and C(expiration).
  - The private key is sensitive — treat it as a secret and write it with
    restricted permissions (C(mode='0600')).
author:
  - Cosmic Chimps <hello@cosmicchimps.io>
version_added: "0.2.0"
requirements:
  - bella-baxter (Python package, C(pip install --pre bella-baxter))
options:
  common_name:
    description:
      - Requested Common Name (CN) for the certificate (e.g. C(api.internal)).
      - Must match the C(allowed_domains) of the PKI role.
    required: true
    type: str
  role_name:
    description:
      - Name of the PKI role to use for issuance.
    required: true
    type: str
  ttl:
    description:
      - Requested certificate TTL (e.g. C(720h), C(30d)). Capped at role max.
    type: str
  alt_names:
    description:
      - Comma-separated Subject Alternative Names (SANs) to include.
    type: str
  ip_sans:
    description:
      - Comma-separated IP SANs to include (e.g. C(192.168.1.10)).
    type: str
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
- name: Issue TLS certificate for api.internal
  ansible.builtin.set_fact:
    tls_cert: "{{ lookup('jjchiw.bella_baxter.pki_issue',
                    common_name='api.internal',
                    role_name='internal-services',
                    ttl='720h',
                    project_slug='my-app',
                    environment_slug='production',
                    baxter_url='https://api.bella-baxter.io',
                    api_key='bax-abc123') }}"

- name: Write certificate to disk
  ansible.builtin.copy:
    dest: /etc/ssl/certs/api.internal.crt
    content: "{{ tls_cert.certificate }}"
    owner: root
    group: root
    mode: '0644'

- name: Write private key to disk (restricted permissions!)
  ansible.builtin.copy:
    dest: /etc/ssl/private/api.internal.key
    content: "{{ tls_cert.private_key }}"
    owner: root
    group: root
    mode: '0600'

- name: Write CA chain to disk
  ansible.builtin.copy:
    dest: /etc/ssl/certs/bella-ca-chain.pem
    content: "{{ tls_cert.ca_chain }}"
    owner: root
    group: root
    mode: '0644'
"""

RETURN = r"""
_raw:
  description:
    - List with a single item — a dict with the issued certificate details.
  type: list
  elements: dict
  contains:
    certificate:
      description: PEM-encoded leaf (server/client) certificate.
      type: str
    private_key:
      description: PEM-encoded private key. Treat as a secret.
      type: str
    ca_chain:
      description: Full PEM chain of issuing CAs (newline-joined).
      type: str
    issuing_ca:
      description: PEM-encoded immediate issuing CA certificate.
      type: str
    serial_number:
      description: Certificate serial number (Vault format, e.g. "1a:2b:...").
      type: str
    expiration:
      description: Unix timestamp of certificate expiration, or null.
      type: int
"""

import os

from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase
from ansible.utils.display import Display

display = Display()

try:
    from bella_baxter import BaxterClient, BaxterClientOptions, PkiIssueCertificateRequest
except ImportError as _exc:  # pragma: no cover
    raise AnsibleError(
        "The 'bella-baxter' Python package is required for this lookup plugin. "
        "Install it with: pip install --pre bella-baxter"
    ) from _exc


class LookupModule(LookupBase):
    """Lookup plugin that issues a TLS certificate from Bella Baxter PKI."""

    def run(self, terms: list, variables=None, **kwargs) -> list[dict]:  # type: ignore[override]
        self.set_options(var_options=variables, direct=kwargs)

        baxter_url = self._resolve("baxter_url", "BELLA_BAXTER_URL", kwargs)
        api_key = self._resolve("api_key", "BELLA_API_KEY", kwargs)
        project_slug = kwargs.get("project_slug") or self._resolve("project_slug", "BELLA_PROJECT_SLUG", kwargs)
        environment_slug = kwargs.get("environment_slug") or self._resolve("environment_slug", "BELLA_ENV_SLUG", kwargs)

        common_name = kwargs.get("common_name") or ""
        role_name = kwargs.get("role_name") or ""
        ttl = kwargs.get("ttl") or None
        alt_names = kwargs.get("alt_names") or None
        ip_sans = kwargs.get("ip_sans") or None

        for name, val in [
            ("project_slug", project_slug),
            ("environment_slug", environment_slug),
            ("common_name", common_name),
            ("role_name", role_name),
        ]:
            if not val:
                raise AnsibleError(f"{name} is required for the pki_issue lookup plugin.")

        if not baxter_url:
            raise AnsibleError("baxter_url is required (option or BELLA_BAXTER_URL env var).")
        if not api_key:
            raise AnsibleError("api_key is required (option or BELLA_API_KEY env var).")

        display.vvv(
            f"[jjchiw.bella_baxter.pki_issue] Issuing certificate for cn='{common_name}' "
            f"role='{role_name}' project_slug={project_slug} environment_slug={environment_slug}"
        )

        try:
            client = BaxterClient(BaxterClientOptions(baxter_url=baxter_url, api_key=api_key))
            body = PkiIssueCertificateRequest(
                common_name=common_name,
                role_name=role_name,
                ttl=ttl,
                alt_names=alt_names,
                ip_sans=ip_sans,
            )
            cert = client.issue_pki_certificate(body, project_slug, environment_slug)
        except Exception as exc:
            raise AnsibleError(
                f"[jjchiw.bella_baxter.pki_issue] Failed to issue certificate: {exc}"
            ) from exc

        display.vvv(f"[jjchiw.bella_baxter.pki_issue] Certificate issued (serial={cert.serial_number})")
        return [{
            "certificate": cert.certificate,
            "private_key": cert.private_key,
            "ca_chain": cert.ca_chain,
            "issuing_ca": cert.issuing_ca,
            "serial_number": cert.serial_number,
            "expiration": cert.expiration,
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
