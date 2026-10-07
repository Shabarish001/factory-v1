# Tests

The tests verify Factory's structure and executable tools. They use Python's standard `unittest` module; search, page-fetch, and research tests inject mocked providers/transports and do not contact sibling repositories or external services.

Run:

```text
python -m unittest discover -s tests -v
```

Live Tavily access is optional and intentionally excluded from the required suite.
