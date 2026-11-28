"""Integración OPCIONAL con Cauce Network Engine RC3: diagnóstico de la sesión de navegador (solo estado).

- ``cliente_ne``: copia mínima y fijada del cliente ``session`` de Network Engine (sin dependencias).
- ``identidad``: identidad estable y sin datos personales del puesto de trabajo.
- ``diagnostico``: latidos, fases y avisos de operación; si el motor no está, Forms sigue igual y lo dice.

Network Engine solo OBSERVA: nunca abre, controla ni encamina el navegador, y Forms nunca espera al motor para que
la persona actúe más allá de un plazo corto y fijo (ver ``diagnostico.AVISO_MAX_S``).
"""
