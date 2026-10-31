# Beta verification

Install the project in an isolated Python environment, including its optional
HTTP, browser and AI adapter test dependencies:

```sh
python -m pip install -e '.[web,web-test,openai]'
python -m playwright install chromium
python -m unittest discover -s tests -v
```

On Linux, use `python -m playwright install --with-deps chromium` to install
browser system dependencies as well. Browser tests use system Chromium when
available, otherwise the browser downloaded by Playwright. AI tests use test
clients and mocked provider responses; no real API key is required.

The checked-in GitHub Actions workflow runs engine, HTTP and real-browser tests
on Linux and Windows using Python 3.12. It takes effect when pushed; adding the
workflow locally does not establish a successful hosted run.

The optional HTTPS deployment drill needs Caddy and OpenSSL on PATH (or
`BADBADGER_TEST_CADDY` pointing to Caddy). Without them, that one test is skipped.
See deploy/README.md for the restore drill and its deployment limits.

One identity security test creates a symbolic link that points outside its
allowed directory. On Windows it skips only when the OS reports missing symlink
creation privileges (WinError 1314); other errors fail the test. Absolute-path,
parent-directory, duplicate-identity and invalid-personality checks run
independently. Linux CI exercises the symbolic-link rejection without requiring
Windows permission changes.
