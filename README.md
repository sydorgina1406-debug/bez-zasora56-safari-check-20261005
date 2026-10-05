# Safari check for bez-zasora56.ru

An owner-authorized test snapshot of the built public website. This repository
does not deploy the production website. No private profile files are included.

GitHub Actions runs Selenium against the actual Apple Safari browser on a
temporary standard macOS runner. The test report records browser capabilities,
viewport measurements, interactions and screenshots.

Narrow layouts rendered inside desktop Safari are responsive-layout tests,
not tests on a physical iPhone or on iOS Safari. Production hosting speed and
phone calls (tel: links are checked, never dialed) are outside this check.

Run: `python -m pip install -r requirements.txt`, then
`python tests/check_safari.py` on a Mac with Safari WebDriver enabled.

The website snapshot lives in `site/`. Reports are retained in the Actions
artifact for one day and downloaded locally by the owner.
