# ProExpense Developer Document

## App Identity
- Package name: `com.arduia.expense`
- Main app data root: `/data/data/com.arduia.expense`
- State summary: expense data is stored in an app-private SQLite database.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/com.arduia.expense/databases/accounting.db` | Common authoritative expense store |
| SQLite DB directory | `/data/data/com.arduia.expense/databases/` | Discover the initialized expense DB here; ignore obvious work/google helper DBs unless schema evidence says otherwise |
| SQLite DB directory | `/data/data/com.arduia.expense/no_backup` | Alternate app-private DB location used by some builds |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `expense` | `accounting.db`; table `expense` or expense-like table | primary key `id`/`_id`/`uid`/`expense_id`, `name`/`title`/`description`/`desc`, `amount`/`value`/`cost`/`price`, `category`/`category_id`/`cat`/`type`, `note`/`notes`/`memo`, `created_date`, `modified_date`, or date/timestamp fields |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `category` | `accounting.db`; category column or category table if schema exposes one | `id`/`_id`, `name`/`title`/`description`; category values may be encoded integer ids |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `backup_record` | `accounting.db`; table `backup` | backup metadata |

### Access Semantics
- Source of truth: Read expenses from `accounting.db`, especially the `expense` table, rather than from rendered list UI alone.
- Entity semantics: An expense contains name, amount, category reference/value, note, and created/modified state; category rows are separate entities when the schema exposes them.
- Selector semantics: Expense row id is the primary selector; date/name/category filters are alternate selectors and may match multiple expenses.
- Value encoding: The documented `expense.amount` stores integer cents; preserve discovered numeric representation, created/modified date format, currency, account, type, and note fields. In the fixed-domain schema, category codes are `1=Others`, `2=Income`, `3=Food`, `4=Housing`, `5=Social`, `6=Entertainment`, `7=Transportation`, `8=Clothes`, `9=Health Care`, `10=Education`, and `11=Donation`; matching is case-insensitive but stored values remain integer codes.
- Relationship invariants: A category id column refers to a verified category relation or the documented fixed domain and never stores the display label. A similarly named table is not authoritative category metadata unless its key is actually referenced by the expense schema; category creation occurs only when explicitly modeled.
- Boundary: Do not guess an uninitialized fallback database or treat helper/framework tables as expense entities.

### Access Procedure
- Initialization: Require an app-initialized expense database; do not create a guessed database filename or schema when no authoritative store is present.
- Runtime discovery: Select an initialized SQLite database by header plus expense-shaped schema, excluding sidecars and helper/framework stores; map id, name, amount, category, note, timestamp, constraints, and category-table columns. Determine whether category values use the fixed domain or a verified foreign-key relation before resolving labels.
- Read procedure: Optional name and category filters use case-insensitive substring matching. Read results contain both the raw category code and its semantic label. When the expense column uses the fixed domain, labels resolve from that domain rather than from an unrelated or unreferenced lookup table.
- Write procedure: Accept category meaning through an exact semantic label or verified id, resolve it to the storage code before mutation, and reject unknown or ambiguous labels rather than substitute a nearby category. Preserve schema defaults for required date, currency, account, type, and timestamp fields; create a category only when the interface explicitly permits it. When transactional access to the authoritative SQLite file is available, mutate that file in place rather than requiring a detached replacement workflow.
- Delete procedure: Delete only the expense row selected by its durable id; do not reinterpret a matching id from an unrelated table as an expense. Apply deletion transactionally to the selected authoritative database.
- Process/file handling: Stop ProExpense when external file replacement is actually required. Treat every privileged copy or transfer as fallible and do not continue with a missing or empty staging artifact. A replacement workflow preserves database ownership, mode, security context, and consistent sidecar state; in-place SQLite access does not require an unrelated staging copy.

### Consistency / Recovery
- Snapshot consistency: Copy matching sidecars with the main DB or checkpoint first. Restore original uid/gid, mode, and security context, and remove only sidecars invalidated by replacement.
- Transaction consistency: Expense creation/update and category-code validation occur in one transaction; a multi-expense request does not leave a silently miscategorized partial result. A failed access, copy, transaction, or replacement leaves the authoritative database untouched and is surfaced as an access/consistency failure rather than an empty result.
- Relationship consistency: Keep stored category foreign keys consistent with the selected category row and do not substitute display labels into id columns.
- Recovery boundary: Unknown category labels and contradictory lookup metadata are schema/encoding errors, not permission to fall back to `Others` or another approximate category.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Preferences are not the expense/category content store | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: Do not use SharedPreferences as an expense or category content store.

### Access Procedure
- Not applicable: Expense and category content access uses the selected SQLite database.

### Consistency / Recovery
- Boundary: Preference changes cannot repair expense rows or category references.

## Providers
### Primary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Secondary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: No provider-backed ProExpense API is documented for the documented state interface.

### Access Procedure
- Not applicable: Expense and category access uses the selected SQLite database.

### Consistency / Recovery
- Boundary: Do not invent provider rows for private expense state.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | DB rows are the documented state interface | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| exported backup file | backup/export path if produced by app | file path/date |

### Access Semantics
- Boundary: Backup/export files are auxiliary artifacts and are not the authoritative expense store.

### Access Procedure
- Surface selection: Use a concrete backup/export path only for an explicitly modeled export or restore interface.
- Delete procedure: Backup-file deletion does not delete expense rows and expense deletion does not imply backup-file mutation.

### Consistency / Recovery
- Recovery boundary: Do not restore or replace the expense database from an unvalidated auxiliary file format.
