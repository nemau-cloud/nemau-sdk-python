# Contributing

Bug reports, questions and pull requests are welcome on GitHub:
https://github.com/nemau-cloud/nemau-sdk-python

Do not report a security vulnerability in a public issue. See
[SECURITY.md](SECURITY.md).

## Reporting a problem

Open an issue at https://github.com/nemau-cloud/nemau-sdk-python/issues. Include
the SDK version (`nemau.__version__`), the Python version, the call you made
and the error you got. For a failed task, include the task id from the
`NemauTaskFailedError` message.

Remove credentials, tokens, kubeconfigs and presigned URLs from anything you
post.

## Proposing a change

For a small fix, open a pull request against `main`. For a new feature or a
change to the public API, open an issue first so the change can be discussed
before you write it.

A pull request should:

- include tests for the change;
- pass the checks described below;
- add an entry to [CHANGELOG.md](CHANGELOG.md) under `## [Unreleased]`, in an
  `Added`, `Changed`, `Fixed` or `Removed` section, when users of the package
  can see the change;
- update the [README](README.md) or the pages in [docs](docs/README.md) when
  the change affects what users see.

## Running the tests and the linters

The tests use `unittest` and a local HTTP stub (`tests/helpers.py`), so they
need no network access and no credentials. They import the package from the
working tree, so there is nothing to install:

```sh
python3 -m unittest discover -s tests -t .
```

The CI also runs ruff and mypy, at these versions:

```sh
python3 -m pip install ruff==0.16.8 mypy==1.19.1
ruff check .
ruff format --check .
mypy nemau
```

`ruff format .` fixes the formatting. The CI runs the tests on Python 3.9,
3.10, 3.11, 3.12 and 3.13, so the code must run on Python 3.9.

## No runtime dependencies

The package uses only the Python standard library, and `dependencies` in
`pyproject.toml` stays empty. A change that needs a third-party package at
runtime will not be accepted. Development tools such as ruff and mypy are not
dependencies of the package.

## Commit messages

Commit messages follow the Conventional Commits format, in English:

```text
type(scope): summary
```

The type is one of `feat`, `fix`, `docs`, `test`, `refactor`, `build` or `ci`.
The scope is optional and names the part of the SDK concerned, such as
`storage`, `network` or `kubernetes`. For example:

```text
feat(kubernetes): add upgrade_cluster
docs: describe the retry policy
```
