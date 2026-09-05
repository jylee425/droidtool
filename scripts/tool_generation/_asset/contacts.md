# Contacts Developer Document

## App Identity
- Package name: `com.google.android.contacts`
- Package fallback: `com.android.contacts`
- Provider package: `com.android.providers.contacts`
- State summary: saved contacts are Android ContactsProvider state; Contacts app preferences are separate UI state.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| ContentProvider | `content://contacts/phones/` | Contact phone read surface |
| ContentProvider | `content://com.android.contacts/raw_contacts` | Raw contact identity write surface |
| ContentProvider | `content://com.android.contacts/data` | Structured name and phone data surface |
| Intent | `android.intent.action.INSERT` with contact MIME type | UI draft surface, not durable saved-contact state |
| SharedPreferences XML | `/data/data/com.google.android.contacts/shared_prefs/com.google.android.contacts.xml` | Contacts UI preferences |

## DB Entities

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Direct provider-private DB edits are outside the documented interface | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| provider-private rows | ContactsProvider private database under `com.android.providers.contacts` | raw-contact, data, and phone rows; `raw_contact_id`, `mimetype`, `data1`, `data2`, `data3` |

### Access Semantics
- Boundary: Provider-private rows explain the contact relation model but are not a direct DB mutation surface for this interface.

### Access Procedure
- Surface selection: Use ContactsProvider for saved-contact state rather than replacing or editing provider-private DB files.

### Consistency / Recovery
- Relationship consistency: Saved contacts retain the provider-managed relationship between raw-contact identity rows and typed data rows.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `contacts_ui_preference` | `com.google.android.contacts.xml` | `android.contacts.SORT_ORDER`; build-dependent display order, theme, and navigation keys when present |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Source of truth: Contacts UI preferences are distinct from saved contacts and affect display behavior rather than contact identity.
- Selector semantics: Preference keys select individual initialized UI settings.
- Boundary: Do not infer preference writes for keys whose value domains and mutation behavior are not documented.

### Access Procedure
- Runtime discovery: Read initialized XML keys and their stored types before exposing build-dependent preferences.
- Read procedure: Parse preference XML structurally and return only documented user-facing keys.

### Consistency / Recovery
- Boundary: Preference state cannot create, repair, or delete provider-backed contacts.

## Providers

### Primary
| Entity | Provider/URI | Fields |
|---|---|---|
| `contact` | ContactsProvider URIs above | `_id`, `display_name`, `number`, phone label, `raw_contact_id`, data-row ids |

### Secondary
| Entity | Provider/URI | Fields |
|---|---|---|
| `contact_data_row` | `content://com.android.contacts/data` | StructuredName: `mimetype=vnd.android.cursor.item/name`, `data1` display name, `data2` given name, `data3` family name; Phone: `mimetype=vnd.android.cursor.item/phone_v2`, `data1` number, `data2` phone type |

### Auxiliary
| Entity | Provider/URI | Fields |
|---|---|---|
| `contact_draft` | `android.intent.action.INSERT` | prefilled name and phone before user save |

### Access Semantics
- Source of truth: Saved contacts are provider rows; an insert-intent payload is only a draft until the user saves it.
- Entity semantics: A contact write consists of a raw-contact identity plus one or more typed data rows, not one flat row.
- Selector semantics: Provider row ids are primary selectors; display name and normalized phone number are alternate selectors that may match multiple contacts.
- Value encoding: Names and phone numbers use the documented MIME-typed data rows and `data1`/`data2`/`data3` fields.
- Relationship invariants: Every StructuredName or Phone row created for a contact remains linked to the intended `raw_contact_id`.

### Access Procedure
- Runtime discovery: Query available provider columns and tolerate projection differences across Android builds.
- Read procedure: Query saved contacts through the documented phone/contact provider surface and normalize phone numbers for matching.
- Write procedure: Insert the raw-contact row first, then insert documented StructuredName and Phone rows linked by `raw_contact_id`.
- Delete procedure: Resolve exact provider ids before deletion; ambiguous name or phone selectors do not silently choose one contact.

### Consistency / Recovery
- Transaction consistency: A contact creation must not leave an identity row without its requested typed data rows.
- Relationship consistency: Raw-contact and data-row ids remain associated through provider-managed relations.
- Recovery boundary: Provider operations are authoritative; do not attempt to repair partial state through private database replacement.

## Files

### Primary
| Entity | Path | Fields |
|---|---|---|
| None | No file-backed contact lifecycle is documented | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: Do not invent a file-backed representation for saved contacts.

### Access Procedure
- Not applicable: Use ContactsProvider and documented Contacts preferences.

### Consistency / Recovery
- Boundary: File operations cannot substitute for provider contact lifecycle operations.
