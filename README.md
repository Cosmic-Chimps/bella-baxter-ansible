# jjchiw.bella_baxter — Ansible Collection

Ansible collection for [Bella Baxter](https://github.com/cosmic-chimps/bella-baxter) — a secret management gateway that proxies secrets from Vault, AWS Secrets Manager, Azure Key Vault, and other providers through a single unified API.

## Requirements

- Ansible 2.14+
- Python 3.9+
- [`bella-baxter`](https://pypi.org/project/bella-baxter/) Python SDK installed in the Python environment Ansible uses

```bash
pip install --pre bella-baxter
```

> **Why `--pre`.** The SDK ships as `0.1.1-preview.N`, which PEP 440 normalises to `0.1.1rcN` — a
> pre-release. `pip` skips pre-releases unless asked, so omitting `--pre` resolves *no version at
> all* rather than installing an older one. Every release on PyPI is
> currently a pre-release, so `--pre` is the contract until a stable `0.1.x` ships. (npm is
> unaffected: its `latest` dist-tag points at the preview and `npm install` honours it regardless.)

> **Note:** The `bella-baxter` package is the only Python dependency. It includes the Kiota-generated API client — no need for `requests` or other HTTP libraries.

## Installation

```bash
ansible-galaxy collection install jjchiw.bella_baxter
```

Or pin a specific version:

```bash
ansible-galaxy collection install jjchiw.bella_baxter:==0.2.0
```

## Authentication

All plugins authenticate using **API keys** (bax-...). These are issued from the Bella Baxter admin interface.

| Parameter | Environment Variable | Description |
|-----------|---------------------|-------------|
| `baxter_url` | `BELLA_BAXTER_URL` | Base URL of the Baxter API |
| `api_key` | `BELLA_API_KEY` | Bella Baxter API key (`bax-...`) |
| `project_slug` | `BELLA_PROJECT_SLUG` | Project slug (e.g. `my-app`) |
| `environment_slug` | `BELLA_ENV_SLUG` | Environment slug (e.g. `production`) |

---

## Secrets

### Lookup — `jjchiw.bella_baxter.secret`

Reads one or more secret values from Bella Baxter.

```yaml
vars:
  db_password: "{{ lookup('jjchiw.bella_baxter.secret', 'DATABASE_URL',
                    baxter_url='https://baxter.example.com',
                    environment_slug='production',
                    api_key='bax-abc123') }}"
```

### Module — `jjchiw.bella_baxter.secret`

Creates, updates, or deletes secrets. Idempotent (`state: absent` on a non-existent key is a no-op).

```yaml
- name: Store database password
  jjchiw.bella_baxter.secret:
    baxter_url: "{{ baxter_url }}"
    api_key: "{{ bella_api_key }}"
    environment_id: "550e8400-e29b-41d4-a716-446655440000"
    provider_id: "550e8400-e29b-41d4-a716-446655440001"
    key: "DATABASE_PASSWORD"
    value: "{{ generated_password }}"
    state: present
```

See [`examples/read_secrets.yml`](examples/read_secrets.yml) and [`examples/write_secrets.yml`](examples/write_secrets.yml).

---

## SSH Certificate Authority

### Lookup — `jjchiw.bella_baxter.ssh_ca_key`

Fetches the SSH CA public key — write it to `TrustedUserCAKeys` so servers trust certificates signed by Bella.

```yaml
- name: Fetch SSH CA public key
  ansible.builtin.set_fact:
    ssh_ca: "{{ lookup('jjchiw.bella_baxter.ssh_ca_key',
                  project_slug='my-app',
                  environment_slug='production',
                  baxter_url='https://api.bella-baxter.io',
                  api_key='bax-abc123') }}"

- name: Write trusted CA key to server
  ansible.builtin.copy:
    dest: /etc/ssh/trusted-user-ca-keys.pem
    content: "{{ ssh_ca }}\n"
    mode: '0644'
```

### Lookup — `jjchiw.bella_baxter.ssh_sign`

Signs an SSH public key and returns the signed certificate.

```yaml
- name: Sign SSH public key
  ansible.builtin.set_fact:
    signed_cert: "{{ lookup('jjchiw.bella_baxter.ssh_sign',
                      project_slug='my-app',
                      environment_slug='production',
                      role_name='ops-team',
                      public_key=lookup('file', '~/.ssh/id_ed25519.pub'),
                      ttl='1h',
                      baxter_url='https://api.bella-baxter.io',
                      api_key='bax-abc123') }}"

- name: Write signed certificate to disk
  ansible.builtin.copy:
    dest: ~/.ssh/id_ed25519-cert.pub
    content: "{{ signed_cert }}\n"
    mode: '0600'
```

See [`examples/provision_ssh_trust.yml`](examples/provision_ssh_trust.yml) for a complete playbook.

---

## PKI / TLS Certificates

Bella Baxter uses a private Certificate Authority (CA) backed by Vault/OpenBao. These certificates are **for internal use only** — browsers and operating systems won't trust them by default. Distribute the CA certificate to your machines to establish trust.

Typical use case: provision TLS certificates for internal microservices, databases, and APIs without going through Let's Encrypt.

### Module — `jjchiw.bella_baxter.pki_role`

Creates or deletes a PKI role. Roles control which common names can be issued certificates and with what TTL / key constraints.

```yaml
- name: Create PKI role for internal services
  jjchiw.bella_baxter.pki_role:
    baxter_url: "{{ bella_baxter_url }}"
    api_key: "{{ bella_api_key }}"
    project_slug: my-app
    environment_slug: production
    name: internal-services
    allowed_domains: internal,svc.cluster.local
    allow_subdomains: true
    default_ttl: 720h
    max_ttl: 8760h
    state: present
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `baxter_url` | str | Yes | Baxter API base URL |
| `api_key` | str | Yes | API key (**no_log**) |
| `project_slug` | str | Yes | Project slug |
| `environment_slug` | str | Yes | Environment slug |
| `name` | str | Yes | Role name |
| `allowed_domains` | str | No\* | Comma-separated allowed domains. Required when `allow_any_name=false` |
| `allow_subdomains` | bool | No | Allow subdomains (default: `true`) |
| `allow_any_name` | bool | No | Allow any CN (default: `false`) |
| `allow_localhost` | bool | No | Allow `localhost` (default: `false`) |
| `default_ttl` | str | No | Default cert TTL (default: `720h`) |
| `max_ttl` | str | No | Max cert TTL (default: `8760h`) |
| `key_type` | str | No | `rsa` or `ec` (default: `rsa`) |
| `key_bits` | int | No | Key size in bits |
| `state` | str | No | `present` or `absent` (default: `present`) |

### Lookup — `jjchiw.bella_baxter.pki_ca`

Fetches PKI CA information. Returns a dict with `certificate`, `ca_chain`, and `acme_directory_url`.

```yaml
- name: Fetch PKI CA info
  ansible.builtin.set_fact:
    pki_ca: "{{ lookup('jjchiw.bella_baxter.pki_ca',
                  project_slug='my-app',
                  environment_slug='production',
                  baxter_url='https://api.bella-baxter.io',
                  api_key='bax-abc123') }}"

- name: Write CA cert to system trust store
  ansible.builtin.copy:
    dest: /usr/local/share/ca-certificates/bella-ca.crt
    content: "{{ pki_ca.certificate }}"
    mode: '0644'
  notify: Run update-ca-certificates
```

### Lookup — `jjchiw.bella_baxter.pki_issue`

Issues a TLS certificate. Returns a dict with `certificate`, `private_key`, `ca_chain`, `issuing_ca`, `serial_number`, and `expiration`.

> ⚠️ **The `private_key` is sensitive.** Write it with `mode: '0600'` and never log it.

```yaml
- name: Issue TLS certificate
  ansible.builtin.set_fact:
    tls: "{{ lookup('jjchiw.bella_baxter.pki_issue',
               common_name='api.internal',
               role_name='internal-services',
               ttl='720h',
               project_slug='my-app',
               environment_slug='production',
               baxter_url='https://api.bella-baxter.io',
               api_key='bax-abc123') }}"

- name: Write certificate
  ansible.builtin.copy:
    dest: /etc/ssl/certs/api.internal.crt
    content: "{{ tls.certificate }}"
    mode: '0644'

- name: Write private key (restricted!)
  ansible.builtin.copy:
    dest: /etc/ssl/private/api.internal.key
    content: "{{ tls.private_key }}"
    mode: '0600'

- name: Write CA chain
  ansible.builtin.copy:
    dest: /etc/ssl/certs/bella-ca-chain.pem
    content: "{{ tls.ca_chain }}"
    mode: '0644'
```

See [`examples/provision_tls.yml`](examples/provision_tls.yml) for a complete playbook that:
1. Ensures a PKI role exists
2. Issues a TLS certificate per host
3. Writes cert + key + CA chain to the correct paths
4. Distributes the CA to the system trust store
5. Reloads nginx/apache2

---

## Check Mode

The `secret` and `pki_role` modules support `--check` mode — no API calls are made.

```bash
ansible-playbook write_secrets.yml --check
ansible-playbook provision_tls.yml --check
```

## License

MIT


Ansible collection for [Bella Baxter](https://github.com/cosmic-chimps/bella-baxter) — a secret management gateway that proxies secrets from Vault, AWS Secrets Manager, Azure Key Vault, and other providers through a single unified API.

## Requirements

- Ansible 2.14+
- Python 3.9+
- [`bella-baxter`](https://pypi.org/project/bella-baxter/) Python SDK installed in the Python environment Ansible uses

```bash
pip install --pre bella-baxter
```

## Installation

```bash
ansible-galaxy collection install jjchiw.bella_baxter
```

Or pin a specific version:

```bash
ansible-galaxy collection install jjchiw.bella_baxter:==0.1.0
```

## Authentication

All plugins authenticate using **API key pairs** (client ID + client secret). These are issued from the Bella Baxter admin interface.

| Parameter | Environment Variable | Description |
|-----------|---------------------|-------------|
| `baxter_url` | `BELLA_BAXTER_URL` | Base URL of the Baxter API |
| `client_id` | `BELLA_CLIENT_ID` | API key client ID |
| `client_secret` | `BELLA_CLIENT_SECRET` | API key client secret |
| `environment_slug` | `BELLA_ENV_SLUG` | Environment slug (lookup only) |

## Lookup Plugin — `jjchiw.bella_baxter.secret`

Reads one or more secret values from Bella Baxter. Designed for use in `vars:` blocks.

### Parameters (keyword arguments)

| Argument | Env Var | Required | Description |
|----------|---------|----------|-------------|
| `baxter_url` | `BELLA_BAXTER_URL` | Yes | Baxter API base URL |
| `environment_slug` | `BELLA_ENV_SLUG` | Yes | Target environment slug (e.g. `production`) |
| `client_id` | `BELLA_CLIENT_ID` | Yes | API key client ID |
| `client_secret` | `BELLA_CLIENT_SECRET` | Yes | API key client secret |

### Usage

**Inline parameters:**
```yaml
vars:
  db_password: "{{ lookup('jjchiw.bella_baxter.secret', 'DATABASE_URL',
                    baxter_url='https://baxter.example.com',
                    environment_slug='production',
                    client_id='bella_ak_abc123',
                    client_secret='s3cr3t') }}"
```

**Using environment variables (recommended for CI/CD):**
```bash
export BELLA_BAXTER_URL=https://baxter.example.com
export BELLA_CLIENT_ID=bella_ak_abc123
export BELLA_CLIENT_SECRET=s3cr3t
export BELLA_ENV_SLUG=production
```
```yaml
vars:
  db_password: "{{ lookup('jjchiw.bella_baxter.secret', 'DATABASE_URL') }}"
```

**Multiple secrets at once:**
```yaml
vars:
  credentials: "{{ lookup('jjchiw.bella_baxter.secret',
                    'DB_HOST', 'DB_PORT', 'DB_NAME',
                    environment_slug='production') }}"
  # credentials == ['db.prod.internal', '5432', 'myapp']
```

**Using `with_` loop syntax:**
```yaml
- name: Print each secret
  debug:
    msg: "{{ item }}"
  with_jjchiw.bella_baxter.secret:
    - DATABASE_URL
    - REDIS_URL
```

See [`examples/read_secrets.yml`](examples/read_secrets.yml) for a complete playbook.

## Module — `jjchiw.bella_baxter.secret`

Creates, updates, or deletes secrets in Bella Baxter. Idempotent: `state: absent` on a non-existent key is a no-op.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `baxter_url` | str | Yes | Baxter API base URL |
| `client_id` | str | Yes | API key client ID (**no_log**) |
| `client_secret` | str | Yes | API key client secret (**no_log**) |
| `environment_id` | str | Yes | Target environment UUID |
| `provider_id` | str | Yes | Target provider UUID |
| `key` | str | Yes | Secret key name |
| `value` | str | No\* | Secret value (**no_log**). Required when `state=present` |
| `description` | str | No | Human-readable description |
| `state` | str | No | `present` (default) or `absent` |

### Return values

| Key | Type | Description |
|-----|------|-------------|
| `changed` | bool | Whether the module made a change |
| `key` | str | The secret key that was operated on |
| `state` | str | Final state (`present` or `absent`) |

### Usage

```yaml
- name: Store database password
  jjchiw.bella_baxter.secret:
    baxter_url: "{{ baxter_url }}"
    client_id: "{{ bella_client_id }}"
    client_secret: "{{ bella_client_secret }}"
    environment_id: "550e8400-e29b-41d4-a716-446655440000"
    provider_id: "550e8400-e29b-41d4-a716-446655440001"
    key: "DATABASE_PASSWORD"
    value: "{{ generated_password }}"
    description: "Production database master password"
    state: present

- name: Remove deprecated secret
  jjchiw.bella_baxter.secret:
    baxter_url: "{{ baxter_url }}"
    client_id: "{{ bella_client_id }}"
    client_secret: "{{ bella_client_secret }}"
    environment_id: "550e8400-e29b-41d4-a716-446655440000"
    provider_id: "550e8400-e29b-41d4-a716-446655440001"
    key: "OLD_API_KEY"
    state: absent
```

See [`examples/write_secrets.yml`](examples/write_secrets.yml) for a complete playbook.

## Check Mode

The `secret` module supports `--check` mode. In check mode it reports what it *would* do without making any API calls.

```bash
ansible-playbook write_secrets.yml --check
```

## License

MIT
