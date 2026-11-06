# mechanicalai

The official Python library for the Mechanical AI API.

```bash
pip install mechanicalai
```

The client is under development. Nothing here calls the API yet; the package holds the name,
the version and the shape of what is coming:

```python
import mechanicalai

client = mechanicalai.Client()  # reads MECHANICALAI_API_KEY
run = client.runs.create(problem="Design a gripper for a 2 kg box, 300 mm reach.", files=["plate.step"], budget=40)
run.wait(on_progress=print)
run.results.download("./out")
```

## License

Apache-2.0. See [LICENSE](LICENSE).
