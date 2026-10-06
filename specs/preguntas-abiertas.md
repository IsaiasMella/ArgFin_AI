# Preguntas abiertas

Dudas detectadas al leer las specs. Cada una indica a qué tarea bloquea. No se inventan
respuestas: se resuelven con el responsable del producto y se actualiza la spec.

## Estructura y repositorio

1. **`spec/` vs `specs/`.** `AGENTS.md`, el plan técnico (sección 3) y las tareas (T3.1)
   hablan de `specs/`, pero la carpeta se llama `spec/`. ¿Se renombra la carpeta o se
   corrigen las referencias? *(Este archivo quedó en `spec/` hasta decidir.)*
2. **Remoto de GitHub.** T0.6 (CI con GitHub Actions) necesita un repositorio remoto.
   ¿Existe ya? ¿Público o privado?

## RLS y autenticación (bloquea T0.4 y T1.1)

3. **Login con RLS sobre `users` y `sessions`.** Para iniciar sesión hay que buscar el
   usuario por `sub_oauth` y la sesión por `token_hash` *antes* de conocer el `user_id`,
   y la política `user_id = current_setting('app.current_user_id')` lo impide. Como el plan
   dice que "no existe un rol que saltee RLS", propongo funciones `SECURITY DEFINER`
   acotadas (p. ej. `resolver_sesion(token_hash)`, `upsert_usuario_oauth(sub, email)`),
   documentadas en un ADR. ¿Aprobado?
4. **Worker que recorre usuarios.** El resumen semanal y el envío trimestral necesitan listar
   usuarios activos (y "quiénes tienen la empresa X"). Mismo problema que el punto 3: ¿se
   resuelve también con funciones `SECURITY DEFINER` que devuelven solo IDs?
5. **`payment_intents` de visitantes anónimos** (`visitor_id`, sin `user_id`): la política
   RLS por `user_id` no aplica. ¿Se guardan en una tabla separada sin datos personales?
6. **Rol de administrador (HU-07).** El modelo de datos no tiene columna de rol. ¿Se define
   por lista de emails en `.env` (p. ej. `ADMIN_EMAILS`), por columna `users.rol`, o solo
   por comando de CLI?

## Producto

7. **Informes trimestrales y fake door.** El plan gratuito no incluye informes trimestrales
   y durante el MVP no se cobra. ¿Durante la validación todos reciben informes trimestrales,
   o nadie los recibe y solo se muestran en el ejemplo público?
8. **Universo concreto.** ¿Qué 20 acciones del panel líder y qué 20 CEDEARs? ¿"Más operados"
   según qué fuente y en qué ventana de tiempo? (bloquea T2.1)

## Reglas de texto generado (bloquea T5.2)

9. **Lenguaje prohibido vs. hechos.** "La empresa *vendió* su participación" o "*compró*
   activos" son hechos, no recomendaciones. ¿Se prohíben todas las flexiones de
   "comprar"/"vender", solo el infinitivo/imperativo, o se aplica una lista de excepciones?
10. **Dígitos fuera de marcadores.** Nombres propios y períodos contienen dígitos
    ("3M", "G20", "COVID-19", "2T26", "Ley 25.326"). ¿Se usa una lista blanca configurable
    o todo nombre con dígitos debe ir como marcador?
11. **Cifras de noticias.** Una noticia macro ("el BCRA llevó la tasa al X %") trae cifras
    que no están en la base. ¿El `resumen_propio` omite cifras, o se extraen como datos
    con fuente para poder usar marcadores?

## Configuración faltante en `.env.example`

12. **Zona horaria de los cron** (`CRON_*`): ¿se asume `America/Argentina/Buenos_Aires`?
    ¿Se agrega una variable `TIMEZONE`?
13. **Almacenamiento de PDFs** (`documents.ruta_almacenada`): no hay variable para el
    directorio o bucket. ¿Disco del VPS (y entra en los backups) u objeto externo?
14. **Proveedor de embeddings.** Anthropic no ofrece embeddings; ¿`LLM_EMBEDDING_MODEL` es
    de OpenAI? (justifica que existan las dos claves).
