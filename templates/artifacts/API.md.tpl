````markdown
# API: {{project_name}}

**Format:** {{REST_GraphQL_gRPC_etc}}
**Base URL:** {{url}}
**Auth:** {{auth_scheme}}

## Endpoints

### `{{METHOD}} {{path}}`

**Purpose:** {{one_line}}

**Request:**
```json
{{example}}
```

**Response (success):**
```json
{{example}}
```

**Response (errors):**

| Code | Meaning | Body shape |
|---|---|---|

**Rate limits / quotas:** {{if_any}}

<!-- Repeat per endpoint. Alternatively, link out to an OpenAPI spec file. -->

## Versioning policy
{{policy}}

## Deprecation policy
{{policy}}
````
