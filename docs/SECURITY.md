# Security policy

Thank you for helping keep Autowright and its users safe. This page explains
which versions receive security fixes, how to report a vulnerability privately,
and what to expect after you do.

## Supported versions

Autowright is an early release under active development. Security fixes ship in
the latest release only, delivered through the app's built-in updater and the
normal release channels.

| Version        | Supported          |
| -------------- | ------------------ |
| Latest release | Yes                |
| Older releases | No, please update  |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Report vulnerabilities privately through GitHub's private vulnerability
reporting:

<https://github.com/hansololz/autowright/security/advisories/new>

This opens a private draft advisory that only the maintainer can see. If you
cannot use that form, open a public issue that says only "security report,
please reach out" with no details, and the maintainer will contact you.

Please include, as far as you can:

- The Autowright version (shown in the footer of the menu bar panel, or the
  `release/VERSION` file for source builds) and your operating system.
- Steps to reproduce, or a proof of concept.
- The impact you believe it has (for example: secret exposure, code execution
  outside an automation, bypass of a permission prompt).
- Whether you would like to be credited in the advisory, and under what name.

## What to expect

- Acknowledgment within 7 days of your report.
- Regular updates while the issue is investigated and fixed.
- A fix released as a normal Autowright update, followed by a published
  advisory that credits you if you want to be credited.

We ask that you give us a reasonable window to release a fix before disclosing
the issue publicly, and that you avoid accessing or modifying data that is not
yours while testing.

## What is in scope

Autowright runs locally on your machine and executes automations that you
wrote or installed yourself, using AI agents you connected with your own
accounts. Some behaviors that look alarming are how the product is designed to
work. The following are **not** vulnerabilities on their own:

- An automation you created runs commands, reads files, or makes network
  requests on your machine. Automations are local code that you authored or
  installed; they run with your user's permissions by design.
- Drafting context (your description, the automation's spec, build
  instructions, and related step code) is sent to the AI agent you connected.
  See [PRIVACY.md](PRIVACY.md) for exactly what leaves the machine and how to
  turn each connection off.
- Secret values are visible to a running automation's steps. Secrets are
  stored in the OS secret store and injected only at execution time; the steps
  you wrote are meant to use them.

The following **are** in scope and we want to hear about them:

- Secret values leaking into scripts, logs, exports, transfer archives, chat
  history, or the UI where they are not supposed to appear.
- Code execution or file access triggered by something other than an
  automation the user chose to run (for example, via a crafted transfer
  archive, YAML file, update feed, or Discord message).
- Bypassing a permission or confirmation the app is supposed to ask for.
- Weaknesses in the updater, the local backend service, the CLI shim, or the
  way the desktop app talks to the backend.
- Any way for one local user to read or run another local user's automations
  or secrets.

If you are unsure whether something counts, report it privately anyway and we
will figure it out together.
