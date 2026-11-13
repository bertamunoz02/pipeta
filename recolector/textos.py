"""
«Leer más» de Pipeta: una explicación larga en español de cada noticia y cada artículo.

Para cada pieza nueva se consigue el texto completo (la página de la noticia o el resumen
completo del artículo científico) y la IA escribe a partir de él una explicación de varios
párrafos para todo el público. No se publica el texto original entero (es de sus autores):
solo se usa para escribir la explicación, que siempre enlaza a la fuente.

Las explicaciones se guardan aparte, en data/explica/<x>.json (16 ficheros), para que el feed
siga pesando poco: la web solo descarga el fichero de una pieza cuando alguien pulsa «Leer más».
"""
import hashlib
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

from ia import _config, _llamar

V = 1                  # versión del formato
LOTE = 3               # piezas por petición (los textos son largos)
MAX_PETICIONES = 30    # por ejecución; el resto se hace en las siguientes
PAUSA = 4.5
MIN_TEXTO = 450        # si no conseguimos más texto que esto, no merece la pena
MAX_TEXTO = 7000       # lo que se manda a la IA de cada pieza
TIPOS = ("noticia", "paper")

# textos completos que ya trae la fuente (p. ej., el resumen entero de un artículo de Europe PMC)
CRUDOS = {}


def fichero(id_):
    return hashlib.sha1(id_.encode()).hexdigest()[0]


# ---------- sacar el texto principal de una página ----------
class _Parrafos(HTMLParser):
    SALTAR = {"script", "style", "nav", "footer", "aside", "header", "form", "figcaption", "noscript", "button", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pila, self.parr, self.buf, self.en_p, self.en_art = [], [], [], 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "img", "meta", "link", "input", "hr"):
            return
        clase = " ".join(v or "" for k, v in attrs if k in ("class", "id")).lower()
        saltar = tag in self.SALTAR or re.search(r"(comment|related|share|social|newsletter|cookie|subscribe|promo|sidebar|menu|footer|breadcrumb|author-bio|tags)", clase)
        self.pila.append((tag, bool(saltar)))
        if tag == "article" or re.search(r"(article-body|articlebody|entry-content|post-content|story-body|news-body|field-body|cuerpo|texto-noticia)", clase):
            self.en_art += 1
        if tag in ("p", "li", "h2", "h3") and not self._saltando():
            self.en_p += 1
            self.buf = []

    def handle_endtag(self, tag):
        while self.pila:
            t, _ = self.pila.pop()
            if t in ("p", "li", "h2", "h3") and self.en_p:
                self.en_p -= 1
                txt = re.sub(r"\s+", " ", "".join(self.buf)).strip()
                if txt:
                    self.parr.append((txt, self.en_art > 0, t))
                self.buf = []
            if t == "article" and self.en_art:
                self.en_art -= 1
            if t == tag:
                break

    def handle_data(self, data):
        if self.en_p and not self._saltando():
            self.buf.append(data)

    def _saltando(self):
        return any(s for _, s in self.pila)


def _jsonld_cuerpo(htm):
    for bloque in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', htm, re.S | re.I):
        try:
            datos = json.loads(bloque.strip())
        except Exception:
            continue
        pila = [datos]
        while pila:
            d = pila.pop()
            if isinstance(d, list):
                pila += d
            elif isinstance(d, dict):
                cuerpo = d.get("articleBody")
                if isinstance(cuerpo, str) and len(cuerpo) > MIN_TEXTO:
                    return html.unescape(re.sub(r"<[^>]+>", " ", cuerpo))
                pila += [v for v in d.values() if isinstance(v, (dict, list))]
    return ""


def texto_de_pagina(htm):
    cuerpo = _jsonld_cuerpo(htm)
    if cuerpo:
        return re.sub(r"\s+", " ", cuerpo).strip()
    p = _Parrafos()
    try:
        p.feed(htm)
    except Exception:
        pass
    trozos = p.parr
    if sum(len(t) for t, art, _ in trozos if art) > MIN_TEXTO:
        trozos = [x for x in trozos if x[1]]
    buenos = [t for t, _, tag in trozos if (len(t) >= 70 and tag != "li") or (tag == "li" and len(t) >= 40)]
    return "\n".join(dict.fromkeys(buenos))


# ---------- explicación con IA ----------
def _prompt(lote):
    piezas = "\n\n".join(
        f'### Pieza {n} [{i["tipo"]} · {i.get("fuente","")}]\nTítulo: {i.get("titulo","")}\n{i["_texto"][:MAX_TEXTO]}'
        for n, i in enumerate(lote, 1))
    return f"""Eres una bióloga y divulgadora que escribe en Pipeta, una app de ciencia en español para todo el público.
Para cada pieza, escribe en español una explicación clara y amena para alguien curioso sin formación científica.

Reglas:
- De 3 a 5 párrafos cortos (en total, entre 150 y 300 palabras).
- Cuenta qué ha pasado o qué se ha descubierto, cómo se ha hecho, por qué importa y, si el texto lo dice,
  sus límites o próximos pasos. Explica los términos técnicos con palabras sencillas.
- Usa SOLO información del texto. No inventes cifras, nombres, fechas ni conclusiones; no exageres.
- Con tus propias palabras: no copies frases del original.
- Sin títulos, listas, emojis ni frases de relleno tipo «En resumen».
- Si el texto no tiene contenido suficiente o no es la noticia del título (por ejemplo, un aviso de cookies), devuelve "p": [].

{piezas}

Responde SOLO con JSON:
{{"r":[{{"n":1,"p":["párrafo 1","párrafo 2","párrafo 3"]}}]}}"""


def _leer(ruta):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        return {}


def ampliar(items, carpeta, session):
    """Escribe data/explica/*.json y marca en cada pieza con explicación el fichero donde está (i["tx"])."""
    carpeta.mkdir(parents=True, exist_ok=True)
    almacen = {}
    for k in "0123456789abcdef":
        almacen[k] = _leer(carpeta / f"{k}.json")
    previo = {id_: v for k in almacen for id_, v in almacen[k].items()}
    informe = {"activa": False}

    # piezas que aún no tienen explicación (las que fallaron se reintentan como mucho 2 veces)
    pendientes = [i for i in items if i.get("tipo") in TIPOS and (i.get("titular_es") or i.get("resumen_es"))
                  and not (previo.get(i["id"]) or {}).get("p") and (previo.get(i["id"]) or {}).get("f", 0) < 2]
    pendientes.sort(key=lambda i: i.get("fecha") or "", reverse=True)
    cfg = _config()
    if cfg:
        pendientes = pendientes[:LOTE * MAX_PETICIONES]

        def texto(i):
            t = CRUDOS.get(i["id"], "")
            if not t and i["tipo"] == "noticia":
                try:
                    r = session.get(i["url"], timeout=12)
                    if r.ok and "html" in r.headers.get("content-type", "html"):
                        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
                        t = texto_de_pagina(r.text[:1_500_000])
                except Exception:
                    t = ""
            i["_texto"] = t if len(t) >= MIN_TEXTO else ""

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=12) as ex:
            list(ex.map(texto, pendientes))
        sin_texto = [i for i in pendientes if not i["_texto"]]
        for i in sin_texto:
            previo[i["id"]] = {"f": (previo.get(i["id"]) or {}).get("f", 0) + 1, "v": V}
        con_texto = [i for i in pendientes if i["_texto"]]
        print(f"Textos completos: {len(con_texto)} conseguidos, {len(sin_texto)} sin texto ({time.time()-t0:.0f} s)")

        clave, url, modelos = cfg
        informe.update(activa=True)
        hechas, peticiones, errores, modelo_ok = 0, 0, [], None
        for k in range(0, len(con_texto), LOTE):
            lote = con_texto[k:k + LOTE]
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
                parr = [re.sub(r"\s+", " ", str(x)).strip() for x in (r.get("p") or []) if str(x).strip()]
                parr = [x for x in parr if len(x) > 30][:6]
                if len(" ".join(parr)) >= 300:
                    previo[i["id"]] = {"p": parr, "v": V}
                    hechas += 1
                else:
                    previo[i["id"]] = {"f": (previo.get(i["id"]) or {}).get("f", 0) + 1, "v": V}
            time.sleep(PAUSA)
        informe.update(modelo=modelo_ok, nuevas=hechas, peticiones=peticiones, sin_texto=len(sin_texto), errores=errores[-3:])
        for i in pendientes:
            i.pop("_texto", None)
    else:
        informe["motivo"] = "sin clave"

    # guardar solo lo de las piezas que siguen en el feed
    vivos = {i["id"] for i in items}
    nuevo = {k: {} for k in almacen}
    for id_, v in previo.items():
        if id_ in vivos:
            nuevo[fichero(id_)][id_] = v
    for k, d in nuevo.items():
        (carpeta / f"{k}.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    con = 0
    for i in items:
        if (previo.get(i["id"]) or {}).get("p"):
            i["tx"] = fichero(i["id"])
            con += 1
        else:
            i.pop("tx", None)
    informe["con_explicacion"] = con
    print(f"Explicaciones: {informe}")
    return informe
