# Demostración del conector · 2026-11-20 04:12

Datos 100 % ficticios (`sample_data/personas_ficticias.json`). Formularios propios servidos en 127.0.0.1. La columna **Eventos recibidos** la mide la propia página con un script de instrumentación: cuenta keydown/keyup por carácter y asignaciones directas de `.value`.

## 1. Formulario HTML tradicional

Archivo: `lab_forms/01_tradicional.html` · fila 1 · resultado global: **OK** · enviado: **no** · 2124 ms

| Campo | Método | Esperado | Registrado | Estado | Caracteres enviados | Eventos recibidos por la página |
|---|---|---|---|---|---|---|
| Nombre | teclado_secuencial | José Ángel | José Ángel | ok | 10 | keydown 10, keyup 10, input 10, pegar 0, `.value=` 0 |
| Apellidos | teclado_secuencial | Muñoz Ibáñez | Muñoz Ibáñez | ok | 12 | keydown 12, keyup 12, input 12, pegar 0, `.value=` 0 |
| Correo electrónico | teclado_secuencial | jose.munoz@example.com | jose.munoz@example.com | ok | 22 | keydown 22, keyup 22, input 22, pegar 0, `.value=` 0 |
| Teléfono | teclado_secuencial | 5512345678 | 5512345678 | ok | 10 | keydown 10, keyup 10, input 10, pegar 0, `.value=` 0 |
| Fecha de nacimiento | fill_nativo | 1988-03-14 | 1988-03-14 | ok |  | — (control sin texto) |
| País | select_option | MX | MX | ok |  | — (control sin texto) |
| Género | check | M | M | ok |  | — (control sin texto) |
| Mensaje | teclado_secuencial | Solicitud de prueba con datos ficticios.⏎Segunda línea del mensaje. | Solicitud de prueba con datos ficticios.⏎Segunda línea del mensaje. | ok | 67 | keydown 67, keyup 67, input 67, pegar 0, `.value=` 0 |
| Acepto los términos de uso | check | true | true | ok |  | — (control sin texto) |

Botón de envío detectado y **no pulsado**: Enviar registro

Captura: `evidence/out/01_01_tradicional_fila1.png`

## 2. Formulario dinámico (persona física)

Archivo: `lab_forms/02_dinamico.html` · fila 1 · resultado global: **OK** · enviado: **no** · 1392 ms

| Campo | Método | Esperado | Registrado | Estado | Caracteres enviados | Eventos recibidos por la página |
|---|---|---|---|---|---|---|
| Tipo de solicitante | select_option | persona | persona | ok |  | — (control sin texto) |
| Correo de contacto | teclado_secuencial | jose.munoz@example.com | jose.munoz@example.com | ok | 22 | keydown 22, keyup 22, input 22, pegar 0, `.value=` 2 |
| Nombre *(apareció al rellenar)* | teclado_secuencial | José Ángel | José Ángel | ok | 10 | keydown 10, keyup 10, input 10, pegar 0, `.value=` 2 |
| Apellidos *(apareció al rellenar)* | teclado_secuencial | Muñoz Ibáñez | Muñoz Ibáñez | ok | 12 | keydown 12, keyup 12, input 12, pegar 0, `.value=` 2 |
| CURP (documento de identidad) *(apareció al rellenar)* | teclado_secuencial | MUIJ880314HDFXXX09 | MUIJ880314HDFXXX09 | ok | 18 | keydown 18, keyup 18, input 18, pegar 0, `.value=` 2 |
| Comentarios | teclado_secuencial | Prueba de laboratorio: ¿se registran acentos y la eñe? | Prueba de laboratorio: ¿se registran acentos y la eñe? | ok | 54 | keydown 54, keyup 54, input 54, pegar 0, `.value=` 2 |

Botón de envío detectado y **no pulsado**: Enviar solicitud

Captura: `evidence/out/02_02_dinamico_fila1.png`

## 3. Formulario dinámico (empresa)

Archivo: `lab_forms/02_dinamico.html` · fila 2 · resultado global: **OK** · enviado: **no** · 1345 ms

| Campo | Método | Esperado | Registrado | Estado | Caracteres enviados | Eventos recibidos por la página |
|---|---|---|---|---|---|---|
| Tipo de solicitante | select_option | empresa | empresa | ok |  | — (control sin texto) |
| Correo de contacto | teclado_secuencial | lucia.fernandez@example.org | lucia.fernandez@example.org | ok | 27 | keydown 27, keyup 27, input 27, pegar 0, `.value=` 2 |
| Razón social *(apareció al rellenar)* | teclado_secuencial | Tostadores Ficticios S.A. de C.V. | Tostadores Ficticios S.A. de C.V. | ok | 33 | keydown 33, keyup 33, input 33, pegar 0, `.value=` 2 |
| Cargo del contacto *(apareció al rellenar)* | teclado_secuencial | Directora de compras | Directora de compras | ok | 20 | keydown 20, keyup 20, input 20, pegar 0, `.value=` 2 |
| Comentarios | teclado_secuencial | Empresa inventada para pruebas. | Empresa inventada para pruebas. | ok | 31 | keydown 31, keyup 31, input 31, pegar 0, `.value=` 2 |

Botón de envío detectado y **no pulsado**: Enviar solicitud

Captura: `evidence/out/03_02_dinamico_fila2.png`

## 4. Validaciones y confirmaciones de correo y teléfono

Archivo: `lab_forms/03_validaciones.html` · fila 1 · resultado global: **OK** · enviado: **no** · 1303 ms

| Campo | Método | Esperado | Registrado | Estado | Caracteres enviados | Eventos recibidos por la página |
|---|---|---|---|---|---|---|
| Nombre completo | teclado_secuencial | José Ángel Muñoz Ibáñez | José Ángel Muñoz Ibáñez | ok | 23 | keydown 23, keyup 23, input 23, pegar 0, `.value=` 0 |
| Correo electrónico | teclado_secuencial | jose.munoz@example.com | jose.munoz@example.com | ok | 22 | keydown 22, keyup 22, input 22, pegar 0, `.value=` 0 |
| Confirme su correo electrónico | teclado_secuencial | jose.munoz@example.com | jose.munoz@example.com | ok | 22 | keydown 22, keyup 22, input 22, pegar 0, `.value=` 0 |
| Teléfono móvil (10 dígitos) | teclado_secuencial | 5512345678 | 55 1234 5678 | ok_normalizado | 10 | keydown 10, keyup 10, input 10, pegar 0, `.value=` 10 |
| Repita el teléfono | teclado_secuencial | 5512345678 | 55 1234 5678 | ok_normalizado | 10 | keydown 10, keyup 10, input 10, pegar 0, `.value=` 10 |
| Código postal | teclado_secuencial | 06700 | 06700 | ok | 5 | keydown 5, keyup 5, input 5, pegar 0, `.value=` 0 |
| Ciudad | teclado_secuencial | Ciudad de México | Ciudad de México | ok | 16 | keydown 16, keyup 16, input 16, pegar 0, `.value=` 0 |
| He leído el aviso de privacidad | check | true | true | ok |  | — (control sin texto) |

Confirmaciones: Correo electrónico → **coinciden**; Teléfono móvil (10 dígitos) → **coinciden**

Botón de envío detectado y **no pulsado**: Dar de alta

Captura: `evidence/out/04_03_validaciones_fila1.png`

## 5. Página con verificación humana (debe bloquearse)

Archivo: `lab_forms/04_captcha_simulado.html` · fila 1 · resultado global: **BLOQUEADO** · enviado: **no** · 0 ms

Motivo: Se detectó un CAPTCHA o verificación anti-bot: requiere intervención humana.

No se escribió nada en la página.

## Nota sobre `.value=`

En los laboratorios 2 y 3 la propia página asigna `.value` (re-render del componente controlado y máscara de teléfono). Esos contadores son de la página, no del conector: en el laboratorio 1, que no tiene scripts que toquen los campos, el contador es 0 en todos los campos de texto. Ver `tests/test_sequential_typing.py`.
