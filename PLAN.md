# Plan: gitagent v0.6.0 — Protocolo feromona (sin inbox, rechazo en escrituras, snapshots parciales multi-orquestador)

> **Estado:** borrador para iterar — definido para nueva sesión con solo este documento.
> **Rama de trabajo:** `feat/v0.6-pheromone-snapshot` desde `main`.
> **Release:** merge a `main` + tag `v0.6.0` tras validación del usuario.

---

## 1. Objetivos

- **Eliminar el inbox.** No hay comunicación directa entre agentes. La coordinación emerge de la **feromona**: un log en la BD con tripletas `(actual_intent_id, op, archivo)`. Cada edición deja una marca rastreable: "yo edité este archivo, y cuando lo edité tenía esta intención".
- **Escrituras con lock por archivo y rechazo informado.** `write_file` / `edit_file` / `delete_file` **adquieren el lock del archivo al inicio** y lo **liberan al final, siempre** (`try/finally`). Si otro agente activo lo mantiene → la escritura **NO se aplica** y se devuelve el output de `read_file` (informado), para que el agente re-planifique sabiendo qué pasó. No hay espera de bloqueo en el server.
- **Lecturas informadas (estilo git).** `read_file` devuelve contenido + diff contra la base (rama target) + histórico de ediciones con intención. Es la misma herramienta de lectura, no una nueva.
- **`snapshot_status`** — nueva herramienta que devuelve el estado del snapshot como un `read` de **todo el worktree** (todos los archivos editados), estilo diff.
- **Warning de intent en cada lectura.** `read_file` y `snapshot_status` devuelven un aviso corto en cada respuesta recordando que la intent debe estar actualizada antes de escribir.
- **Snapshots parciales multi-orquestador.** Varias sesiones abiertas a la vez, cada una con sus especialistas. El orquestador hace **snapshot** de la parte que considere suya: commit en la rama del worktree, con archivos y `boundary_edit_id` opcionales, sin borrar el worktree.
- **Iteración por snapshot.** El snapshot avanza la frontera (por archivo/sesión); lo posterior y los archivos no incluidos quedan para la siguiente.
- **Documento autónomo.** Este `PLAN.md` es la única referencia; una sesión nueva debería poder implementar v0.6.0 literal.

## 2. No-objetivos (v0.6.0)

- Sin colas de escritores ni fairness: el lock es optimista.
- Sin espera dentro del server MCP (evita timeouts del cliente). El conflicto se resuelve en el agente vía rechazo informado.
- Sin merge automático de conflictos.
- Sin detección de escrituras fuera de MCP (solo prompt enforcement).
- HTTP/SSE MCP transport: no (solo stdio).
- Retrocompatibilidad con v0.5.x (breaking total).

---

## 3. Arquitectura

```
Host (Claude Code / opcode / ...)
  → spawns: gitagent mcp (stdio)
    → .gitagent/state.db (sqlite)
    → .gitagent/worktree/   ← worktree compartido por TODAS las sesiones
```

- **Varias sesiones open a la vez**, cada una con sus agentes.
- **Un worktree por repositorio**, reutilizado entre sesiones (no se borra al hacer snapshot).
- **Target de rama a nivel de worktree.** La primera sesión sobre un worktree define `target_branch`; las siguientes lo comparten. Todas las snapshots de un worktree van a la misma rama. `start_session` no acepta cambiar target en sesiones posteriores (solo si se crea el worktree).

---

## 4. Lifecycle (multi-sesión, worktree compartido)

```
no_worktree
  ↓ start_session(feature, target_branch?)        # crea worktree + sesión; pick target
worktree + 1..n sesiones open
  ├─ register_agent(role, session_id?)            # 1 open: opcional; 2+: OBLIGATORIO (error duro)
  ├─ agente: start_intent → read / edit / write / delete (lock + rechazo informado)
  ├─ snapshot_session(session_id, message, files?, boundary_edit_id?)   # commit parcial, worktree SIGUE vivo
  ├─ snapshot_status(session_id)                  # estado del snapshot actual (worktree completo vs target)
  ├─ abort_session(session_id)                    # marca aborted; SOLO borra trabajo si es la última open
  ├─ get_session(session_id?), list_sessions()
  ▼
finalizado/aborted por sesión
```

Permitido: sesión A procesando `src/auth.py`, sesión B `tests/`. Especialistas compiten por un archivo compartido; uno gana el lock, el otro recibe rechazo y cambia de plan.

---

## 5. Esquema SQLite (migración `user_version` 2 → 3)

Se conservan `session`, `agents`, `intents`, `edits`. Se elimina `inbox`.

```sql
PRAGMA user_version = 3;

-- 1. Multi-open: eliminar la única-sesión como invariante
DROP INDEX idx_one_open_session;

-- 2. Eliminar inbox
DROP TABLE inbox;

-- 3. Blank para re-play exacto
ALTER TABLE edits ADD COLUMN replace_all INTEGER NOT NULL DEFAULT 0;

-- 4. Frontera por archivo y por sesión (la "iteración de snapshot")
CREATE TABLE IF NOT EXISTS snapshot_progress (
    session_id   TEXT NOT NULL,
    file         TEXT NOT NULL,
    last_edit_id INTEGER NOT NULL DEFAULT 0,
    last_ts      TEXT,
    PRIMARY KEY (session_id, file)
);

-- 5. Locks por archivo (escrituras)
CREATE TABLE IF NOT EXISTS locks (
    file         TEXT PRIMARY KEY,
    holder_agent TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    token        TEXT NOT NULL,
    acquired_at  TEXT NOT NULL
);

-- 6. Histórico de snapshots
CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    message         TEXT NOT NULL,
    boundary_edit_id INTEGER,          -- sin boundary_ts
    files           TEXT NOT NULL,     -- JSON array de archivos incluidos
    sha             TEXT NOT NULL,     -- commit hash en target
    ts              TEXT NOT NULL
);
```

### 5.1 Target a nivel de worktree

- La fila `session` conserva `target_branch` por simplicidad, PERO el valor real se fija en la primera sesión que crea el worktree.
- `start_session` cuando `worktree` ya existe: **ignora** `target_branch` nuevo y usa el del worktree.
- No hay tabla de worktree separada inicial; si se quiere auditar mejor, añadiremos `worktrees` en v0.6.1. (decisión de simplicidad)

---

## 6. Locks (`locks.py`) — escritura con rechazo informado

### 6.1 Adquisición (al inicio del write/edit/delete)

```
1. INSERT INTO locks(file, holder_agent, session_id, token, acquired_at)
2. si el archivo ya tiene lock de OTRO agente activo → RECHAZADO (no aplica, nada espera)
3. si el lock es de otro agente pero acquired_at expiró (> lock_ttl_seconds) → se reclama (huérfano por crash)
4. si es el mismo agente → idempotente (reutiliza token)
```

TTL por defecto: **15 segundos** (`lock_ttl_seconds=15`). Escrituras son rápidas (sub-ms temp + os.replace); 15s cubren con margen y permiten liberara huérfanos. `lock_ttl_seconds` configurable en `start_session`.

### 6.2 Aplicación + liberación (SIEMPRE)

Con lock adquirido → `try:` aplicar atomic write y grabar `edits` → `finally:` **liberar el lock SIEMPRE** (DELETE condicional por token, nunca libera lock de otro agente).

### 1. Respuesta de escritura rechazada — NO se aplica

```
{
  "status": "rejected",
  "blocked_by": {"agent_id": "a_3f2c", "role": "...", "session_id": "s_x"},
  "reason": "file locked by another agent",
  "read": {
     "content": "...",
     "sha256": "...",
     "path": "src/auth.py",
     "base_sha": "<sha de rama target>",
     "diff": "...",
     "edits": [
        {"edit_id": 12, "agent_id": "a_3f2c", "role": "impl", "intent": "rate limiter", "op": "edit", "ts": "..."},
        ...
     ]
  }
}
```

El campo `read` **es exactamente lo que devolvería `read_file(agent_id, file)`**. La escritura no se intenta. La respuesta normal (aplicó) sigue siendo `{ok: True, path}`.

### 6.4 STALE_WRITE

`expected_sha256` se comprueba tras adquirir el lock: si el archivo cambió entre la lectura y el write → `STALE_WRITE`, rechazo informado, sin aplicar.

### 6.5 Locks de lectura

`read_file` **nunca adquiere lock** (lecturas concurrentes). El write es atómico → nunca hay estado a medias.

---

## 7. `read_file` ampliada (es la MISMA herramienta)

```
{
  "content": "...",
  "sha256": "...",
  "path": "src/auth.py",
  "base_sha": "<sha de rama target>",
  "diff": "<git diff textual del archivo vs target>",
  "edits": [
      {"edit_id": 12, "agent_id": "a_3f2c", "role": "...", "intent": "...", "op": "edit", "ts": "..."},
      ...
  ],
  "warning": "La intención debe estar actualizada antes de escribir nada (start_intent/repurpose)."
}
```

- `diff`: git diff real del archivo contra `target_branch` HEAD. **Sin toggle**: diff puro siempre. Confiamos en que los snapshots del worktree a la rama mantienen diffs pequeños (los snapshots logran cada vez más parecido al estado del target).
- `edits`: histórico ordenado de ediciones del archivo con intención + rol. Correlación hunks ↔ edits.
- `warning`: **SIEMPRE presente**, recuerdo corto.
- `list_edits` gana `limit=N` para que el orquestador elija frontera.

---

## 8. `snapshot_status(session_id)` — estado del snapshot ACTUAL

Nueva herramienta MCP. Devuelve **el diff de todos los archivos editados no commiteados en el worktree**, estilo `git status` + `git diff`, con la feromona:

```
{
  "worktree": "/path/to/.gitagent/worktree",
  "target_branch": "main",
  "base_sha": "...",
  "files": [
    {
      "file": "src/auth.py",
      "status": "modified",          // 'added' | 'modified' | 'deleted' | 'clean'
      "diff": "<git diff del archivo vs target>",
      "edits": [ {edit_id, agent_id, role, intent, op, ts}, ... ],
      "snapshot_progress": {"last_edit_id": 40, "last_ts": "..."}
    }, ...
  ],
  "pending_files_count": 4,
  "warning": "La intención debe estar actualizada antes de escribir (start_intent/repurpose)."
}
```

- Ámbito: **todo el worktree** vs target (no filtra por sesión; el target es único del worktree). Así una sola llamada le da al orquestador la vista de trabajo en pendiente.
- Se incluye `snapshot_progress` por archivo para que vea qué es nuevo vs lo ya commitado.
- Última línea: alarm de intención igual que `read_file`.
- El orquestador usa esto para decidir `files` y `boundary_edit_id` de la siguiente snapshot.

---

## 9. Snapshot (`snapshot.py`) — commit parcial multi-orquestador, sin borrar el worktree

```python
snapshot_session(
    session_id: str,
    message: str,
    *,
    boundary_edit_id: int | None = None,   # SIN boundary_ts (eliminado)
    files: list[str] | None = None,        # explícito; None = TODOS los archivos (por trabajo completo)
    sign: bool = False,
)
```

Reglas:

1. **Scope**: `files` `None` → **todos los archivos con ediciones** del worktree (cualquier agente/sesión). Lista → solo esos. **Solo los archivos incluidos** se tocan en target; el resto del árbol queda igual.
2. **Frontera**: senza → contenido **actual del disco** (fast path). Con `boundary_edit_id` → se reconstruye cada archivo del scope **hasta ese `edit_id`** vía replay. La frontera la elige el orquestador tras `list_edits` / `read` / `snapshot_status`. El snapshot incluye ediciones **de cualquier agente/sesión** que quedaron ≤ frontera (estado real); ediciones posteriores NO entran.
3. **Reconstrucción en frontera (`replay`)**: base = `git show <target>:<file>`; aplicar en orden `ts`/`id` todas las filas `edits` del archivo con `id ≤ boundary`: `write` (full content), `edit` (old→new con `replace_all`), `delete` (archivo ausente). Mismatch (excepto cambios fuera de MCP) → error claro `REPLAY_MISMATCH`, aborto, no se avanza frontera.
4. **Commit**:
   - temp worktree `.gitagent/_snapshot_temp/` sobre `target_branch`.
   - Sobrescribir **solo** archivos del scope → `git add <scope>` (no `add -A`) → commit → `git update-ref`.
   - Si ningún archivo cambió neto en target → **error limpio "nothing to snapshot"**, idempotente, sin fila `snapshots`.
   - El worktree vivo NO se borra.
5. **Frontera por archivo (CRÍTICO — punto 4)**:
   - Tras commit: `snapshot_progress[session_id][archivo].last_edit_id = <último edit_id del archivo ≤ boundary_edit_id>`, o el `edit_id` máximo del archivo si no hubo frontera.
   - Archivos **fuera de `files` NO avanzan**, aunque sus ediciones sean **anteriores** al `edit_id` elegido — quedan pendientes para el siguiente snapshot de esa sesión. (lo que S2 tocó no se pierde; la next iter lo coge).
   - Ejemplo:

     ```
      S1: a1 edita a.py (id=1) y b.py (id=2)
      S2: a2 edita b.py (id=3) y a.py (id=4)
      S1: snapshot_session(files=["a.py"], boundary_edit_id=4)
      ASSERT:
        - commit pone a.py = replay(base + edit1 + edit4)  (estado real en frontera 4)
        - b.py NO se toca en target; snapshot_progress[S1]["b.py"] NO avanza (sigue 0)
        - snapshot_progress[S1]["a.py"] = 4
        - siguiente snapshot (S1, files=["b.py"],... ) reconstruye b.py incluyendo edit2 y edit3 (estado actual), y avanza su progress
     ```

6. **Iteraciones**: snapshot 2 de S1 con files=["a.py"] → nada nuevo (a.py ya está en target) → `nothing to snapshot` (idempotente); b.py sigue pendiente.

---

## 10. Recuperación crash medio-write (DUDA 5 — elegida)

`edits` = atribución de feromona, no fuente de verdad de contenido (el disco lo es). El orden operacional: aplicar `os.replace` (write) y luego `INSERT` fila en la BD. Ventana de crash microsegundos.

**Solución elegante: reconciliación observada.** En `snapshot_status` (o al inicio del snapshot):
- Comparar `git worktree diff` (por archivo) contra el log `edits`.
- Si hay cambios en disco que ninguna fila explica (filas perdidas por crash) → insertar **fila sintética** `{op: 'adjusted', agent_id: '<unknown>', intent_id: NULL, ts: now}` para no dejar agujero en la feromona y que el replay en frontera **no** falle con `REPLAY_MISMATCH`.
- El archivo se desconecta: la siguiente snapshot B podrá tomarlo en el estado actual (o con frontera validada).

Nada de columnas `applied`, ni transacción file+SQLite, ni write-ahead: se mantiene simple, el disco manda, el crash queda observables al siguiente snapshot.

---

## 11. Cambios de código y archivos

| Archivo | Cambio |
|---|---|
| `db.py` | migración v3 (`snapshot_progress`, `locks`, `snapshots`, drop inbox/índice, `replace_all`). |
| `locks.py` | **NUEVO** — `acquire` (rechazo/TTL), `release` con token. TTL default 15s. |
| `edits.py` | `write`/`edit`/`delete` agarran lock al inicio y liberan en finally; rechazo informado; `read` ampliada; `list_edits(limit)`; grabación de `replace_all`. |
| `replay.py` | **NUEVO** — `reconstruct(file, session_id, boundary_edit_id)`. |
| `snapshot.py` | **NUEVO** — `snapshot_session`, `snapshot_status`, `reconcile_untracked()`. |
| `session.py` | multi-open; reusuario worktree; target fijada en worktree; abort lógico; `list_sessions()`. |
| `agents.py` | `register_agent(role, session_id?)`; error duro pasi 2+ open y no `session_id`; `validate_agent` vía `agents.session_id`. |
| `mcp_server.py` | registrar `snapshot_session`, `snapshot_status`, `list_sessions`, `list_snapshots`; quitar `check_inbox`, `send_message`, `finalize_session`; versión `0.6.0`. |
| borrado | `inbox.py`, `finalize_*` de session. |
| docs | `SKILL.md`, `README.md`, `CHANGELOG.md`, `PLAN.md` (este). |

---

## 12. Tests

Eliminar `tests/test_inbox.py`.

- **test_db**: migración v2→v3 (DROP inbox/índice, `replace_all`, nuevas tablas, multi-open insert OK).
- **test_session**: 2 sesiones open a la vez; target worktree compartido; abort última borra worktree vs con otras open NO; `list_sessions`.
- **test_agents**: `register_agent` obligatorio el `session_id` con 2+ open; validación vía agent.session_id.
- **test_locks**: lock adquirido/liberado con finally; rechaza write a otro agente (NO aplica); TTL 15s libera huérfano; idempotencia re-mismo agente.
- **test_edits**: read ampliada (diff+edits+warning siempre); write/edit con lock correcto; rechazo NO aplica recursa y devuelve `read` completo; STALE_WRITE; replace_all grabado.
- **test_replay**: write/edit/delete/replace_all en frontera; orden ts/id; mismatch→ REPLAY_MISMATCH.
- **test_snapshot** (punto 4 aconse):
  - sincroniza el escenario de la sección 9 (a.py/b.py con sesiones A/B, boundary=4)
  - sin files = todos; nothing-to-snapshot idempotente; el worktree vivo sigue y 2ª iteración de agente.
- **test_status**: `snapshot_status` lista files/diffs/edits/progress correctos.
- **test_reconcile**: crash simulado (disco con cambio sin filas) → `snapshot_status` inserta fila sintética.
- CI: ruff + pytest.

---

## 13. Documentos finales

- `SKILL.md` v0.6.0 (protocolo feromona, lock rechazo, snapshot_status, sin inbox).
- `README.md` — multiline quick start.
- `CHANGELOG.md` — `[Unreleased]` con breaking: eliminado inbox/finalize, escaneado de locks, lectura rica, snapshot_status, multi-sesión.

---

## 14. Decisiones cerradas (v0.6.0)

1. ✅ `register_agent` con 2+ sesiones abiertas sin `session_id` → **error duro**
2. ✅ TTL lock = **15s**
3. ✅ Target = **nivel de worktree**; snapshot_status muestra **todo** el worktree vs target
4. ✅ Sin toggle de diff — `git diff` puro; los snapshots del worktree aspiran a diffs pequeños
5. ✅ Crash-medio: **reconciliación observada** (`adjusted` rows), no trans-App-máquina

## 14a. Decisiones nuevas — v0.6.1 (feedback de ejecución real)

1. ✅ **`expected_sha256` sale de la API MCP.** El agente lo manejaba mal (schema string obligatorio, sentinel `"null"`). Ahora gawt guarda en una tabla `last_reads(agent_id, file, sha256, ts)` la última lectura de cada agente por archivo; el write verifica contra ella automáticamente y rechaza con `STALE_WRITE` si quedó vieja. El agente ya no pasa ni ve SHA. Tras write/edit la fila se actualiza al nuevo SHA; tras delete se borra.
2. ✅ **Read legible, sin `diff`.** `read_file` devuelve `content`, `sha256`, `base_sha`, `edits[]` (con `op`, `role` resuelto, `intent` texto, `intent_id`, `ts`) y `warning` de intent. El `diff` solo vive en `snapshot_status` (donde tiene sentido: estado global vs target). La intención y el rol ya aparecen en el read y en el rechazo `STALE_WRITE` (mismo payload), sin depender de `list_edits`.
3. ✅ **Aviso de lectura vieja.** Si lees un archivo y tu `last_reads` apunta a un SHA distinto del actual → `note` breve en la respuesta (alguien más escribió después de tu última lectura) + warning de intent como siempre.
4. ✅ `list_edits` resuelve `intent` texto y `role` (JOIN) para trazabilidad sin llamadas extra.
5. Migración `user_version` 3 → 4: `CREATE TABLE last_reads(agent_id, file, sha256, ts, PRIMARY KEY(agent_id, file))`.

## 15. Orden de implementación

1. Rama `feat/v0.6-...`.. `git checkout -b feat/v0.6-pheromone-snapshot` + migraciones.
2. `db.py` (migración v3) + tests.
3. `locks.py` (`acquire`/`release`, TTL 15) + tests.
4. `edits.py` (locks en edit/write/delete, rechazo, read ampliado, warning, `list_edits(limit)`) + tests.
5. `replay.py` reconstrución + tests.
6. `snapshot.py` (`snapshot_session`, `snapshot_status`, `reconcile`) + tests (punto 4 + crash).
7. `agents.py`/`session.py` multi-sesión + target por worktree + tests.
8. `mcp_server.py` tools + versión.
9. ruff + pytest, E2E manual do (a y b, compartidas).
10. Docs.

---

*Este documento es el AST+SINGULAR destino para la implementación v0.6.0. Toda decisión nueva de v0.6 va a este archivo ANTES de codificar.*