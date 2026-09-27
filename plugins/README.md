# JARVIS Plugins

Drop a plugin folder here. Each plugin is a folder containing:

- `plugin.yaml` — manifest:

```yaml
name: myplugin            # required; tools are registered as myplugin.<tool>
version: 1.0.0
description: What it does.
tools:
  - name: dothing         # required
    description: Does the thing.
    risk: low             # low | medium | high (anything else → medium)
    needs_network: false
    handler: "mymodule:my_function"   # <file>.py : <callable>
    schema:               # argument hints
      arg1: "string"
```

- `<module>.py` — the handler module. Each handler takes a dict and returns a
  dict, and must NEVER raise (return `{"error": "..."}` on failure).

Broken plugins are skipped with a warning — they can never crash JARVIS.
Use the `plugins.list` / `plugins.reload` tools to inspect and re-scan.
Shipped examples live in `sidecar/jarvis/tools/plugins/examples/`.
