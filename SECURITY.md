# Security

## Reporting a vulnerability

Email **security@mechanicalai.com**. Do not open a public issue for a vulnerability, in this library or in the API.

Include what you found, how to reproduce it and the version. We acknowledge within three business days, keep you
informed while we fix it, and credit you in the release notes if you wish.

## Scope

This library and the `mechanicalai` command. A problem in the API itself or in the console is also in scope for the
same address.

## Supported versions

The latest release on PyPI receives fixes. Earlier releases do not.

## What the library does with your key

The key is sent only as `Authorization: Bearer` to the configured `base_url`. It is never sent to the signed upload and
download URLs on other hosts, never written to disk and never logged (`MECHANICALAI_LOG=debug` redacts it).
