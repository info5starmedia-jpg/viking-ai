import os
import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-mark API smoke tests as integration.

    This keeps `pytest` green on servers without API keys / quota.
    Run integration tests explicitly with: `pytest -m integration`.
    """
    integration_names = {"test_openai.py", "test_gemini.py"}

    for item in items:
        path = str(getattr(item, "fspath", ""))
        base = os.path.basename(path)
        if base in integration_names:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def _skip_integration_if_no_keys(request):
    if request.node.get_closest_marker("integration") is None:
        return

    # OpenAI
    if request.node.fspath.basename == "test_openai.py":
        if not (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY")):
            pytest.skip("OPENAI_API_KEY not set")

    # Gemini
    if request.node.fspath.basename == "test_gemini.py":
        if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
            pytest.skip("GOOGLE_API_KEY/GEMINI_API_KEY not set")
