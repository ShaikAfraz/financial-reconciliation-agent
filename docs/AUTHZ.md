# Veritas Treasury • Authorization & Role-Based Access Control (RBAC) Specification

## 1. Security Architecture & Boundary Guarantees
All API routes under `/api/runs` and `/api/imports` enforce the following server-side security checks:
1. **Session Authentication**: Valid active session cookie (`session_id`) or Bearer token required. Unauthenticated requests return `401 Unauthorized`.
2. **CSRF Protection**: All state-changing requests (`POST`, `PUT`, `PATCH`, `DELETE`) with cookie sessions require a valid `X-CSRF-Token` header matching the active session. Mismatches return `403 Forbidden`.
3. **Origin & Referer Validation**: Host header must match `Origin` or `Referer` on non-GET requests. Cross-origin forgery returns `403 Forbidden`.
4. **Ownership & Cross-User Privacy**:
   - Resources (`runs`, `import_batches`, `import_rows`, matches, exceptions, and results) are strictly scoped to `owner_user_id`.
   - If an `analyst` attempts to access, inspect, modify, or query a resource owned by another user, the server responds with **`404 Not Found`** (preventing resource enumeration and privacy leakage).
5. **Least Privilege & Role Enforcement**: Roles are enforced on the server, never solely in the UI.

---

## 2. Role Definitions

| Role | Description |
|---|---|
| `admin` | System Administrator. Full read/write access to all runs across all users. Can perform emergency batch confirmation overrides (min 10 char audited reason) and manage user accounts. |
| `analyst` | Financial Treasury Analyst. Can create runs, upload statements, stage imports, map columns, review/edit rows, confirm batches, and trigger reconciliation for runs **owned by them**. Cannot access or modify other analysts' runs. |
| `auditor` | Compliance & Internal Audit. Read-only access to all runs, confirmed records, reviews, matches, exceptions, statement checks, and audit trails. Blocked from creating or mutating any state (`403 Forbidden`). |

---

## 3. Authorization Permission Matrix

| Endpoint | HTTP Method | Minimum Role | Ownership Check | Cross-User Behavior |
|---|---|---|---|---|
| `/api/runs` | `GET` | `auditor` | Filtered to own runs (`admin`/`auditor` see all) | Scoped |
| `/api/runs` | `POST` | `analyst` | Assigned to authenticated user | N/A |
| `/api/runs/{id}` | `GET` | `auditor` | Run owner (`admin`/`auditor` can read) | `404 Not Found` |
| `/api/runs/{id}/imports/{side}` | `PUT` | `analyst` | Run owner (`admin` can upload) | `404 Not Found` |
| `/api/imports/{batch}/discard` | `POST` | `analyst` | Batch/Run owner (`admin` can discard) | `404 Not Found` |
| `/api/imports/{batch}/map` | `POST` | `analyst` | Batch/Run owner (`admin` can map) | `404 Not Found` |
| `/api/imports/{batch}/rows` | `GET` | `auditor` | Batch/Run owner (`admin`/`auditor` can view) | `404 Not Found` |
| `/api/imports/{batch}/rows/{row}` | `PATCH` | `analyst` | Batch/Run owner (`admin` can edit) | `404 Not Found` |
| `/api/imports/{batch}/rows/{row}/reject` | `POST` | `analyst` | Batch/Run owner | `404 Not Found` |
| `/api/imports/{batch}/rows/{row}/restore` | `POST` | `analyst` | Batch/Run owner | `404 Not Found` |
| `/api/imports/{batch}/rows/{row}/include-duplicate` | `POST` | `analyst` | Batch/Run owner | `404 Not Found` |
| `/api/imports/{batch}/summary` | `GET` | `auditor` | Batch/Run owner (`admin`/`auditor` can view) | `404 Not Found` |
| `/api/imports/{batch}/confirm` | `POST` | `analyst` (standard) / `admin` (override) | Batch/Run owner (`admin` can override) | `404 Not Found` |
| `/api/runs/{id}/statement-checks` | `GET` | `auditor` | Run owner (`admin`/`auditor` can view) | `404 Not Found` |
| `/api/runs/{id}/reconcile` | `POST` | `analyst` | Run owner (`admin` can trigger) | `404 Not Found` |
| `/api/runs/{id}/results` | `GET` | `auditor` | Run owner (`admin`/`auditor` can view) | `404 Not Found` |
| `/api/runs/{id}/matches` | `GET` | `auditor` | Run owner (`admin`/`auditor` can view) | `404 Not Found` |
| `/api/runs/{id}/exceptions` | `GET` | `auditor` | Run owner (`admin`/`auditor` can view) | `404 Not Found` |
| `/api/runs/{id}/records` | `GET` | `auditor` | Run owner (`admin`/`auditor` can view) | `404 Not Found` |
| `/api/runs/{id}/export/matches.csv` | `GET` | `auditor` | Run owner (`admin`/`auditor` can view) | `404 Not Found` |
| `/api/auth/users` | `GET` / `POST` | `admin` | System-wide admin only | `403 Forbidden` |

## 4. CSV Workflow Route Matrix

All CSV workflow routes require an authenticated session. Every non-GET request requires a matching same-host `Origin` (or `Referer`) and CSRF token. Admins may operate on any run; analysts may operate only on runs they own; auditors may read all runs and may not mutate state. A run, batch, or row outside the caller's permitted scope returns 404.

| Route | Method | Role | Ownership rule |
|---|---|---|---|
| `/api/runs` | GET | admin, analyst, auditor | Analyst sees own; admin/auditor see all |
| `/api/runs` | POST | admin, analyst | Created for authenticated owner |
| `/api/runs/{id}` | GET | admin, analyst, auditor | Owner; admin/auditor may read all |
| `/api/runs/{id}/imports/{bank\|ledger}` | PUT | admin, analyst | Must own run unless admin |
| `/api/imports/{batch}/map` | POST | admin, analyst | Must own batch unless admin |
| `/api/imports/{batch}/rows` | GET | admin, analyst, auditor | Owner; admin/auditor may read all |
| `/api/imports/{batch}/rows/{row}` | PATCH | admin, analyst | Must own batch unless admin |
| `/api/imports/{batch}/rows/{row}/{reject\|restore\|include-duplicate}` | POST | admin, analyst | Must own batch unless admin |
| `/api/imports/{batch}/summary` | GET | admin, analyst, auditor | Owner; admin/auditor may read all |
| `/api/imports/{batch}/confirm` | POST | admin, analyst | Must own batch; override is admin-only |
| `/api/imports/{batch}/discard` | POST | admin, analyst | Must own batch unless admin |
| `/api/runs/{id}/statement-checks?side=...` | GET | admin, analyst, auditor | Owner; admin/auditor may read all |
| `/api/runs/{id}/reconcile` | POST | admin, analyst | Must own run unless admin |
| `/api/runs/{id}/{results\|matches\|exceptions\|records}` | GET | admin, analyst, auditor | Owner; admin/auditor may read all |
| `/api/runs/{id}/export/matches.csv` | GET | admin, analyst, auditor | Owner; admin/auditor may read all |

The legacy `/api/reconciliation/upload` direct-write endpoint and benchmark-loading endpoint return 404. They cannot bypass staged confirmation or populate product records with benchmark fixtures.
