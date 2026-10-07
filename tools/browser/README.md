# Browser adapter

Factory uses Python Playwright with Chromium for deterministic browser and UI checks. Python was chosen because Factory's inspector, runner, artifact recorder, and test suite already use Python. This avoids a second runtime wrapper and lets browser results flow directly into the active run recorder.

## Setup

From the Factory root:

```text
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

The first command installs the Python API. The second installs Playwright's managed Chromium runtime. The browser binary remains outside the repository.

## Interface

```python
from tools.artifacts import RunArtifactRecorder
from tools.browser import run_browser_test

actions = [
    {"action": "expect_visible", "selector": "h1", "text": "Welcome"},
    {"action": "click", "selector": "[data-testid='book-demo']"},
    {"action": "expect_url", "url": "/booked"},
    {"action": "screenshot", "name": "booked"},
]

with RunArtifactRecorder(factory_root, target_path) as run:
    result = run_browser_test("http://127.0.0.1:3000/", actions, recorder=run)
```

Supported actions are `wait_for_selector`, `expect_visible`, `read_text`, `click`, `fill`, `expect_url`, and `screenshot`. Selectors and actions are explicit. There is no AI-driven element selection or arbitrary JavaScript evaluation.

## Safety boundary

- Initial and subsequent HTTP requests are restricted to localhost or loopback IPs.
- `file://`, embedded URL credentials, public hosts, unsupported schemes, and external requests are blocked.
- Each action has a bounded timeout.
- Console and page errors are redacted, bounded, and count-limited.
- Fill values are never recorded. Credential-like fields cannot be filled.
- The browser uses an ephemeral context and does not save cookies or storage state.
- Screenshots are explicit and stored only under the active Factory artifact run.
- The tool expects an already-running app. It does not start a server or weaken command-runner policy.

The controlled runner may later start a detected, policy-approved development command as a separate process-lifecycle capability. That behavior is not part of this browser tool.
