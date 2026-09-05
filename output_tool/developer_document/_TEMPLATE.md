# Developer Document Template

Use this template for app-state Developer Documents under `_asset/`. Replace placeholders with evidenced app facts; do not invent unsupported surfaces, schemas, or lifecycle behavior.

## Document Rules

- Keep the top-level order fixed: `App Identity`, `Backing Resources`, `DB Entities`, `SharedPreferences`, `Providers`, `Files`, then optional app-specific extensions.
- Keep the subsection order fixed under every state surface: `Primary`, `Secondary`, `Auxiliary`, `Access Semantics`, `Access Procedure`, `Consistency / Recovery`.
- Use `Entity | Backing representation | Fields` for state-model tables. For providers or files, a more specific middle-column label such as `Provider/URI` or `Path` is allowed.
- Keep an unavailable surface explicit with `None` rows and a concise boundary. Do not infer a storage or access surface from UI text alone.
- Include table names, columns, preference keys, provider URIs, paths, value domains, and encodings when evidenced; these are state-interface facts rather than implementation specification.
- Avoid implementation-level details where possible, such as helper/function names, temporary paths, subprocess wrappers, sleep durations, retry counts, UI coordinates/resource ids, evaluator quirks, result-payload formatting, and test-only logging. In particular, keep temporary-path and subprocess-wrapper details to the minimum needed to explain a state-interface boundary. Do not include device-side readback recipes, GUI refresh instructions, post-launch verification, or bounded retry policy.
- Within `Access Procedure`, keep applicable bullets in this order: `Surface selection`, `Initialization`, `Runtime discovery`, `Read procedure`, `Write procedure`, `Delete procedure`, `Process/file handling`.
- Within `Consistency / Recovery`, keep applicable bullets in this order: `Snapshot consistency`, `Transaction consistency`, `Relationship consistency`, `Cross-surface consistency`, `Process consistency`, `Recovery boundary`.

## Classification Guide

### Primary

State entities directly modeled by the documented interface.

### Secondary

Entities owned by, contained in, or directly related to a primary entity.

### Auxiliary

Derived state, metadata, caches, history, side records, sync state, or file references that support but do not define the primary entity.

### Access Semantics

Describes what state representations mean. Prefer applicable bullets in this order:

1. `Source of truth`
2. `Entity semantics`
3. `Selector semantics`
4. `Value encoding`
5. `Relationship invariants`
6. `Boundary`

### Access Procedure

Describes how a generated interface may access or mutate the state:

1. `Surface selection`
2. `Initialization`
3. `Runtime discovery`
4. `Read procedure`
5. `Write procedure`
6. `Delete procedure`
7. `Process/file handling`

`Write procedure` covers create and update behavior. `Delete procedure` separately defines selector resolution, hard/soft deletion, cascade/dependent-row behavior, and destructive-operation boundaries.

### Consistency / Recovery

Describes invariants that prevent corruption or partial state, not how to verify a completed operation:

1. `Snapshot consistency`
2. `Transaction consistency`
3. `Relationship consistency`
4. `Cross-surface consistency`
5. `Process consistency`
6. `Recovery boundary`

## Canonical Skeleton

```md
# <App Name> Developer Document

## App Identity
- Package name: `<package>`
- Package fallback: `<alternate package when evidenced>`
- Main app data root: `<path>`
- State summary: <persistent-state summary>

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `<path>` | <role> |
| SharedPreferences XML | `<path>` | <role> |
| ContentProvider/service | `<URI or service>` | <role> |
| Directory/file | `<path>` | <role> |

## DB Entities

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `<entity>` | `<database>; table <table>` | `<fields>` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `<entity or None>` | `<representation>` | `<fields>` |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `<entity or None>` | `<representation>` | `<fields>` |

### Access Semantics
- Source of truth: ...
- Entity semantics: ...
- Selector semantics: ...
- Value encoding: ...
- Relationship invariants: ...
- Boundary: ...

### Access Procedure
- Surface selection: ...
- Initialization: ...
- Runtime discovery: ...
- Read procedure: ...
- Write procedure: ...
- Delete procedure: ...
- Process/file handling: ...

### Consistency / Recovery
- Snapshot consistency: ...
- Transaction consistency: ...
- Relationship consistency: ...
- Cross-surface consistency: ...
- Process consistency: ...
- Recovery boundary: ...

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `<setting entity>` | `<XML>; key <key>` | `<value type/domain>` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `<entity or None>` | `<representation>` | `<fields>` |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `<entity or None>` | `<representation>` | `<fields>` |

### Access Semantics
- Source of truth: ...
- Entity semantics: ...
- Selector semantics: ...
- Value encoding: ...
- Relationship invariants: ...
- Boundary: ...

### Access Procedure
- Surface selection: ...
- Initialization: ...
- Runtime discovery: ...
- Read procedure: ...
- Write procedure: ...
- Delete procedure: ...
- Process/file handling: ...

### Consistency / Recovery
- Snapshot consistency: ...
- Transaction consistency: ...
- Relationship consistency: ...
- Cross-surface consistency: ...
- Process consistency: ...
- Recovery boundary: ...

## Providers

### Primary
| Entity | Provider/URI | Fields |
|---|---|---|
| `<entity>` | `<provider URI or service>` | `<fields>` |

### Secondary
| Entity | Provider/URI | Fields |
|---|---|---|
| `<entity or None>` | `<provider URI or service>` | `<fields>` |

### Auxiliary
| Entity | Provider/URI | Fields |
|---|---|---|
| `<entity or None>` | `<provider URI or service>` | `<fields>` |

### Access Semantics
- Source of truth: ...
- Entity semantics: ...
- Selector semantics: ...
- Value encoding: ...
- Relationship invariants: ...
- Boundary: ...

### Access Procedure
- Surface selection: ...
- Initialization: ...
- Runtime discovery: ...
- Read procedure: ...
- Write procedure: ...
- Delete procedure: ...
- Process/file handling: ...

### Consistency / Recovery
- Snapshot consistency: ...
- Transaction consistency: ...
- Relationship consistency: ...
- Cross-surface consistency: ...
- Process consistency: ...
- Recovery boundary: ...

## Files

### Primary
| Entity | Path | Fields |
|---|---|---|
| `<entity>` | `<path>` | `<fields>` |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| `<entity or None>` | `<path>` | `<fields>` |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| `<entity or None>` | `<path>` | `<fields>` |

### Access Semantics
- Source of truth: ...
- Entity semantics: ...
- Selector semantics: ...
- Value encoding: ...
- Relationship invariants: ...
- Boundary: ...

### Access Procedure
- Surface selection: ...
- Initialization: ...
- Runtime discovery: ...
- Read procedure: ...
- Write procedure: ...
- Delete procedure: ...
- Process/file handling: ...

### Consistency / Recovery
- Snapshot consistency: ...
- Transaction consistency: ...
- Relationship consistency: ...
- Cross-surface consistency: ...
- Process consistency: ...
- Recovery boundary: ...
```

Omit inapplicable bullets inside a subsection, but keep every subsection heading. For an unavailable surface, use a `None` entity row and concise `Not applicable` or `Boundary` statements rather than fabricating behavior.
