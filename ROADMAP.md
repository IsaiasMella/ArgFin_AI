# ROADMAP — Versiones futuras

Nada de este documento se implementa en el MVP. Cada ítem indica de dónde surgió.

## Versión 1.1 (después de validar el MVP)

- **Alertas inmediatas** para hechos relevantes, resultados y movimientos fuertes de precio, con latencia objetivo menor a una hora (no tiempo real). *Origen: definición de frecuencias.*
- **Monitoreo de regulación CNV y BCRA**, integrado como una fuente más del mapa de exposición (factores "regulación financiera", "regulación de mercado de capitales"). *Origen: idea del radar regulatorio.*
- **Eventos corporativos completos:** dividendos, splits, cambios de ratio de CEDEARs, con ajuste del rendimiento histórico. *Origen: asesor financiero.*
- **Separación del rendimiento de los CEDEARs** entre la variación de la acción de origen y la del dólar CCL. *Origen: asesor financiero.*
- **Comparables por empresa:** métricas de pares del mismo sector como dato, sin recomendación. *Origen: idea de empresas similares.*
- **Exportar informes a PDF.**
- **Informe de bienvenida gratis** al registrarse: un informe de una empresa al azar del portafolio, distinto del trimestral y del resumen semanal. Falta definir contenido y momento de envío. *Origen: decisión 5 de `specs/preguntas-abiertas.md`.*

## Versión 2

- **Ampliación del universo** (más acciones argentinas, más CEDEARs) una vez que las evals lo permitan.
- **Acciones y ETFs de EE.UU.** comprados directamente en brokers del exterior.
- **Importación del portafolio desde brokers** (archivos de movimientos o APIs oficiales), sin pedir credenciales.
- **Resumen en audio** del informe semanal.
- **Bonos y obligaciones negociables** en el portafolio.
- **Transcripciones de conferencias de resultados** como fuente adicional del informe trimestral.
- **Asociación con un asesor registrado en la CNV** si en algún momento se quiere ofrecer recomendaciones, con la parte de recomendación firmada por esa persona.
- **Langfuse autoalojado** u otra observabilidad propia si el volumen lo justifica.

## Ideas de negocio a evaluar

- Plan para asesores financieros que siguen los portafolios de varios clientes (B2B2C).
- Newsletter pública gratuita como canal de captación.
- Contenido en LinkedIn y X basado en los datos del propio sistema.
