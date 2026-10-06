# Prompts versionados

Cada prompt es un archivo `<nombre>@<versión>.yaml` (por ejemplo `clasificar_noticia@1.yaml`):

```yaml
descripcion: Qué hace el prompt.
sistema: |
  Instrucciones de sistema.
usuario: |
  Texto con variables ${asi}.
parametros:        # opcional: se pasan tal cual al proveedor
  max_tokens: 2000
```

- Una versión publicada **no se edita**: cualquier cambio es una versión nueva.
- Cada resultado guarda la referencia `nombre@versión` que lo produjo (`llm_calls.version_prompt`).
- Las variables usan `${nombre}`; si falta una, el render falla antes de llamar al modelo.
- Los modelos Claude 5.x rechazan `temperature`/`top_p`: no los pongas en `parametros` para ellos.
- Nunca incluyas datos de usuarios: a los LLMs solo van documentos públicos y noticias.
