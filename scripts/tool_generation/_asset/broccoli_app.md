# Broccoli App Developer Document

## App Identity
- Package name: `com.flauschcode.broccoli`
- Main app data root: `/data/data/com.flauschcode.broccoli`
- State summary: recipe data is stored in an app-private SQLite database.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/com.flauschcode.broccoli/databases/broccoli` | Authoritative recipe store |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `recipe` | DB `broccoli`; table commonly named `recipes`, `recipe`, or `recipeentity` | primary key `id`/`_id` or text primary key, `title`/`name`, `description`/`desc`/`summary`, `servings`/`portions`/`yield`, `preparationTime`/`preparationtime`/`prepTime`/`prep_time`/`time`, `ingredients`, `directions`/`instructions`/`steps`, `source`, `imageName`, favorite flag when present |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `category` | DB `broccoli`; table `categories` when present | category id/name used to organize recipes |
| `ingredient` | DB `broccoli`; ingredient table when present | recipe relation by `recipeId` or `recipe_id`, ingredient fields |
| `direction_step` | DB `broccoli`; step/direction table when present | recipe relation by `recipeId` or `recipe_id`, step/direction fields |

### Access Semantics
- Source of truth: Read recipes from the private SQLite database and discover schema before assuming column names.
- Entity semantics: A recipe can store ingredients/directions inline or own normalized category, ingredient, and direction-step relations.
- Selector semantics: Prefer the recipe primary key column (`id` or `_id`) for update, delete, and exact lookup. Title/name selectors are user-facing alternatives and can be ambiguous; interfaces using them should resolve matching row ids before mutation. For an auto-generated SQLite primary key, `last_insert_rowid()` is scoped to the connection that performed the insert, so the created recipe id is obtained and read back on that same connection before it is used for child relations or returned to callers.
- Value encoding: Preserve existing row id type, timestamp scale, favorite flag, preparation-time text, and ingredient/direction formatting. `servings` is textual; a purely numeric serving input may be normalized to singular/plural `serving(s)` text. Normalized child tables retain order fields.
- Relationship invariants: Normalized children refer to an existing recipe id and preserve position/order; category/image references remain separate from serialized ingredient/direction text.
- Boundary: Do not select helper/framework databases or map serialized content into identifier/relation columns.

### Access Procedure
- Runtime discovery: Select a database by SQLite header plus recipe-shaped schema, excluding sidecars and helper/framework stores; inspect recipe candidates (`recipes`, `recipe`, `recipeentity`), columns, constraints, aliases, and optional child relations before mapping inputs.
- Read procedure: Read recipe rows plus discovered ingredient and direction-step relations without requiring optional child tables.
- Write procedure: Map requested title, description, servings, preparation time, ingredients, and directions only to compatible discovered columns; honor schema defaults and create/update the parent row, obtain its generated id, and write child rows within the same SQLite connection and transaction.
- Delete procedure: Resolve the exact recipe id and remove child rows only when schema evidence or declared foreign-key behavior establishes the relationship.
- Process/file handling: Stop Broccoli before private DB mutation or replacement, prefer structural SQLite operations, and preserve database ownership and sidecar consistency.

### Consistency / Recovery
- Snapshot consistency: Copy the main DB with matching WAL/SHM state or checkpoint first. Replace atomically, restore original uid/gid, mode, and security context, and discard only sidecars made stale by that replacement.
- Relationship consistency: Preserve category, ingredient, and direction linkage and ordering across parent/child tables. A successful recipe creation returns the persisted parent-row id; zero, an empty value, or an id that cannot be read back does not identify a created recipe.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Preferences are not the recipe content store | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: Do not use SharedPreferences as a recipe content store.

### Access Procedure
- Not applicable: Recipe content access uses the private SQLite database.

### Consistency / Recovery
- Boundary: Preference changes cannot repair recipe rows or normalized child relations.

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
- Boundary: No provider-backed recipe API is documented for the documented state interface.

### Access Procedure
- Not applicable: Recipe access uses the private SQLite database.

### Consistency / Recovery
- Boundary: Do not invent provider state for private recipes or child relations.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| `recipe_image_reference` | image names referenced by recipe rows | image filename/name stored in DB |

### Access Semantics
- Entity semantics: Image references are auxiliary fields owned or shared by recipe rows.
- Boundary: Do not mutate image files unless a recipe-image lifecycle is documented.

### Access Procedure
- Surface selection: Resolve recipe image names through the owning recipe row before any file access.
- Delete procedure: Recipe deletion does not imply image-file deletion unless ownership and sharing semantics are documented.

### Consistency / Recovery
- Relationship consistency: Shared or referenced recipe images must not be removed based only on one recipe-row deletion.
