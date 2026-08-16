# Codex Workflow Guardian

`codex-workflow-guardian` es un flujo Plugin/Skill de Codex exclusivo para macOS,
no un servidor MCP: no tiene una frontera viva de herramientas, datos o
autenticación remotos. Combina la orquestación Guardian, el Reconciler
estrictamente de solo lectura `reconcile-codex-subagents` y el Setup Skill
explícito `setup-codex-workflow-guardian`. El slug antiguo del repositorio es
`codex-subagent-reconciler`.

Este es un proyecto comunitario no oficial, no un producto oficial de OpenAI.

Idiomas: [English](README.md) | [简体中文](README.zh-CN.md) |
[日本語](README.ja.md) | [Español](README.es.md)

Plataforma compatible: solo macOS. La instalación del Plugin core solo requiere
Codex CLI con soporte de marketplace y no requiere Python. Ejecutar cualquier
script incluido requiere Python 3.9+; `portable-full --apply` requiere Python 3.11+.
El manifest no tiene una barrera de instalación por sistema operativo, así que otro SO puede descubrir o instalar
el Plugin; eso no significa que esté soportado. Cada Skill ejecutable incluido falla de forma cerrada en un host
no Darwin antes de cargar snapshots o realizar lecturas/escrituras gestionadas. El soporte sigue siendo solo macOS.

## Modelo mental en tres pasos y ruta corta

1. Usa el comando `codex` normal ya instalado y confiado por el usuario como
   pre-Setup trust boundary. Registra el marketplace respaldado por GitHub con
   el SHA inmutable auditado e instala el Plugin Guardian. Esto proporciona los
   tres Skills Guardian incluidos; no copia credenciales, configuración MCP ni
   OMC privado.

   ```sh
   GUARDIAN_REF="42db0a2c493b39bbf7f1661c2cc375f9b51af769"
   codex plugin marketplace add OneBigMoon/codex-subagent-reconciler \
     --ref "$GUARDIAN_REF" --json
   codex plugin add codex-workflow-guardian@onebigmoon-codex-workflows --json
   ```

2. Inicia una tarea nueva de Codex y llama explícitamente a
   `$setup-codex-workflow-guardian`. El Skill primero toma `E` como el `exact versioned
   cache` de la ruta instalada de su propia Skill y valida que ese anclaje sea limpio,
   inmutable y con `exact-ref` estable. Luego obtiene la fila única de Guardian en
   `codex plugin list --json` y toma `source.path` de esa fila como `P`, verifica que `P`
   proviene de la fuente canónica marketplace y que `P` y `E` tienen el mismo `exact-ref`,
   y después lee y valida el JSON del Plugin desde `E`. Descubre Python entre los candidatos
   fijos de macOS y
   resuelve estáticamente un wrapper de Codex compatible cuando hace falta; no analices
   JSON ni rellenes rutas native/Python a mano. La versión `0.146.0` de
   `plugin list --json` puede omitir `installedPath`; no uses glob/scan para deducir
   rutas ni vuelvas a ejecutar `plugin add`.
   Revisa la
   proyección pública de `--check` y después concede permiso puntual para un
   único `--apply`. Ese apply instala el Plugin All in Luna fijado, su wheel CLI
   exacto y 14 plantillas de roles saneadas en el `CODEX_HOME` persistente elegido.
   Ponytail, Headroom, Node, los hooks de ciclo de vida y el relay HostAdapter
   siguen siendo opcionales y requieren autorización separada. Los Plugins que añade Setup, incluido All in Luna,
   permanecen `installed-but-unowned`; la propiedad del receipt solo cubre archivos de roles y el venv gestionados
   cuya creación por Setup esté demostrada.
3. Para cada entrega posterior llama solo a `$codex-workflow-guardian`. Si All in
   Luna está verificado, reutiliza una ejecución duradera coincidente. El fallback
   nativo/manual acotado solo es elegible antes de cualquier propietario y tras una
   prueba canónica reciente de cero coincidencias; con propietario, un relay no
   demostrado devuelve `ACTION_RELAY_REQUIRED`, y una prueba reciente de ausencia
   de la herramienta exacta devuelve `HOST_CAPABILITY_BLOCKED`: ambos conservan el
   propietario, detienen el flujo y prohíben el fallback. Usa
   `$reconcile-codex-subagents` únicamente para diagnósticos avanzados, saneados y de solo lectura.

Los dos comandos `codex` normales anteriores son la pre-Setup trust boundary.
El Mach-O native directo, el PATH saneado, las vinculaciones explícitas de
Git/Python, las comprobaciones de firma, cutover/rollback y la aceptación
aislada del mantenedor son material avanzado reforzado. La mayoría de los
usuarios solo necesita esta ruta de tres pasos.

| Clase de dependencia | Elementos | Comportamiento de Setup |
| --- | --- | --- |
| `bundled-with-guardian-plugin` | Skills Guardian, Reconciler y Setup | Incluidos en el Plugin Guardian instalado |
| `automatic-after-explicit-setup-apply` | Plugin All in Luna, wheel CLI fijado y 14 roles | El Plugin queda installed-but-unowned; tras un único `--apply` autorizado, solo wheel, roles y venv gestionado pueden tener receipt-backed ownership |
| `external-prerequisite` | macOS, Codex CLI, Python `>=3.11` para `portable-full`, Command Line Tools de macOS operativas (u otro proveedor confiable) para `/usr/bin/git` y HTTPS de GitHub | Proporcionados y confiados fuera de la transacción gestionada; Setup solo detecta el proveedor y nunca lo instala |
| `detect-then-separately-authorize` | Plugin Ponytail completo con hooks/Node, Headroom, integración Node y relay HostAdapter | Solo se detectan; nunca se instalan, activan ni confían silenciosamente |
| `account-dependent` | Inicio de sesión de Codex, entitlement/cuota del modelo y OMC privado | Requiere evidencia reciente de cuenta/capacidad |
| `none` | MCP (`mcp_servers: []`) | No se copia configuración MCP, servidor ni credencial |

## Autoridad y flujo de entrega

Invoca `$codex-workflow-guardian` explícitamente para una entrega. Lleva el
objetivo y el plan del usuario por el descubrimiento o reanudación de una
ejecución coincidente, la revisión exacta de la huella de TaskGraph y de los
host-slots compartidos, el enrutamiento acotado de autores, la recuperación tras
compact/resume y la aceptación separada de código fuente, pruebas y estado vivo.
Codex nativo es la autoridad para las acciones y el estado vivo.

All in Luna, OMC, Headroom y Ponytail son componentes opcionales cuya capacidad
se detecta. All in Luna es el runtime opcional y duradero de TaskGraph; Guardian
es el flujo de entrada de composición, permisos y aceptación. Cuando están disponibles, All in Luna posee el TaskGraph/Store,
dependencias, recuperación y finalización raíz duraderos coincidentes; OMC
posee el enrutamiento de autores Sol→Spark/Luna y la verificación independiente;
Headroom solo observa el transporte; y Ponytail `lite` es solo para el autor.
Las 14 plantillas de roles compatibles con OMC son plantillas saneadas de
enrutamiento, no la instalación completa/privada de OMC, sus Skills, el
entitlement del modelo ni su cuota.
Si falta una capacidad, aplica la regla de estado del propietario y nunca la
simula. El fallback nativo/manual solo se permite cuando no existe una ejecución
duradera, una búsqueda canónica reciente demuestra que no hay una ejecución activa
coincidente y no se ha emitido ningún `HostAction`. Cuando una ejecución duradera,
una ejecución activa coincidente o un `HostAction` emitido posee el alcance, conserva
ese propietario y detente; `ACTION_RELAY_REQUIRED` y `HOST_CAPABILITY_BLOCKED` son
resultados de propietario/bloqueo y nunca permiten fallback. `resume` debe cargar los
valores guardados `goal_ref`, `revision`, `intent_id` y `run_ref`; no vuelvas a buscar
por el texto del prompt ni regeneres `run_ref`. Un desajuste de Store/schema/digest es
`PROTOCOL_INTEGRITY_FAILURE`. Este repositorio no incluye servidor MCP, herramienta
remota ni servicio porque no existe una frontera de herramienta/servicio remoto.
Los Plugins instalados por Setup son observaciones de estado, no recursos propiedad del receipt: Codex CLI `0.146.0`
no demuestra qué operación creó un Plugin. Por eso eliminar un Plugin siempre es una operación Codex nativa separada,
confirmada explícitamente después de comprobar la preservación; los campos `owned_plugins` de receipts antiguos se ignoran.
All in Luna permanece `installed-but-unowned` hasta una confirmación separada y explícita de eliminación. Primero confirma la
identidad exacta del selector de Guardian y la línea base conservada; después, procede a eliminar solo el selector exacto de Guardian
mediante una operación Codex nativa separada y confirmada explícitamente. Este paso del selector no exige demostrar cero dependencias
del marketplace. Tras eliminar Guardian, captura una lista fresca posterior de Plugins (`codex plugin list --json`). La regla es:
solo entonces eliminar el marketplace si esa lista demuestra que ningún selector instalado restante depende de él; si queda una
dependencia, conserva el registro del marketplace y detente.

Guardian incorpora reglas mínimas `author-lite` para mantener la equivalencia
del flujo core sin Ponytail: separa roles de requisitos, implementación y
verificación, usa handoffs tipados y conserva evidencia independiente. Ponytail
solo es una mejora.

El Store duradero predeterminado es
`<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/allinluna-runtime.db`; cada
llamada CLI de All in Luna debe pasar esta ruta absoluta canónica con `--db`.
Primero congela una `goal_identity` no secreta que conserve la semántica del
resultado; sustituye credenciales, secretos, tokens sin procesar y PII/identificadores
de clientes por placeholders no secretos proporcionados por el llamador, como
`<credential-ref>`, `<artifact-ref>` o `<pii-ref>`. Nunca derives un placeholder del
valor protegido ni escribas el texto original en un ID o log. El `intent_id`
determinista es `cwfg-` más los primeros 16 caracteres hexadecimales minúsculos de
SHA-256 del JSON canónico que contiene `protocol`, el workspace canónico y esa
`goal_identity` congelada; es opaco y tiene 21 caracteres. El Store puede conservar
un `RunIntent` completo autorizado, pero su campo `goal` debe seguir saneado y los
valores protegidos solo pueden viajar mediante referencias autorizadas por separado.
Si no hay registro, se hace `start` una sola vez; una coincidencia activa permite
solo `status/reconcile/resume`; un registro terminal, un mismatch o varias
coincidencias detienen el flujo y exigen una nueva revisión explícita. Sin un
directorio Setup confiable y un CLI gestionado no se afirma una ejecución duradera.

La única prueba que permite un zero-match solo para core es el contrato legible
por máquina `host_capability.zero_match_evidence`. El registro exacto de
identidad es `<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/run-identities/<intent_id>.json`:
su directorio es `0700`, su entrada atómica `guardian-run/v1` es `0600` y debe
coincidir con el Store canónico y el `intent_id` exacto. La prueba
runtime-available exige rutas canónicas seguras, una consulta exacta del Store
exitosa, ningún `HostAction` emitido y resultados coherentes entre Store e
identidad; el primer `start` exitoso conserva atómicamente esa identidad no
secreta antes de cualquier dispatch. Si falla, conserva la ejecución nueva y
se detiene con `recovery-required`; cualquier discrepancia de Store, sidecar,
identidad, schema o digest es `PROTOCOL_INTEGRITY_FAILURE`.

Solo si All in Luna se prueba fresh como `unavailable` (no
`configured-unverified` ni `blocked`), el Store y sidecar canónicos y la
entrada exacta de identidad se prueban fresh como ausentes sin seguir
symlinks, no existen owner actual ni `HostAction` emitido y no existe ninguna
referencia conocida a un Store legacy/alternativo, se puede devolver
`FRESH_ZERO_MATCH` para un único flujo native/manual no durable y acotado, sin
afirmar exactly-once ni `resume`. Si el Store/identidad existe, es inseguro,
ilegible, inconsistente, o no se puede inspeccionar un Store legacy/alternativo
conocido, devuelve `OWNER_LOOKUP_BLOCKED` con fallback false. Nunca escanees el
disco arbitrariamente.

El Reconciler es una herramienta avanzada, saneada, de diagnóstico y solo
lectura. Nunca despacha, cierra, archiva, elimina, repara, publica, despliega ni
modifica un objetivo de ciclo de vida.

Publicar, desplegar, realizar acciones destructivas o ejecutar operaciones de
ciclo de vida requieren permiso puntual y exacto del usuario. Pasar las
pruebas de fuente no demuestra aceptación en tiempo real, en navegador o en
dispositivo; esas afirmaciones necesitan evidencia actual separada.

Frases en lenguaje natural como «全自动处理» o «coordinate this delivery» son
solo sugerencias de uso. No autorizan Setup, publicación, despliegue ni acciones
de ciclo de vida. Solo las llamadas exactas `$codex-workflow-guardian`,
`$reconcile-codex-subagents` y `$setup-codex-workflow-guardian` autorizan esos
Skills; los tres mantienen `allow_implicit_invocation: false`.

## Perfiles y límites de evidencia

| Perfil | Límite de aceptación |
| --- | --- |
| `core` | Plugin Guardian más los tres Skills: Guardian, Reconciler y Setup. |
| `portable-full` | `core` más el Plugin/CLI wheel de All in Luna fijado y 14 roles; Python `>=3.11`. Setup no instala Ponytail ni activa o confía en hooks; `--apply` llega solo a installed y la capacidad callable de HostAdapter se acepta después de la instalación con una tarea nueva. |
| `machine-integration` | skew de Desktop/CLI; presencia, versión, salud y enrutamiento de Headroom; Node aportado por el usuario cuando un hook de ciclo de vida lo necesite; hooks e inicio local. Requiere autorización y aceptación en tiempo real separadas. |
| `account-dependent` | inicio de sesión de Codex, entitlement/cuota del modelo y Skills OMC privados. Requiere un receipt nuevo. El acceso de red a GitHub es un prerrequisito externo separado y no demuestra capacidad de cuenta de Codex. |

Separa evidencia de fuente/pruebas, estado instalado, capacidad invocable y
comportamiento/vivo. Una prueba verde, un receipt instalado o plantillas de rol
no demuestran acceso al modelo ni comportamiento actual en navegador/log/DB/
dispositivo. portable-full busca equivalencia funcional pública; no copia byte a
byte el estado RC2/OMC/Headroom sucio de una máquina local. El destino es una
fuente de marketplace de repositorio Codex respaldada por GitHub, no una
afirmación de publicación en el Universal Public Plugins Directory.

No uses una etiqueta `ready` sin calificar. Informa `installation-ready` solo
cuando existan los archivos previstos y el receipt coincidente;
`capability-configured-unverified` cuando exista la configuración pero no un
receipt reciente de capacidad invocable; y `acceptance-installed` solo cuando
haya pasado la aceptación de instalación declarada. Ninguno de estos estados
demuestra el comportamiento vivo actual.

## Matriz de dependencias

| Componente | Línea base requerida o verificada | Límite |
| --- | --- | --- |
| Codex Desktop/CLI | Mínimo `0.146.0`; CLI local verificado `0.146.0` (la versión embebida de Desktop puede ser distinta) | Codex nativo es la autoridad de acciones y permisos; las formas JSON de `plugin list --json`, marketplace y `plugin add --json` solo se prueban en CLI `0.146.0`; la divergencia de schema falla cerrada |
| Python | `3.9` para los scripts y el Setup Skill; `>=3.11` para el CLI gestionado de All in Luna | No se necesita bytecode ni un resolvedor mutable |
| All in Luna | `2.0.0-rc.3`, commit `723088a7c0d7342f077ad675c6ea72d7e3996536`, Apache-2.0; SHA-256 del wheel `e2e59ce76deab1b39efe6feb1b64c983101c313268ce52dfa25787b77295dee1` | Runtime duradero opcional; solo wheel exacto, sin resolvedor de dependencias |
| Ponytail | `4.9.0`, commit `2ed6c52c9d7e5e56942508591085fd45dea277d3`, MIT | Los Skills de Ponytail no necesitan Node. Los hooks de ciclo de vida pueden requerir un Node aportado por el usuario; Setup no instala, activa ni confía en hooks, y machine-integration valida Node por separado |
| Roles compatibles con OMC | 14 plantillas saneadas (no OMC completa/privada): `code-reviewer`, `coder`, `debugger`, `document-specialist`, `executor`, `explore`, `luna-coder`, `luna-worker`, `security-reviewer`, `sol-lead`, `spark-verifier`, `test-engineer`, `tracer`, `verifier` | El entitlement del modelo es `configured-unverified` hasta recibir un receipt nuevo de rol/modelo/razonamiento |
| Headroom | `headroom-ai` `0.34.0`, repositorio canónico [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom), Apache-2.0, Python `>=3.10` | Solo detección/integración manual. Los hashes del artefacto y de dependencias transitivas no están fijados; presencia/versión no prueba readiness, salud, enrutamiento, lanzamiento, inicialización ni confianza |
| Node.js | Runtime `node` aportado por el usuario para `machine-integration` cuando un hook lo necesite | Los Skills de Ponytail no necesitan Node; solo los hooks pueden necesitarlo. Setup nunca lo instala ni confía automáticamente en hooks |
| Xcode Command Line Tools | Un proveedor operativo del `/usr/bin/git` fijo es obligatorio para `portable-full` | Prerrequisito externo; Setup solo lo detecta y nunca lo instala |
| HostAdapter / relay del host (`host_capability`) | Relay top-level exacto: `codex_app__create_thread` en Codex Desktop o `lane-direct` nativo en CLI/IDE | Setup no lo instala. Solo antes de cualquier ejecución/`HostAction`, tras una prueba canónica reciente de cero coincidencias, es elegible el fallback nativo/manual acotado. Con propietario y relay no demostrado, devuelve `ACTION_RELAY_REQUIRED`, conserva el propietario y detiene; con prueba reciente de ausencia de la herramienta exacta, devuelve `HOST_CAPABILITY_BLOCKED`, conserva el propietario y detiene. Ningún estado con propietario permite fallback. |
| Red de GitHub | Acceso HTTPS a la fuente de marketplace de repositorio Codex respaldada por GitHub y a los hosts de artefactos fijados | Solo conectividad; es independiente del inicio de sesión de Codex, entitlement/cuota del modelo y acceso OMC privado |
| MCP y otros servicios remotos | Ninguno requerido; `mcp_servers` es `[]` | Nunca copies configuración MCP del host, registros de servidores ni credenciales. Memos, Context7 y servicios externos similares están excluidos deliberadamente |

## Instalación avanzada y aceptación del mantenedor en macOS

La instalación tiene deliberadamente dos pasos y usa una referencia inmutable.
El objetivo de Setup es `portable-full`; el Plugin Guardian ya proporciona
`core`. La instalación normal debe usar el `CODEX_HOME` persistente que elijas
explícitamente. No uses un home temporal para la instalación normal ni esperes
que aparezca automáticamente en tus tareas habituales.

### Instalación persistente endurecida para mantenedor

Define un home persistente existente, los ejecutables de Codex y Git, un
ejecutable Python de confianza y el commit auditado exacto de 40 hex. Para
`portable-full`, `PYTHON_BIN` debe ser `>=3.11` y tener `venv` y `ensurepip`.
Usa un intérprete actual de confianza, uno explícito o solo estos candidatos
fijos de macOS: `/opt/homebrew/bin/python3.14` a `python3.11`, y después
`/usr/local/bin/python3.14` a `python3.11`. La selección automática de Setup
solo prueba esa lista fija; nunca ejecuta un lanzador Python arbitrario del PATH.

La confianza de los ejecutables es explícita: fija `GUARDIAN_CODEX_BIN`,
`GUARDIAN_GIT_BIN` y `PYTHON_BIN` a rutas canonical absolute; no los resuelvas
mediante el `PATH` ambiental. Codex nativo solo se ejecuta tras verificar la
firma OpenAI Developer ID. Un JS launcher de Homebrew solo puede ser un localizador
estático restringido del binario nativo versionado incluido; Setup nunca ejecuta
Node ni el wrapper JavaScript. El límite de confianza es root y el UID actual;
solo el prefijo Homebrew estándar permite `admin group-write` sin `extended ACL`.
No protege contra actores same-UID, root o trusted admin.

No hagas chmod ni muevas un `CODEX_HOME` normal existente solo para Setup. Se
acepta un home propiedad del UID actual con modo `0700`, `0750` o `0755` cuando
group/other no pueden escribir y no existe ACL extendida. Setup protege el
directorio sensible `workflow-guardian` con `0700` y sus archivos receipt,
journal y sidecar con `0600`; la aceptación aislada del mantenedor usa otro
home temporal `0700`.

El receipt en disco es `private-local`. stdout es solo una proyección
`public-redacted` y excluye rutas, hashes, valores device/inode, datos env y raw
logs. Si Setup falla, conserva receipt, journal y cualquier quarantine entry; no
los sobrescribas durante la investigación.
El contrato stdout es `codex-workflow-guardian/bootstrap-stdout/v1`: conserva
solo `schema`, `projection`, `generation`, `mode`, `platform`, `status`,
`canonical_source_verified`, `guardian_ref_verified`, `existing_receipt`,
`installation_status`, `capability_status`, `acceptance_level`, datos no
sensibles de `planned` (count/components/action), `conflict_summary`
(count/categories), resúmenes de nombre/status/version de componentes,
resúmenes de acciones rollback, notes, pares name/reason de conflicts, failure y
recovery. Excluye rutas absolutas/relativas, campos `*_relative`, valores
SHA/hash/digest/commit/selector, device/inode, ownership/provenance, valores de
entorno, credentials y logs sin procesar. En todos los modos, `existing_receipt`
es una observación al inicio de la invocación: `present` significa que Setup
validó correctamente un private receipt pair anterior antes de esta llamada;
no afirma que el pair siga existiendo tras un `--uninstall` exitoso. `absent`
significa que no había un pair anterior válido al inicio. `acceptance_level` usa
`preflight|installed|uninstalled|transaction-recovery`; `capability_status` permanece
`configured-unverified` hasta un receipt real de una tarea nueva. `planned` solo
describe All in Luna Plugin/CLI/venv y 14 roles, con count/action, nunca rutas ni hashes.

Añade el marketplace del repositorio Git-backed y guarda su JSON; no es una
instalación desde el Universal Public Plugins Directory. `GUARDIAN_CODEX_BIN`
debe apuntar directamente al binario Mach-O nativo firmado por el proveedor.
No uses `/opt/homebrew/bin/codex` ni `/usr/local/bin/codex` si alguno resuelve a
un wrapper de Node/JavaScript; el PATH restringido de abajo excluye Homebrew a
propósito. Resuelve el binario del proveedor para Apple Silicon o Intel desde
una instalación confiable y compruébalo antes de invocarlo:

```sh
GUARDIAN_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"  # revisa este objetivo persistente
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_REF="42db0a2c493b39bbf7f1661c2cc375f9b51af769"  # sustituye por el commit auditado de 40 hex
MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_ADD_JSON" && test -f "$MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$GUARDIAN_REF" --json >"$MARKETPLACE_ADD_JSON"; then
  echo "marketplace add failed; stop" >&2
  exit 1
fi
```

DETENTE. Abre `$MARKETPLACE_ADD_JSON` y verifica manualmente JSON válido, nombre/origen del marketplace, ref exacto y ausencia de registros inesperados.
No continúes si falta o cambia un campo. Solo después añade el Plugin Guardian:

```sh
PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$PLUGIN_ADD_JSON" && test -f "$PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$PLUGIN_ADD_JSON"; then
  echo "Guardian plugin add failed; stop" >&2
  exit 1
fi
```

DETENTE. Comprueba el schema JSON, el `pluginId` exacto, `name` y
`marketplaceName` esperados, `version` no vacío, `authPolicy` y
`installedPath` no vacío en `$PLUGIN_ADD_JSON`. Si falta o cambia cualquier
campo, detente; no adivines ni derives una ruta antes de esta inspección:

```sh
PLUGIN_INSTALLED_PATH="/absolute/path/from-the-installedPath-field"
test "$PLUGIN_INSTALLED_PATH" != "/absolute/path/from-the-installedPath-field" || exit 1
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test -f "$PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

DETENTE. Verifica bootstrap, lock, marketplace y las 14 plantillas de roles en la raíz inspeccionada. Inicia una tarea
nueva e invoca `$setup-codex-workflow-guardian` desde el Plugin instalado; nunca derives el apply desde un checkout fuente.
Ejecuta la capacidad Python y `--check` en un paso separado:

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

DETENTE. Revisa la proyección stdout `public-redacted` de `$CHECK_RECEIPT_JSON` y confirma
el `existing_receipt` observado al inicio: `absent` significa que no había un private receipt anterior
válido y solo se revisa el public logical plan; `present` significa que se inspecciona el fixed receipt pair
sin cambios validado desde un `--apply` anterior exitoso. Revisa provenance, schema, capacidades, conflictos y propiedad prevista.
Un check no cero no autoriza apply; solicita explícitamente el siguiente paso:

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

Revisa `$APPLY_RECEIPT_JSON` como proyección stdout `public-redacted` y el par de receipts
`private-local` vivo por separado. Los comandos solo escriben en el home elegido, no en este checkout.
Si falla, conserva receipt, transaction journal y cualquier quarantine entry; no los sobrescribas.

Guardian, Reconciler y Setup requieren invocación explícita con
`allow_implicit_invocation: false`. Los comandos de marketplace y Plugin
escriben en el home seleccionado, no en este checkout.

### Desinstalación ordinaria

Inicia una tarea nueva de Codex, invoca explícitamente `$setup-codex-workflow-guardian` y pídele una desinstalación segura.
El Skill descubre y valida por sí mismo la raíz/ref exacta del Plugin instalado, y solo elimina archivos de roles y el venv
gestionado de All in Luna cuando un receipt coincidente demuestra que son propiedad de Setup y no han cambiado. Todos los
Plugins añadidos por Setup, incluido All in Luna, permanecen `installed-but-unowned`; los campos `owned_plugins` antiguos
no pueden autorizar su eliminación. Tras comprobar la preservación, confirma la identidad exacta del selector de Guardian y la
línea base conservada; después, procede a eliminar solo el selector exacto de Guardian mediante una operación Codex nativa separada
y confirmada explícitamente. Este paso del selector no exige demostrar cero dependencias del marketplace. All in Luna permanece
`installed-but-unowned` hasta una confirmación separada de eliminación. Tras eliminar Guardian, captura una lista fresca posterior
de Plugins (`codex plugin list --json`); la regla es: solo entonces eliminar el marketplace si esa lista demuestra que ningún selector instalado
restante depende de él. Si queda una dependencia, conserva el registro del marketplace y detente. Ante cualquier conflicto o estado conservado
que no entiendas, detente y conserva la evidencia de receipt, journal y quarantine.

`plugin marketplace list --json` por sí solo solo demuestra que existe el registro.

La siguiente secuencia shell larga es solo para mantenedores que realizan recuperación aislada o un cutover auditado; no es la ruta ordinaria del usuario.

### Desinstalación de mantenedor/recuperación (avanzada)

Usa la raíz exacta del Plugin instalado que devuelve `codex plugin add --json`; nunca derives esta ruta desde un checkout fuente. La desinstalación de Setup solo cubre archivos de roles y el venv gestionado que el receipt demuestre como propios y sin cambios. Setup siempre conserva los selectores de Plugin; tras las comprobaciones de preservación siguientes, elimina un Plugin únicamente mediante una operación Codex separada y confirmada explícitamente. Si falla cualquier paso, conserva la evidencia de receipt, journal y quarantine.

```sh
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_CODEX_HOME="/absolute/path/to/explicit-codex-home"
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
GUARDIAN_REF="42db0a2c493b39bbf7f1661c2cc375f9b51af769"
PLUGIN_INSTALLED_PATH="/absolute/path/from-plugin-add-installedPath"
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test "$PLUGIN_INSTALLED_PATH" != "/absolute/path/from-plugin-add-installedPath" || exit 1
test -f "$PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
UNINSTALL_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.uninstall.XXXXXX")"
test -n "$UNINSTALL_RECEIPT_JSON" && test -f "$UNINSTALL_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --guardian-ref "$GUARDIAN_REF" >"$UNINSTALL_RECEIPT_JSON"; then
  echo "uninstall failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

DETENTE. Inspecciona `$UNINSTALL_RECEIPT_JSON` y el receipt/journal privado. Detente ante cualquier conflicto, resultado parcial, cambio de propiedad o estado conservado que no entiendas; la desinstalación propiedad del receipt nunca autoriza borrar contenido ajeno.

```sh
POST_UNINSTALL_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-uninstall-plugin-list.XXXXXX")"
test -n "$POST_UNINSTALL_PLUGIN_LIST_JSON" && test -f "$POST_UNINSTALL_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_UNINSTALL_PLUGIN_LIST_JSON"; then
  echo "post-uninstall plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

DETENTE. Exige JSON válido, confirma que el selector exacto de Guardian sigue presente y verifica que las filas no Guardian conservadas y sus identidades de origen no cambiaron antes de eliminar solo el selector exacto de Guardian. Este paso del selector no exige demostrar cero dependencias del marketplace.

```sh
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin remove codex-workflow-guardian@onebigmoon-codex-workflows; then
  echo "Guardian selector removal failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

DETENTE. Confirma que desapareció el selector exacto de Guardian. Si cambió cualquier otro selector, detente.

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

DETENTE. Exige JSON reciente válido posterior a eliminar Guardian. Solo entonces elimina el marketplace si la lista fresca de Plugins
demuestra que ningún selector instalado sigue haciendo referencia a `onebigmoon-codex-workflows`; si queda una dependencia, conserva el
registro del marketplace y detente. El marketplace list de abajo solo prueba el registro.

```sh
MARKETPLACE_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace-list.XXXXXX")"
test -n "$MARKETPLACE_LIST_JSON" && test -f "$MARKETPLACE_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace list --json >"$MARKETPLACE_LIST_JSON"; then
  echo "marketplace list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

DETENTE. Elimina este marketplace solo después de que la lista fresca posterior demuestre que ningún Plugin instalado ni dependencia conservada usa `onebigmoon-codex-workflows`; de lo contrario conserva el registro y detente.

```sh
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace remove onebigmoon-codex-workflows; then
  echo "marketplace removal failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

DETENTE. Confirma que solo se eliminó el registro exacto del marketplace. Si falla, conserva `$UNINSTALL_RECEIPT_JSON`, el receipt privado, el transaction journal y la quarantine entry.

### Aceptación aislada para mantenedores

Usa un home temporal nuevo solo para verificar una release. Es independiente
de la instalación normal y no cambia automáticamente las tareas habituales.
Aplica el mismo límite Python explícito y los candidatos fijos 3.11–3.14. Instala
primero el Plugin exacto y deriva `SETUP_SKILL_DIR` solo desde la raíz indicada
por el `installedPath` de un `codex plugin add --json` exitoso. Un checkout fuente
solo sirve para validación de solo lectura cuando es un checkout tracked limpio
del commit exacto auditado, con verificación completa de archivos/ hashes, raíz
canónica, propiedad root/UID actual, sin modos world-writable o group-writable no
autorizados y sin ACL extendida. Bootstrap, doctor y postflight deben proceder de
la misma raíz confiable. El Setup normal y el `--apply` canónico deben usar
bootstrap, lock, marketplace y 14 roles del Plugin instalado:

```sh
GUARDIAN_CODEX_HOME="$(/usr/bin/mktemp -d "/tmp/codex-workflow-guardian.XXXXXX")"
test -n "$GUARDIAN_CODEX_HOME" && test -d "$GUARDIAN_CODEX_HOME" || exit 1
GUARDIAN_CODEX_HOME="$(cd "$GUARDIAN_CODEX_HOME" && pwd -P)" # ruta canónica de macOS
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_REF="42db0a2c493b39bbf7f1661c2cc375f9b51af769"  # commit auditado exacto de 40 hex
MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_ADD_JSON" && test -f "$MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$GUARDIAN_REF" --json >"$MARKETPLACE_ADD_JSON"; then
  echo "marketplace add failed; stop" >&2
  exit 1
fi
```

DETENTE. Inspecciona `$MARKETPLACE_ADD_JSON`: JSON, schema, fuente/nombre y ref exactos. Si falta algo, para.

```sh
PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$PLUGIN_ADD_JSON" && test -f "$PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$PLUGIN_ADD_JSON"; then
  echo "Guardian plugin add failed; stop" >&2
  exit 1
fi
```

DETENTE. Comprueba el schema, el `pluginId` esperado, `name`, `marketplaceName`,
`version`, `authPolicy` y `installedPath` no vacíos:

```sh
PLUGIN_INSTALLED_PATH="/absolute/path/from-the-installedPath-field"
test "$PLUGIN_INSTALLED_PATH" != "/absolute/path/from-the-installedPath-field" || exit 1
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test -f "$PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

DETENTE. Confirma bootstrap, lock, marketplace y 14 roles. Inicia una tarea nueva e invoca Setup desde el Plugin instalado; no derives apply desde el checkout. Ejecuta probe y `--check` por separado:

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

DETENTE. Revisa `$CHECK_RECEIPT_JSON` como proyección stdout `public-redacted` y confirma
el `existing_receipt` observado al inicio: `absent` requiere revisar solo el public logical plan; `present`
permite inspeccionar el fixed receipt pair sin cambios validado desde el `--apply` anterior exitoso. Autoriza explícitamente el apply solo después
de revisar provenance, schema, capacidades y propiedad:

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

Revisa `$APPLY_RECEIPT_JSON` y el par de receipts vivo por separado. Los comandos escriben en el home elegido, no en este checkout.
Guardian, Reconciler y Setup requieren invocación explícita con `allow_implicit_invocation: false`.

`--guardian-ref` debe ser un commit exacto de 40 hex. `--check` no escribe
directamente contenido gestionado, pero ejecuta sondas validadas de Codex/Python
y los programas externos pueden mantener su propio estado; la aceptación de
release compara el árbol, mtimes y hashes de un objetivo aislado nuevo antes y
después. Termina con código distinto de cero cuando hay cambios, conflictos o
capacidades obligatorias ausentes. Revisa el receipt antes de ejecutar `--apply`.
La CLI solo expone `--check`, `--apply` y `--uninstall`. `--uninstall` elimina
solo archivos de roles y el venv gestionado sin modificar que un receipt
coincidente demuestre como propios; nunca elimina un selector de Plugin. Cada
Plugin añadido por Setup, incluido All in Luna, queda installed-but-unowned y
los campos `owned_plugins` antiguos se ignoran. El contenido gestionado
preexistente o modificado se conserva y se informa como conflicto.
Cuando hay Python `>=3.11`, el receipt expone el ejecutable gestionado de All in
Luna en `venvs/allinluna/bin/allinluna` del objetivo explícito. Los hooks de
Ponytail nunca se activan ni se confían automáticamente: revísalos y actívalos
explícitamente.

Después del Setup inicia una tarea nueva de Codex y exige receipts de rol,
modelo y razonamiento antes de considerar configurada una lane compatible con
OMC. Una plantilla configurada no demuestra acceso al modelo, enrutamiento de
Headroom, activación de hooks ni finalización duradera de All in Luna.

Para cambiar un ref inmutable, trata el registro de marketplace con el mismo
nombre como parte de ese ref. El Codex CLI actual no tiene una opción `--ref`
para `plugin marketplace upgrade`; primero captura una lista completa con
`plugin list --json` y deriva una línea base conservada: cada fila de Plugin
instalada (`pluginId`, estado installed e identidad de origen) forma parte de la
comprobación de conservación. Codex CLI 0.146.0 no proporciona prueba de
creador/cambio, así que ningún selector se excluye como propiedad del receipt.
Completa después el uninstall del receipt antiguo solo para archivos de roles y
venv, y elimina el Plugin
Guardian y ese registro, y vuelve a añadirlo con `--ref "$NEW_GUARDIAN_REF"`.
Este cambio no autoriza eliminar otros Plugins. Durante la ventana de registro
vacío del marketplace, los Plugins conservados pueden quedar temporalmente no
descubribles; esa advertencia solo aplica a esa ventana y nunca es prueba de
eliminación. Tras volver a añadir el
marketplace, compara `plugin list --json` con la línea base y detente ante JSON
o schema inválido, cualquier fila ausente/no instalada o un cambio de origen
inesperado.

Antes del cambio, captura/archiva el receipt antiguo activo
`workflow-guardian/bootstrap-receipt.json` y su sidecar
`workflow-guardian/bootstrap-receipt.sha256` como evidencia de auditoría, y
conserva la raíz del Plugin antiguo y su ruta de Setup derivada. Un `--uninstall` antiguo
exitoso consume/elimina el par de receipts activo; no queda disponible para
reutilizarlo tras la desinstalación. No restaures manualmente el par archivado.
El `--uninstall` antiguo conserva todos los Plugins, incluido All in Luna; la
eliminación de un Plugin requiere la operación Codex separada y confirmada tras
la comprobación de preservación. Una instalación exacta preexistente también se
conserva y no se reclama ni elimina silenciosamente. Define la
ruta del Setup nuevo solo después de que el nuevo `plugin add --json` termine
correctamente y su schema haya sido inspeccionado, derivándola desde su
`installedPath` (la raíz del Plugin);
conserva ambas rutas hasta que el nuevo `--check` y `--apply` terminen
correctamente. La secuencia de cambio hacia delante es manual y no hay rollback
automático:

```sh
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_CODEX_HOME="/absolute/path/to/explicit-codex-home"
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PLUGIN_BASELINE_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin-baseline.XXXXXX")"
test -n "$PLUGIN_BASELINE_JSON" && test -f "$PLUGIN_BASELINE_JSON" || exit 1
OLD_PLUGIN_INSTALLED_PATH="/absolute/path/from-old-plugin-add-installedPath"
OLD_SETUP_SKILL_DIR="$OLD_PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
OLD_GUARDIAN_REF="OLD_AUDITED_COMMIT_SHA"  # commit viejo exacto de 40 hex
NEW_GUARDIAN_REF="NEW_AUDITED_COMMIT_SHA"  # commit nuevo exacto de 40 hex
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json > "$PLUGIN_BASELINE_JSON"
```

DETENTE. Inspecciona el JSON completo y el receipt antiguo. Conserva todas las filas de Plugins instaladas, incluido All in Luna:
Codex CLI 0.146.0 no aporta prueba de creador/cambio y ningún selector puede excluirse como propiedad del receipt.

```sh
"$PYTHON_BIN" -I -B "$OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF"
```

DETENTE. Revisa el receipt del uninstall antiguo; un conflicto o fallo parcial detiene el cambio.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

DETENTE. Confirma que el selector exacto de Guardian sigue presente y la línea base no cambió; después, elimina solo el selector exacto de Guardian. Este paso no exige demostrar cero dependencias del marketplace.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin remove \
  codex-workflow-guardian@onebigmoon-codex-workflows
```

DETENTE. Confirma la eliminación del selector Guardian y captura una lista fresca posterior de Plugins. Solo entonces elimina el marketplace
si esa lista demuestra que ningún selector instalado sigue haciendo referencia a `onebigmoon-codex-workflows`; si queda una dependencia,
conserva el marketplace y detente. El marketplace list de abajo solo prueba el registro:

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace list --json
```

DETENTE. Solo si existe el registro exacto y el schema es válido, elimínalo:

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace remove \
  onebigmoon-codex-workflows
```

DETENTE. Confirma la eliminación del registro y vuelve a añadir el marketplace nuevo, guardando su JSON:

```sh
MARKETPLACE_READD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_READD_JSON" && test -f "$MARKETPLACE_READD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$NEW_GUARDIAN_REF" --json >"$MARKETPLACE_READD_JSON"; then
  echo "new marketplace add failed; stop" >&2
  exit 1
fi
```

DETENTE. Inspecciona `$MARKETPLACE_READD_JSON` y valida schema, fuente/nombre canónicos y ref nuevo:

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

DETENTE. Compara con la línea base; todas las filas conservadas, incluido All in Luna, deben seguir instaladas con su origen esperado.
Ningún Plugin puede faltar por el uninstall de Setup. Luego añade el Plugin nuevo:

```sh
NEW_PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$NEW_PLUGIN_ADD_JSON" && test -f "$NEW_PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$NEW_PLUGIN_ADD_JSON"; then
  echo "new Guardian plugin add failed; stop" >&2
  exit 1
fi
```

DETENTE. Inspecciona `$NEW_PLUGIN_ADD_JSON`: schema, `pluginId`, `name`,
`marketplaceName`, `version`, `authPolicy` e `installedPath` no vacíos. Solo después valida el bundle:

```sh
NEW_PLUGIN_INSTALLED_PATH="/absolute/path/from-new-plugin-add-installedPath"
NEW_SETUP_SKILL_DIR="$NEW_PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test "$NEW_PLUGIN_INSTALLED_PATH" != "/absolute/path/from-new-plugin-add-installedPath" || exit 1
test -f "$NEW_PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$NEW_SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$NEW_SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

DETENTE. Confirma bootstrap, lock, marketplace y 14 roles. Ejecuta `--check` en un paso separado:

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

DETENTE. Revisa `$CHECK_RECEIPT_JSON` y autoriza apply explícitamente solo tras esa revisión:

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "new Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

Revisa `$APPLY_RECEIPT_JSON` y el par de receipts vivo antes de declarar terminado el cambio.

### Rollback manual tras un fallo del cambio

Ejecuta los siguientes snippets de rollback solo después de que falle el cambio
hacia delante; no son una continuación de un cambio exitoso. Usa las mismas
variables explícitas anteriores. El receipt antiguo archivado es solo evidencia
de auditoría y no debe restaurarse manualmente. Cada operación destructiva
requiere una inspección JSON previa y una pausa humana.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

DETENTE. Si el selector Guardian nuevo está presente y existe el receipt nuevo, confirma que Setup solo puede desinstalar archivos de roles y venv propiedad del receipt; ningún selector de Plugin es elegible. Comprueba los Plugins por separado.

```sh
"$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF"
```

DETENTE. Revisa el receipt de uninstall nuevo; si hay conflicto o fallo parcial, para. Inspecciona de nuevo la lista:

Detente también ante propiedad inesperada y confirma que Setup no eliminó ningún selector de Plugin. La ausencia temporal
solo puede explicarse por la ventana de registro vacío del marketplace; no es prueba de eliminación ni relaja la comprobación.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

DETENTE. Confirma la línea base; solo si el selector exacto de Guardian sigue presente, elimina solo el selector exacto de Guardian. Este paso no exige demostrar cero dependencias del marketplace:

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin remove \
  codex-workflow-guardian@onebigmoon-codex-workflows
```

DETENTE. Confirma la eliminación del selector Guardian y captura una lista fresca posterior de Plugins. Solo entonces elimina el marketplace si esa lista demuestra que ningún selector instalado sigue haciendo referencia a `onebigmoon-codex-workflows`; si queda una dependencia, conserva el marketplace y detente. El marketplace list de abajo solo prueba el registro:

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace list --json
```

DETENTE. Solo si el registro exacto existe y el schema es válido, elimínalo:

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace remove \
  onebigmoon-codex-workflows
```

DETENTE. Confirma la eliminación del registro y vuelve a añadir el marketplace antiguo guardando su JSON:

```sh
RESTORED_MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$RESTORED_MARKETPLACE_ADD_JSON" && test -f "$RESTORED_MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$OLD_GUARDIAN_REF" --json >"$RESTORED_MARKETPLACE_ADD_JSON"; then
  echo "old marketplace re-add failed; stop" >&2
  exit 1
fi
```

DETENTE. Inspecciona `$RESTORED_MARKETPLACE_ADD_JSON` y valida schema, fuente/nombre canónicos y ref antiguo; después compara la línea base:

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

DETENTE. Confirma que todos los rows no-Guardian conservados, incluido All in Luna, siguen installed con el origen esperado; vuelve a añadir Guardian:

```sh
RESTORED_PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$RESTORED_PLUGIN_ADD_JSON" && test -f "$RESTORED_PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$RESTORED_PLUGIN_ADD_JSON"; then
  echo "old Guardian plugin re-add failed; stop" >&2
  exit 1
fi
```

DETENTE. Inspecciona `$RESTORED_PLUGIN_ADD_JSON`: schema, `pluginId`, `name`,
`marketplaceName`, `version`, `authPolicy` e `installedPath` no vacíos. Nunca reutilices el `OLD_PLUGIN_INSTALLED_PATH` pre-corte; deriva y verifica la ruta restaurada:

```sh
RESTORED_OLD_PLUGIN_INSTALLED_PATH="/absolute/path/from-restored-old-plugin-add-installedPath"
RESTORED_OLD_SETUP_SKILL_DIR="$RESTORED_OLD_PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test "$RESTORED_OLD_PLUGIN_INSTALLED_PATH" != "/absolute/path/from-restored-old-plugin-add-installedPath" || exit 1
test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$RESTORED_OLD_SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$RESTORED_OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$RESTORED_OLD_SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

DETENTE. Confirma bootstrap, lock, marketplace y 14 roles restaurados. Ejecuta el `--check` del Setup antiguo:

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$RESTORED_OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

DETENTE. Revisa `$CHECK_RECEIPT_JSON`; un check no cero no autoriza apply. Solo después de pedirlo explícitamente ejecuta:

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$RESTORED_OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "restored old Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

Revisa el par de receipts restaurado. No restaures el par archivado, no elimines otros Plugins ni borres rutas fuera del estado propiedad del receipt de roles/venv.

### Alternativa independiente de tres Skills (solo commit auditado exacto)

Si no quieres el Plugin, `$skill-installer` o el script directo deben instalar
Guardian, Reconciler y Setup en el mismo `42db0a2c493b39bbf7f1661c2cc375f9b51af769` exacto; nunca uses
`main`:

```sh
SKILL_INSTALLER="/path/to/install-skill-from-github.py"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
"$PYTHON_BIN" -I -B "$SKILL_INSTALLER" \
  --repo OneBigMoon/codex-subagent-reconciler \
  --ref 42db0a2c493b39bbf7f1661c2cc375f9b51af769 \
  --path skills/codex-workflow-guardian skills/reconcile-codex-subagents \
         skills/setup-codex-workflow-guardian
```

Esta alternativa es `skill-only`, no `core`: escribe los tres directorios de
Skill en el objetivo seleccionado por el instalador, pero no crea el Plugin
Guardian instalado ni su proveniencia git del marketplace. No afirmes `core` ni
`portable-full` desde esta ruta. El Setup Skill independiente es solo una entrada
documental/diagnóstica; su `--apply` falla de forma segura como
`configured-unverified` hasta que exista un Plugin Guardian canónico instalado y
la proveniencia git coincidente.

## Proveniencia de Guardian

portable-full requiere el repositorio GitHub canónico
`https://github.com/OneBigMoon/codex-subagent-reconciler`, un `GUARDIAN_REF`
exacto de 40 hex auditado por el usuario, un único Guardian instalado devuelto
por el resolver de la fuente de marketplace de repositorio Codex respaldada por
GitHub, la URL/root git canónica del marketplace y un Git HEAD del checkout
igual al ref. Lock y marketplace deben coincidir exactamente en repositorios,
rutas, refs y hashes de artefactos. El marketplace local incluido solo sirve
para desarrollo/pruebas y no afirma publicación en el Universal Public Plugins
Directory. Si el resolver no prueba la proveniencia canónica,
`portable-full --apply` falla de forma segura como `configured-unverified`. El
receipt registra operación y propiedad, no identidad del publicador,
autorización ni autoridad de firma.

## Reconciliador de solo lectura

Usa `$reconcile-codex-subagents` solo con JSON sintético o saneado. `doctor.py`
lee un único archivo regular explícito. `postflight.py` lee un único archivo
before y un único archivo after explícitos.
El esquema v1 permanece para diagnóstico. Las comprobaciones operativas estrictas
v2 usan un `scope` generado por el llamador y un `record_key` estable; son claves
de correlación, nunca objetivos de ciclo de vida accionables. `v1` conserva
compatibilidad diagnóstica: un evento desconocido se informa como
`unsupported-event` y termina con `2`; `v2` estricto rechaza los tipos de evento
desconocidos durante la validación del esquema.

```sh
RECONCILER_SKILL_DIR="/path/to/reconcile-codex-subagents"  # directorio que contiene este SKILL.md
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/doctor.py" \
  --input snapshot.json --json
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/postflight.py" \
  --before before-v2.json --after after-v2.json \
  --record-key record-1 --json
```

Establece `RECONCILER_SKILL_DIR` solo desde el `installedPath` devuelto por
`codex plugin add --json` para el Plugin Guardian instalado. Verifica el manifest,
el ref inmutable, la procedencia canónica del marketplace y todo el conjunto lock/roles;
un checkout no verificado no es una raíz operativa normal. La validación de solo
lectura del mantenedor exige un checkout tracked limpio del commit auditado exacto,
verificación completa de archivos/hashes, propiedad/permisos/ACL y doctor/postflight
desde esa misma raíz. Estos comandos no dependen del directorio de trabajo del repositorio.

El código `0` significa consistente/aprobado, `1` incompatibilidad/fallo y `2`
inválido, ambiguo, inconcluyente o no compatible. Un before incongruente pero
concluyente puede ser una línea base de reparación, pero el after debe ser
consistente y el registro elegido debe cambiar realmente con una secuencia hacia
delante. Cada registro resuelto también debe corresponder a su UI (`running` con
`live_state: running` y UI `active`, `terminal` con `live_state: terminal` y UI
`done`); las filas opuestas no pueden cancelarse en el agregado. v2 comprueba
tanto los conteos live derivados como los conteos independientes de filas UI
visibles, por lo que un agregado no puede ocultar una fila seleccionada obsoleta.
Registros terminales sin cambio, drift de scope o key, epochs antiguos,
`not_found` más nuevo, campos desconocidos, claves duplicadas, controles,
identificadores inseguros y archivos inseguros cierran de forma segura.

Los informes sustituyen identificadores, pero la redacción no es anonimización.
La salida del snapshot y todos los informes JSON o de texto v1/v2 llevan
`trust: unauthenticated-consistency-evidence`; nunca son autorización ni verdad
del protocolo. Las clasificaciones públicas usan etiquetas de snapshot-claim.
Los scripts incluidos usan solo la biblioteca estándar, son
acotados, no usan red ni sondeo y no escriben. Nunca cierran, interrumpen,
archivan, eliminan, reparan, despliegan ni modifican un objetivo de ciclo de vida.

## Niveles de aceptación

Mantén separados los perfiles y los conjuntos de evidencia:

- **`core`:** Plugin Guardian más los Skills Guardian, Reconciler y Setup.
- **`portable-full`:** `core` más el Plugin/CLI wheel fijado de All in Luna y
  14 roles; Python `>=3.11`. Setup no instala Ponytail ni activa o confía en
  hooks; ambos requieren autorización de machine-integration y aceptación en tiempo real separadas.
- **`machine-integration`:** skew de Desktop/CLI, presencia/versión/salud/enrutamiento de Headroom, Node aportado por el usuario cuando un hook de ciclo de vida lo necesite, hooks e inicio local; los Skills de Ponytail no necesitan Node y los hooks nunca se confían automáticamente; autorización y aceptación en tiempo real separadas.
- **`account-dependent`:** inicio de sesión de Codex, entitlement/cuota del modelo y Skills OMC privados; receipt nuevo obligatorio. El acceso de red a GitHub es un prerrequisito externo separado y no demuestra estas capacidades de cuenta.

La fuente/pruebas (repositorio y validators), el estado instalado (resolver,
lock, hashes, rutas, receipt), la capacidad invocable (Skills y plantillas de
rol) y el comportamiento/vivo (tarea nueva y evidencia de navegador/log/DB/
dispositivo) son evidencias distintas. La entrega Git reproducible también se
registra aparte. El éxito es equivalencia funcional pública, no copiar byte a
byte el estado RC2/OMC/Headroom sucio de una máquina local.

Los sidecars SHA de receipts y journals solo detectan corrupción o deriva de
schema; no autentican registros ni impiden que un actor con el mismo UID y
permiso de escritura en `CODEX_HOME` cambie tanto el registro como su sidecar.
Setup acepta únicamente allowlists fijas de rutas/Plugins gestionados y vuelve a
observar el estado vivo antes y después de operaciones sensibles a propiedad.

## Aceptación y seguridad

Ejecuta dos suites unitarias, tres validadores rápidos de Skill, un validador
del Plugin, tres comandos `--help`, comprobaciones de constantes JSON/TOML/README,
ausencia de bytecode y `git diff --check`. Los validators oficiales locales
pueden no estar disponibles en un runner CI sin red ni rutas globales de Skills;
en ese caso son un release gate explícito, no una afirmación de que CI los ejecutó.
Estas comprobaciones solo establecen evidencia de fuente/pruebas; no afirman
despliegue ni disponibilidad en vivo actual. Consulta [`skills/reconcile-codex-subagents/SKILL.md`](skills/reconcile-codex-subagents/SKILL.md)
para los esquemas exactos y [`SECURITY.md`](SECURITY.md) para el límite de seguridad.
