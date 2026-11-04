"""
# (clave en el secreto PIPETA_IA_CLAVE del repositorio)
Clasificación con IA (gratuita) para Pipeta.

Usa el plan gratuito de un proveedor de IA (Google Gemini por defecto; también vale Groq o Cerebras)
a través de su API compatible con OpenAI. La clave se guarda como secreto de GitHub (PIPETA_IA_CLAVE)
y nunca aparece en el código.

Para cada pieza nueva, la IA decide:
  - el tema (o "todo" si es ciencia en general, o "fuera" si no es ciencia: gadgets, política, deportes…),
  - 3-6 etiquetas de conceptos en español y en inglés, para el buscador,
  - el formato Pipeta en español: titular gancho, resumen de dos frases y, si el texto trae una cifra, el dato clave.
    Solo con lo que dice el texto original; las cifras se comprueban contra el original y se descartan si no aparecen.
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
         ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"]),
}

IA_V = 2               # versión del formato: si cambia, se vuelve a procesar todo
LOTE = 10              # piezas por petición
MAX_PETICIONES = 60    # por ejecución (el resto se procesa en la siguiente)
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
    piezas = "\n\n".join(
        f'{n}. [{i.get("tipo","")} · {i.get("fuente","")}] {((i.get("titulo") or "") + " — " + (i.get("resumen") or ""))[:900]}'
        for n, i in enumerate(lote, 1))
    return f"""Eres una bióloga y divulgadora que edita Pipeta, una app tipo TikTok de ciencia en español.
Para cada pieza haz cinco cosas:

1. "t": tema, UNO de estos códigos:
{temas}
   Elige por el CONTENIDO científico, no por la fuente. "todo" solo si es ciencia sin tema concreto; "fuera" si no es ciencia.

2. "h": titular en español, máximo 90 caracteres. Que enganche y se entienda sin contexto, pero fiel: nada de exagerar
   ni prometer lo que el texto no dice. Sin comillas ni emojis. Si el original ya está en español y es bueno, puedes mantenerlo.

3. "s": resumen en español de 1 o 2 frases (máximo 240 caracteres): qué se ha descubierto o qué pasa y por qué importa.
   Usa SOLO información del texto. Si el texto apenas dice nada (por ejemplo, un vídeo con solo el título), escribe una frase
   corta y prudente o deja "".

4. "d": el dato clave, SOLO si el texto contiene una cifra concreta. Objeto {{"c": cifra corta, "x": qué mide}}.
   Ejemplos: {{"c":"37 M","x":"caballitos de mar vendidos al año"}}, {{"c":"1 de cada 4","x":"especies amenazadas"}},
   {{"c":"12.000 años","x":"antigüedad del yacimiento"}}. La cifra debe aparecer en el texto; nunca la calcules ni la inventes.
   Si no hay cifra clara, pon null. "x" máximo 50 caracteres.

5. "e": de 3 a 6 etiquetas para un buscador, mezclando español e inglés (ej.: "Alzheimer", "demencia", "dementia").

Piezas:
{piezas}

Responde SOLO con JSON, sin texto adicional:
{{"r":[{{"n":1,"t":"eco","h":"…","s":"…","d":{{"c":"…","x":"…"}},"e":["…"]}}]}}"""


def _cifra_valida(cifra, original):
    """La cifra del dato tiene que estar en el texto original (evita que la IA invente números)."""
    nums = re.findall(r"\d+(?:[.,]\d+)*", cifra or "")
    if not nums:
        m = re.search(r"\b(mitad|tercio|cuarto|doble|triple|half|third|quarter|double|triple)\b", (cifra or "").lower())
        return bool(m) and m.group(1) in original.lower()
    plano = re.sub(r"(?<=\d)[.,\s](?=\d{3}\b)", "", original)  # 37.000 / 37,000 / 37 000 → 37000
    for n in nums:
        n2 = re.sub(r"[.,](?=\d{3}\b)", "", n)
        if n not in original and n2 not in plano:
            return False
    return True


def _texto(x, maximo):
    x = re.sub(r"\s+", " ", str(x or "")).strip().strip('"«»“”')
    return x[:maximo].rsplit(" ", 1)[0] + "…" if len(x) > maximo else x


def _llamar(clave, url, modelo, prompt):
    r = requests.post(url, timeout=90, headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
                      json={"model": modelo, "temperature": 0, "messages": [{"role": "user", "content": prompt}],
                            "response_format": {"type": "json_object"}})
    if r.status_code in (429, 500, 502, 503, 504):
        raise RuntimeError("limite")  # saturado o límite por minuto: se reintenta tras una pausa
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
        if p and p.get("tema_ia") and p.get("ia_v") == IA_V:
            for k in ("tema_ia", "etiquetas_ia", "titular_es", "resumen_es", "dato", "ia_v"):
                if k in p:
                    i[k] = p[k]
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
    nuevas, peticiones, modelo_ok, errores, datos_rechazados = 0, 0, None, [], []
    for k in range(0, len(pendientes), LOTE):
        if peticiones >= MAX_PETICIONES:
            break
        lote = pendientes[k:k + LOTE]
        datos = None
        for modelo in ([modelo_ok] if modelo_ok else modelos):
            for intento in range(3):
                try:
                    datos = _llamar(clave, url, modelo, _prompt(lote))
                    modelo_ok = modelo
                    break
                except RuntimeError as e:
                    if str(e) == "limite" and intento < 2:
                        time.sleep(20 * (intento + 1))
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
                # solo etiquetas en alfabeto latino (español/inglés)
                i["etiquetas_ia"] = [str(x)[:40] for x in (r.get("e") or [])
                                     if re.fullmatch(r"[\w\s\-'’.,()+/&ÁÉÍÓÚÜÑáéíóúüñ]+", str(x), re.A | re.I)][:6]
                h, sm = _texto(r.get("h"), 110), _texto(r.get("s"), 280)
                if h:
                    i["titular_es"] = h
                if sm:
                    i["resumen_es"] = sm
                d = r.get("d")
                if isinstance(d, dict) and d.get("c") and d.get("x"):
                    original = (i.get("titulo") or "") + " " + (i.get("resumen") or "")
                    if _cifra_valida(str(d["c"]), original):
                        i["dato"] = {"c": _texto(d["c"], 16), "x": _texto(d["x"], 60)}
                    else:
                        datos_rechazados.append(str(d["c"]))
                i["ia_v"] = IA_V
                nuevas += 1
        time.sleep(PAUSA)
    informe.update(modelo=modelo_ok, nuevas=nuevas, peticiones=peticiones,
                   pendientes=len(pendientes) - nuevas, errores=errores[-3:],
                   cifras_descartadas=len(datos_rechazados), ej_descartadas=datos_rechazados[:5])
    return informe
