# Security policy

## Reporting a problem

Please do not report security problems in public issues.

Report them privately through GitHub instead:
**[Report a vulnerability](https://github.com/ihsonnet/imagine-reader/security/advisories/new)**
(under the repository's **Security** tab). Include what you found, how to
reproduce it, and what someone could do with it.

You will get a reply within a week. Once the problem is fixed, it is
disclosed in a security advisory, with credit to you if you would like it.

## What is in scope

- The engine, the server, and the reader in this repository.
- The public demo at [imagine-reader.ihsonnet.com](https://imagine-reader.ihsonnet.com), when the problem
  is in this code. Do not attack the demo's availability while testing.

Areas that matter most:

- Uploaded PDFs: parsing runs in a separate process with time and memory
  limits.
- The sample pack: it is downloaded on first start, checked against the
  sample PDFs (and its SHA-256, when set), and only files of the expected
  names and types are installed.
- The AI keys in `.env`: each must go only to its own service, never into
  the image, the reader in the browser, or a log.
- A demo with uploads off (`IR_UPLOADS=false`): nobody should be able to add
  a book, or make the server spend its keys beyond preparing the samples.
- The daily spending limit.

An install with uploads on is meant for one person on their own computer: it
has no accounts, so anyone who can reach it can read and delete its books.
That is by design, not a vulnerability; the Docker setup binds it to
`127.0.0.1`.

## Supported versions

Imagine-Reader is in early preview. Only the latest code on `main` is
supported.
