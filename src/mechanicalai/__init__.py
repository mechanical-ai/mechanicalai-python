"""The official Python library for the Mechanical AI API.

The client is under development. The surface will be:

    import mechanicalai

    client = mechanicalai.Client()  # reads MECHANICALAI_API_KEY
    run = client.runs.create(problem="...", files=[...], budget=40)
    run.wait(on_progress=print)
    run.results.download("./out")
"""

from mechanicalai._client import Client

__version__ = "0.0.2"
__all__ = ["Client", "__version__"]
