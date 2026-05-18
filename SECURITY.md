# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.6.x   | :white_check_mark: |
| 0.5.x   | :white_check_mark: |
| < 0.5   | :x:                |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report vulnerabilities privately via GitHub's [Security Advisory](https://github.com/caua-ferreira/permafrost-framework/security/advisories/new) feature, or email:

**caua.fer@gmail.com**

Include in your report:
- Description of the vulnerability and affected component
- Steps to reproduce (proof-of-concept if possible)
- Potential impact assessment
- Suggested fix or mitigations (optional)

### Response timeline

| Action              | Target SLA |
| ------------------- | ---------- |
| Initial response    | 72 hours   |
| Triage & assessment | 5 days     |
| Patch for critical  | 7 days     |
| Patch for high      | 14 days    |
| Patch for medium/low| 30 days    |

### Scope

In scope:
- Arbitrary code execution via crafted `.permafrost` files
- Path traversal in file read/write operations
- Cryptographic weaknesses in the encryption layer (`permafrost.crypto`)
- Dependency confusion or supply chain issues

Out of scope:
- Vulnerabilities in optional cloud dependencies (boto3, azure-storage-blob, google-cloud-storage) — report those upstream
- Performance degradation without security impact

## Disclosure Policy

We follow [coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure). Once a fix is released, we will publish a CVE and a GitHub Security Advisory crediting the reporter (unless anonymity is requested).
