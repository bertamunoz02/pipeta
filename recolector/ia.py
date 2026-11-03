"""
# (clave en el secreto PIPETA_IA_CLAVE del repositorio)
Clasificación con IA (gratuita) para Pipeta.

Usa el plan gratuito de un proveedor de IA (Google Gemini por defecto; también vale Groq o Cerebras)
a través de su API compatible con OpenAI. La clave se guarda como secreto de GitHub (PIPETA_IA_CLAVE)
y nunca aparece en el código.

Para cada pieza nueva, la IA decide:
  - el tema (o "todo" si es ciencia en general, o "fuera" si no es ciencia: gadgets, política, deportes…),
  - 3-6 etiquetas de conceptos en español y en inglés, para el buscador.
Cada pieza se clasifica una sola vez: el resultado se reutiliza en las siguientes ejecuciones.
Si no hay clave o el servicio falla, el recolector sigue con el clasificador local.
"""
import json
import os
import re
import time

import requests

TEMAS_IA = {
    "gen": "Genética y genómica: ADN, ARN, genes, CRISPR, edición genética, herencia, mutaciones, epigenética, secuenciación",
    "neu": "Neurociencia y mente: cerebro, neuronas, memoria, Alzheimer, Parkinson, salud mental, sueño, conducta, cognición, psicología",
    "eco": "Ecología y medio ambiente: biodiversidad, fauna terrestre, insectos, aves, conservación, cambio climático, contaminación, ecosistemas",
    "mic": "Microbiología: bacterias, virus, hongos, microbiota, infecciones, antibióticos y resistencias, epidemias",
    "evo": "Evolución y origen: fósiles, paleontología, dinosaurios, evolución humana, arqueología, ADN antiguo, origen de la vida",
    "bio": "Biomedicina y salud: cáncer, inmunología, vacunas, fármacos e industria farmacéutica, ensayos clínicos, enfermedades, medicina, nutrición",
    "bot": "Botánica y agricultura: plantas, árboles, bosques, fotosíntesis, cultivos, semillas, hongos de cultivo, agricultura",
    "mar": "Biología marina y océanos: peces, cetáceos, tiburones, corales, plancton, ecosistemas marinos, pesca",
    "fis": "Física, espacio y Tierra: astronomía, planetas, misiones espaciales, cosmología, física cuántica, partículas, química, geología, volcanes, matemáticas",
    "todo": "Ciencia en general: política científica, financiación, carrera investigadora, divulgación, premios, eventos de centros, historia de la ciencia, tecnología con base científica, IA aplicada a la ciencia",
    "fuera": "NO es ciencia: gadgets y productos de consumo, videojuegos, política o economía sin contenido científico, sucesos, deportes, famosos, publicidad, fe de erratas o correcciones de artículos",
}

PROVEEDORES = {
    # prefijo de la clave → (endpoint compatible con OpenAI, modelos por orden de preferencia)
    "gsk_": ("https://api.groq.com/openai/v1/chat/completions", ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]),
    "csk-": ("https://api.cerebras.ai/v1/chat/completions", ["llama-3.3-70b", "llama3.1-8b"]),
    "": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
         ["gemini-3.5-flash-lite", "gemini-3.8-flash-lite", "gemini-3.8-flash"]),
}

LOTE = 20              # piezas por petición
MAX_PETICIONES = 40    # por ejecución (el resto se clasifica en la siguiente hora)
PAUSA = 4.5            # segundos entre peticiones, para respetar los límites por minuto


def _config():
    clave = os.environ.get("PIPETA_IA_CLAVE", "").strip()
    if not clave:
        return None
    url, modelos = PROVEEDORES[""]
    for prefijo, valor in PROVEEDORES.items():
        if prefijo and clave.startswith(prefijo):
            url, modelos = valor
    url = os.environ.get("PIPETA_IA_URL") or url
    if os.environ.get("PIPETA_IA_MODELO"):
        modelos = [os.environ["PIPETA_IA_MODELO"]]
    return clave, url, modelos


def _prompt(lote):
    temas = "\n".join(f"- {k}: {v}" for k, v in TEMAS_IA.items())
    piezas = "\n".join(
        f'{n}. [{i.get("fuente","")}] {((i.get("titulo") or "") + " — " + (i.get("resumen") or ""))[:420]}'
        for n, i in enumerate(lote, 1))
    return f"""Eres una bióloga editora de Pipeta, una app de divulgación científica. Clasifica cada pieza en UN tema.

Temas posibles (usa solo el código):
{temas}

Criterios:
- Elige el tema del CONTENIDO científico principal, no el de la fuente. Ej.: "murciélagos que encogen" = eco; "cáncer de mama" = bio; "un fármaco activado por bacterias" = mic.
- Si mezcla dos temas, elige el más central para un lector.
- "todo" solo si es ciencia pero no encaja en ningún tema concreto.
- "fuera" si no es contenido científico.
- Etiquetas: de 3 a 6 conceptos clave, mezclando español e inglés (ej.: "Alzheimer", "demencia", "dementia"), útiles para un buscador.

Piezas:
{piezas}

Responde SOLO con JSON, sin texto adicional, con esta forma exacta:
{{"r":[{{"n":1,"t":"gen","e":["etiqueta","tag"]}}]}}"""


def _llamar(clave, url, modelo, prompt):
    r = requests.post(url, timeout=90, headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
                      json={"model": modelo, "temperature": 0, "messages": [{"role": "user", "content": prompt}],
                            "response_format": {"type": "json_object"}})
    if r.status_code == 429:
        raise RuntimeError("limite")
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    texto = r.json()["choices"][0]["message"]["content"] or ""
    return _leer_json(texto)


def _leer_json(texto):
    """Lee la respuesta aunque venga con ```json, texto alrededor o varios objetos seguidos."""
    dec, pos, objetos = json.JSONDecoder(), 0, []
    while True:
        k = min([x for x in (texto.find("{", pos), texto.find("[", pos)) if x >= 0], default=-1)
        if k < 0:
            break
        try:
            obj, fin = dec.raw_decode(texto, k)
            objetos.append(obj)
            pos = fin
        except ValueError:
            pos = k + 1
    filas = []
    for o in objetos:
        if isinstance(o, dict) and isinstance(o.get("r"), list):
            filas += o["r"]
        elif isinstance(o, dict) and "n" in o:
            filas.append(o)
        elif isinstance(o, list):
            filas += [x for x in o if isinstance(x, dict)]
    if not filas:
        raise ValueError("respuesta sin JSON válido: " + texto[:160].replace("\n", " "))
    return {"r": filas}


def clasificar_ia(items, anterior_por_id):
    """Pone i["tema_ia"] y i["etiquetas_ia"] en cada pieza (reutilizando lo ya clasificado). Devuelve un informe."""
    informe = {"activa": False}
    # 1) reutilizar lo clasificado en ejecuciones anteriores
    pendientes = []
    for i in items:
        p = anterior_por_id.get(i["id"])
        if p and p.get("tema_ia"):
            i["tema_ia"], i["etiquetas_ia"] = p["tema_ia"], p.get("etiquetas_ia", [])
        else:
            pendientes.append(i)
    informe["reutilizadas"] = len(items) - len(pendientes)
    cfg = _config()
    if not cfg:
        informe["motivo"] = "sin clave (secreto PIPETA_IA_CLAVE)"
        return informe
    clave, url, modelos = cfg
    informe.update(activa=True, endpoint=url.split("/")[2])
    # lo más reciente primero, que es lo que antes ve la gente
    pendientes.sort(key=lambda i: i.get("fecha") or "", reverse=True)
    nuevas, peticiones, modelo_ok, errores = 0, 0, None, []
    for k in range(0, len(pendientes), LOTE):
        if peticiones >= MAX_PETICIONES:
            break
        lote = pendientes[k:k + LOTE]
        datos = None
        for modelo in ([modelo_ok] if modelo_ok else modelos):
            for intento in range(2):
                try:
                    datos = _llamar(clave, url, modelo, _prompt(lote))
                    modelo_ok = modelo
                    break
                except RuntimeError as e:
                    if str(e) == "limite" and intento == 0:
                        time.sleep(25)
                        continue
                    errores.append(f"{modelo}: {e}")
                    break
                except Exception as e:
                    errores.append(f"{modelo}: {e}")
                    break
            if datos:
                break
        peticiones += 1
        if not datos:
            if len(errores) >= 4:
                break
            continue
        for r in datos.get("r", []):
            try:
                i = lote[int(r["n"]) - 1]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            t = str(r.get("t", "")).strip().lower()
            if t in TEMAS_IA:
                i["tema_ia"] = t
                i["etiquetas_ia"] = [str(x)[:40] for x in (r.get("e") or [])][:6]
                nuevas += 1
        time.sleep(PAUSA)
    informe.update(modelo=modelo_ok, nuevas=nuevas, peticiones=peticiones,
                   pendientes=len(pendientes) - nuevas, errores=errores[-3:])
    return informe
