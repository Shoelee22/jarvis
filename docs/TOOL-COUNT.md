# Dynamic tool count (Phase 7)

Every dynamic provider owns one dotted namespace and materializes tools
lazily: `expand()` returns the *exact* addressable count without building
objects, and `resolve(name)` builds exactly one `Tool` on demand. The total
below is the sum of `expand()` over all providers
(`DynamicRegistry.count_addressable()`).

## Per-provider math (real numbers, measured 2026-09-27)

| Namespace | Formula | Current value |
|---|---|---|
| `convert` (units) | N×(N−1) ordered pairs, N = compiled units | 410×409 = **167,690** |
| `web` (web actions) | PAIR_COUNT = Σ actions per site | **308** |
| `joke` | 1 (`joke.random`) + 10 categories + J jokes (`joke.<id>`) | 1+10+260 = **271** |
| `automation` | 5 static (create/run/list/describe/delete) + R routines (`automation.run.<name>`) | 5 + R (R = live routine count) |
| `app` | 2 × A (open + focus per discovered app) | 2A (A = live app count; 2 in this env → 4) |
| `knowledge` | `knowledge.add_text` + `knowledge.ask` | **2** |
| `contact` | C × 5 channels × 30 templates | 150C (C = live contact count) |
| `say` | one tool per TTS voice language | **12** (en hi hinglish ta te bn mr es fr de pt ar) |
| `greet` | one tool per greeting language | **12** |
| `opinion` | `opinion.get` | **1** |

## Total

Measured on this machine (2026-09-27, R=0, A=2, C=0):

```
167,690 (convert) + 308 (web) + 271 (joke) + 5 (automation)
  + 4 (app) + 2 (knowledge) + 0 (contact) + 12 (say) + 12 (greet) + 1 (opinion)
= 168,305
```

## Re-measured after Phase 17 (2026-09-27)

New dynamic namespaces landed (tz, color, encode, decode, math, gen, say/greet
grew to 60 each). Live measurement:

```
>>> from jarvis.tools.dynamic import build_dynamic_registry
>>> r = build_dynamic_registry(None, "/tmp/dyn")
>>> r.count_addressable()
440346
```

Per-namespace: convert 167,690; tz 247,506; color 23,256; web 621; joke 1,011;
say 60; greet 60; math 100; encode 12; decode 12; gen 6; automation 5; app 4;
knowledge 2; contact 0; opinion 1.

Static tools: **292** (was 271 before Phase 18; +21 from projects/schedules/
meetings/security packs). **Total addressable: 440,346 + 292 = 440,638**.

Verified live:

```
>>> from jarvis.tools.dynamic import build_dynamic_registry
>>> r = build_dynamic_registry(None, "/tmp/dyn")
>>> r.count_addressable()
168305
>>> r.list_namespaces()
{"convert": 167690, "automation": 5, "contact": 0, "app": 4, "web": 308,
 "joke": 271, "say": 12, "greet": 12, "opinion": 1, "knowledge": 2}
```

## Worked example: `convert.kilometer_to_mile` end-to-end

```python
from jarvis.tools.dynamic import build_dynamic_registry

r = build_dynamic_registry(None, "/tmp/dyn")
r.count_addressable()                       # 168305

tool = r.resolve("convert.kilometer_to_mile")
tool.name                                   # "convert.kilometer_to_mile"
tool.risk                                   # "low"

r.call("convert.kilometer_to_mile", {"value": 1})
# {"ok": True,
#  "result": {"value": 1, "from": "kilometer", "to": "mile",
#             "result": 0.621371192237},
#  "untrusted": True}

r.call("does.not.exist", {})
# {"ok": False, "error": "tool 'does.not.exist' not registered"}
```

Resolution splits on the first dot → the `convert` provider → its
`resolve()` parses `kilometer_to_mile` against the unit table and returns one
`Tool`. Nothing else is built. `call()` mirrors the main registry's policy
locally (high-risk tools need `confirmed=True`) and marks outputs
`untrusted` — tool output is data, never instructions.

## Honesty notes

- **Cross-dimension pairs are counted but fail honestly.** `convert` counts
  every ordered pair (167,690 names), including e.g.
  `convert.kilometer_to_kilogram`. Invoking one returns a dimension-mismatch
  *error dict*, not a number:
  `{"error": "cannot convert kilometer (length) to gram (mass): incompatible
  dimensions"}`. The count is addressable names, not successful conversions.
- **Contacts and apps vary with user data.** `contact` counts 150 per live
  contact (5 channels × 30 message templates); `app` counts 2 per installed
  app found by the desktop scan. Both read live state at `expand()` time, so
  the total moves as contacts/apps change — on a machine with 0 contacts it
  contributes 0.
- **Automation routines are live too.** `automation` contributes 5 static
  tools plus one `automation.run.<name>` shortcut per stored routine; the
  count changes when routines are created/deleted.
- **No currency units.** Exchange rates move, so `convert` covers only
  static physical units (length, mass, volume, time, temperature, etc.).
