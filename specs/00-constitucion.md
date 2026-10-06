# 00 — Constitución del proyecto

Principios no negociables. Si cualquier spec, plan o tarea entra en conflicto con este documento, este documento gana.

## 1. Información, nunca recomendación

- El producto **informa**: qué pasó, qué cambió, qué viene. **Nunca** recomienda comprar, vender, mantener, hacer short ni sugiere precios objetivo, ni de forma explícita ni implícita.
- Las "señales" son **reglas determinísticas y documentadas** sobre datos (por ejemplo, "la deuda neta sobre EBITDA subió tres trimestres seguidos"). El LLM no inventa señales ni opina.
- Lo positivo y lo negativo se presentan de forma **simétrica**: todo informe muestra puntos fuertes y puntos débiles, seleccionados y ordenados por reglas determinísticas. Nunca se elige qué resaltar según una tesis de compra o venta.
- Lista de términos prohibidos en cualquier texto generado (validada por código): "comprar", "vender", "recomendamos", "oportunidad", "conviene", "precio objetivo", "sobreponderar", "subponderar", "short", y equivalentes configurados en `config/lenguaje_prohibido.yaml`.
- Todo informe y la landing incluyen el disclaimer legal configurado.

## 2. Los números los pone el código, no el LLM

- Ninguna cifra de un informe es escrita por un LLM. El LLM redacta texto con **marcadores** (`{{metric:...}}`) y el código los reemplaza con valores de la base de datos.
- Si un texto generado contiene dígitos fuera de marcadores, se rechaza y se reintenta.
- Toda cifra mostrada debe poder rastrearse hasta su documento fuente (documento, página o campo XBRL).

## 3. Exactitud contable

- Toda cifra extraída guarda **moneda, unidad (miles, millones), período y base de medición** (nominal o moneda homogénea con fecha de reexpresión).
- Las comparaciones trimestrales y anuales usan **siempre las cifras comparativas del último estado contable publicado** (reexpresadas), nunca valores de documentos anteriores.

## 4. Evals antes de escalar

- Ningún componente con LLM pasa a producción sin un dataset de referencia y una métrica con umbral definido.
- El universo de empresas solo se amplía cuando la extracción supera el umbral de calidad en el dataset de referencia.

## 5. Costo por documento, no por usuario

- Cada documento se procesa **una sola vez** y su resultado se reutiliza para todos los usuarios.
- Se registran costo, tokens y latencia de cada llamada a un LLM.

## 6. Seguridad y privacidad

- Secretos solo en `.env`; `.env.example` sin valores reales; `.env` en `.gitignore`.
- Row Level Security en toda tabla con datos de usuario.
- Datos de portafolio cifrados a nivel de campo.
- Nunca se piden credenciales de brokers.
- Nada hardcodeado (ver `AGENTS.md`).

## 7. Simplicidad deliberada

- Monolito modular, organizado por feature. Sin microservicios.
- Sin dependencias que no resuelvan un problema concreto de este proyecto (por ejemplo, sin LangChain en el núcleo: el sistema es un pipeline fijo, no un agente).
- Todo lo que esté en `ROADMAP.md` queda fuera del MVP.
