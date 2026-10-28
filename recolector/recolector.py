"""
Recolector de Pipeta.

Lee fuentes.json, recoge lo último de cada fuente (noticias por RSS, vídeos de YouTube
y posts de ciencia de Bluesky) y lo guarda en data/feed.json, que la web muestra en el feed.
Lo ejecuta GitHub automáticamente cada hora (ver .github/workflows/recolector.yml).
"""
import html
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "data" / "feed.json"
AHORA = datetime.now(timezone.utc)
DIAS = {"noticias": 21, "video": 365, "bluesky": 3}
POR_FUENTE = {"noticias": 20, "video": 6, "bluesky": 40}
MAX_ITEMS = 700

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; PipetaBot/1.0; +https://bertamunoz02.github.io/pipeta/)",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
})
S.cookies.set("SOCS", "CAI", domain=".youtube.com")  # evita la pantalla de cookies de YouTube

# ---------- Temas: palabras clave en inglés y español (mismos id que la web) ----------
TEMAS = {
    "gen": ["genetic", "genom", "crispr", "dna", "rna", "gene ", "genes", "genétic", "genóm", "adn", "arn", "gen ", "epigen"],
    "neu": ["neuro", "brain", "cerebr", "neuron", "alzheimer", "parkinson", "cognit", "memory", "memoria", "dementia", "demencia"],
    "eco": ["ecolog", "biodivers", "ecosystem", "ecosistem", "climate", "climát", "climat", "species", "especie", "conservation", "conservación", "wildlife", "fauna", "doñana", "extinct"],
    "mic": ["microb", "bacteri", "virus", "viral", "antibiotic", "antibiót", "pathogen", "patógen", "infection", "infecci", "fung", "hongo"],
    "evo": ["evolution", "evoluti", "evoluci", "fossil", "fósil", "ancestor", "ancestr", "dinosaur", "dinosaurio", "neanderthal", "neandertal", "phylogen", "filogen"],
    "bio": ["cancer", "cáncer", "tumor", "immun", "inmun", "vaccine", "vacuna", "drug", "fármaco", "farmac", "clinical", "clínic", "therapy", "terapia", "patient", "paciente", "disease", "enfermedad", "cardio", "diabet", "obesi"],
    "bot": ["plant", "planta", "photosynth", "fotosíntesis", "botan", "crop", "cultivo", "tree", "árbol", "forest", "bosque", "flower", "flor", "seed", "semilla"],
    "mar": ["marine", "marin", "ocean", "océano", "oceán", "coral", "fish", "pez", "peces", "whale", "ballena", "sea ", "mar ", "shark", "tiburón", "plankton", "plancton"],
}


def tema(texto):
    t = " " + texto.lower() + " "
    puntos = {k: sum(t.count(w) for w in ws) for k, ws in TEMAS.items()}
    mejor = max(puntos, key=puntos.get)
    return mejor if puntos[mejor] > 0 else "todo"


def limpiar(texto, maximo=420):
    if not texto:
        return ""
    texto = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", texto, flags=re.S | re.I)
    texto = html.unescape(re.sub(r"<[^>]+>", " ", texto))
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) > maximo:
        texto = texto[:maximo].rsplit(" ", 1)[0] + "…"
    return texto


def fecha(entrada):
    for campo in ("published_parsed", "updated_parsed", "created_parsed"):
        v = entrada.get(campo)
        if v:
            return datetime(*v[:6], tzinfo=timezone.utc)
    return None


def imagen(entrada):
    for m in entrada.get("media_thumbnail", []) or []:
        if m.get("url"):
            return m["url"]
    for m in entrada.get("media_content", []) or []:
        if m.get("url") and ("image" in m.get("type", "image") or m.get("medium") == "image"):
            return m["url"]
    for e in entrada.get("enclosures", []) or []:
        if "image" in e.get("type", "") and e.get("href"):
            return e["href"]
    contenido = " ".join([c.get("value", "") for c in entrada.get("content", []) or []] + [entrada.get("summary", "")])
    m = re.search(r'<img[^>]+src="(https?://[^"]+)"', contenido)
    return m.group(1) if m else ""


def descargar(url, **kw):
    r = S.get(url, timeout=25, **kw)
    r.raise_for_status()
    return r


def leer_rss(url):
    r = descargar(url)
    feed = feedparser.parse(r.content)
    if not feed.entries:
        raise ValueError("sin entradas")
    return feed


def descubrir_rss(web):
    r = descargar(web)
    enlaces = re.findall(r"<link[^>]+>", r.text, flags=re.I)
    candidatos = []
    for l in enlaces:
        if re.search(r'type="application/(rss|atom)\+xml"', l, re.I):
            m = re.search(r'href="([^"]+)"', l)
            if m:
                candidatos.append(urljoin(web, html.unescape(m.group(1))))
    # algunas webs (WordPress) no lo anuncian pero tienen /feed/
    candidatos += [urljoin(web, "/feed/"), urljoin(web, "/rss.xml")]
    return candidatos


def item(fuente, tipo, titulo, resumen, url, fecha_pub, img="", extra=None):
    titulo = limpiar(titulo, 300)
    resumen = limpiar(resumen)
    d = {
        "id": f"{tipo}:{url}",
        "tipo": tipo,
        "fuente": fuente["nombre"],
        "idioma": fuente.get("idioma", "es"),
        "titulo": titulo,
        "resumen": resumen,
        "url": url,
        "fecha": fecha_pub.isoformat() if fecha_pub else "",
        "imagen": img,
        "tema": tema(titulo + " " + resumen),
    }
    if extra:
        d.update(extra)
    return d


PATRON_NOTICIA = r"/(noticias?|news|news-releases|actualidad|prensa|comunicacion|blog)/[^?#]{6,}"


def meta(htm, *nombres):
    for n in nombres:
        m = (re.search(r'<meta[^>]+(?:property|name|itemprop)="%s"[^>]+content="([^"]*)"' % re.escape(n), htm, re.I)
             or re.search(r'<meta[^>]+content="([^"]*)"[^>]+(?:property|name|itemprop)="%s"' % re.escape(n), htm, re.I))
        if m and m.group(1).strip():
            return html.unescape(m.group(1).strip())
    return ""


def fecha_web(htm):
    cand = (meta(htm, "article:published_time", "datePublished", "date", "DC.date.issued", "pubdate")
            or (re.search(r'"datePublished"\s*:\s*"([^"]+)"', htm) or [None, ""])[1]
            or (re.search(r'<time[^>]+datetime="([^"]+)"', htm, re.I) or [None, ""])[1])
    if cand:
        try:
            d = datetime.fromisoformat(cand.strip().replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b", htm)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def raspar_web(f):
    """Para webs sin RSS: busca enlaces a noticias en su página de noticias y lee los datos de cada una."""
    paginas = [u for u in [f.get("lista"), f.get("web")] if u]
    patron = re.compile(f.get("patron", PATRON_NOTICIA), re.I)
    enlaces, errores, base_ok = [], [], None
    for p in paginas:
        try:
            r = descargar(p)
        except Exception as e:
            errores.append(f"{p}: {e}")
            continue
        dominio = re.sub(r"^www\.", "", requests.utils.urlparse(r.url).netloc)
        for href, texto in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', r.text, flags=re.S | re.I):
            url = urljoin(r.url, html.unescape(href))
            host = re.sub(r"^www\.", "", requests.utils.urlparse(url).netloc)
            if host != dominio or not patron.search(requests.utils.urlparse(url).path):
                continue
            if url.rstrip("/") in (p.rstrip("/"), r.url.rstrip("/")) or url in enlaces:
                continue
            if len(limpiar(texto)) < 20 and "img" not in texto.lower():
                continue
            enlaces.append(url)
        if enlaces:
            base_ok = p
            break
    if not enlaces:
        raise RuntimeError("sin RSS y sin enlaces a noticias · " + " | ".join(errores[-2:]))
    limite = AHORA - timedelta(days=DIAS["noticias"])
    out = []
    for url in enlaces[:14]:
        try:
            htm = descargar(url).text
        except Exception:
            continue
        titulo = meta(htm, "og:title", "twitter:title") or (re.search(r"<title>(.*?)</title>", htm, re.S | re.I) or [None, ""])[1]
        titulo = limpiar(titulo, 300)
        # descarta páginas que no son noticias (título vacío, demasiado corto o igual al nombre de la web)
        if len(titulo) < 25 or titulo.lower() in (f["nombre"].lower(), f["nombre"].split()[0].lower()):
            continue
        fp = fecha_web(htm)
        if fp and fp < limite:
            continue
        out.append(item(f, "noticia", titulo, meta(htm, "og:description", "description", "twitter:description"),
                        url, fp, urljoin(url, meta(htm, "og:image", "twitter:image")) if meta(htm, "og:image", "twitter:image") else ""))
        if len(out) >= POR_FUENTE["noticias"]:
            break
    if not out:
        raise RuntimeError(f"{len(enlaces)} enlaces encontrados en {base_ok}, pero ninguna noticia reciente legible")
    return out, f"web {base_ok} ({len(enlaces)} enlaces)"


def recoger_noticias(f):
    if f.get("modo") == "web":
        return raspar_web(f)
    try:
        return recoger_rss(f)
    except RuntimeError as e:
        try:
            return raspar_web(f)
        except Exception as e2:
            raise RuntimeError(f"{e} · plan B: {e2}")


def recoger_rss(f):
    intentos = list(f.get("rss", []))
    feed, usado, errores = None, None, []
    for url in intentos:
        try:
            feed, usado = leer_rss(url), url
            break
        except Exception as e:
            errores.append(f"{url}: {e}")
    if feed is None and f.get("web"):
        try:
            for url in descubrir_rss(f["web"]):
                if url in intentos:
                    continue
                try:
                    feed, usado = leer_rss(url), url
                    break
                except Exception as e:
                    errores.append(f"{url}: {e}")
        except Exception as e:
            errores.append(f"{f['web']}: {e}")
    if feed is None:
        raise RuntimeError("no se encontró RSS · " + " | ".join(errores[-3:]))
    limite = AHORA - timedelta(days=DIAS["noticias"])
    out = []
    for e in feed.entries:
        fp = fecha(e)
        if fp and fp < limite:
            continue
        if not e.get("link") or not e.get("title"):
            continue
        out.append(item(f, "noticia", e.title, e.get("summary", ""), e.link, fp, imagen(e)))
        if len(out) >= POR_FUENTE["noticias"]:
            break
    return out, usado


def recoger_youtube(f):
    canal = f.get("canal_id")
    if not canal:
        r = descargar("https://www.youtube.com/" + f["youtube"].lstrip("/"))
        m = (re.search(r'"externalId":"(UC[\w-]{22})"', r.text)
             or re.search(r'<meta itemprop="identifier" content="(UC[\w-]{22})"', r.text)
             or re.search(r'"channelId":"(UC[\w-]{22})"', r.text))
        if not m:
            raise RuntimeError("no se encontró el canal")
        canal = m.group(1)
    rss = f"https://www.youtube.com/feeds/videos.xml?channel_id={canal}"
    feed = leer_rss(rss)
    limite = AHORA - timedelta(days=DIAS["video"])
    out = []
    for e in feed.entries:
        fp = fecha(e)
        if fp and fp < limite:
            continue
        if "/shorts/" in e.get("link", ""):
            continue
        vid = e.get("yt_videoid") or e.link.rsplit("=", 1)[-1]
        # la descripción de YouTube suele ser publicidad y enlaces: mostramos solo título y canal
        out.append(item(f, "video", e.title, "", e.link, fp, f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", {"video_id": vid}))
        if len(out) >= POR_FUENTE["video"]:
            break
    return out, f"canal {canal}"


BSKY = "https://public.api.bsky.app/xrpc/"


def recoger_bluesky(f):
    usados, out = [], []
    limite = AHORA - timedelta(days=DIAS["bluesky"])
    for consulta in f.get("buscar_feeds", ["science"]):
        r = descargar(BSKY + "app.bsky.unspecced.getPopularFeedGenerators", params={"query": consulta, "limit": 15}).json()
        feeds = [g for g in r.get("feeds", []) if consulta.lower() in (g.get("displayName", "") + " " + g.get("description", "")).lower()]
        feeds.sort(key=lambda g: g.get("likeCount", 0), reverse=True)
        if not feeds:
            continue
        g = feeds[0]
        usados.append(f"{g.get('displayName')} ({g.get('likeCount', 0)} likes)")
        datos = descargar(BSKY + "app.bsky.feed.getFeed", params={"feed": g["uri"], "limit": 100}).json()
        for p in datos.get("feed", []):
            post = p.get("post", {})
            rec = post.get("record", {})
            texto = rec.get("text", "").strip()
            if len(texto) < 60 or rec.get("reply"):
                continue
            try:
                fp = datetime.fromisoformat(rec.get("createdAt", "").replace("Z", "+00:00"))
            except ValueError:
                fp = None
            if fp and fp < limite:
                continue
            autor = post.get("author", {})
            rkey = post.get("uri", "").rsplit("/", 1)[-1]
            url = f"https://bsky.app/profile/{autor.get('handle')}/post/{rkey}"
            emb = post.get("embed", {}) or {}
            img = ""
            enlace = None
            if emb.get("images"):
                img = emb["images"][0].get("thumb", "")
            ext = emb.get("external") or (emb.get("media", {}) or {}).get("external")
            if ext:
                img = img or ext.get("thumb", "")
                enlace = {"url": ext.get("uri", ""), "titulo": limpiar(ext.get("title", ""), 160)}
            langs = rec.get("langs") or []
            fx = dict(f, idioma=("es" if "es" in langs else "en"), nombre=f["nombre"])
            out.append(item(fx, "post", "", texto, url, fp, img, {
                "titulo": "",
                "resumen": limpiar(texto, 600),
                "tema": tema(texto + " " + (enlace or {}).get("titulo", "")),
                "autor": autor.get("displayName") or autor.get("handle", ""),
                "usuario": autor.get("handle", ""),
                "avatar": autor.get("avatar", ""),
                "likes": post.get("likeCount", 0),
                "enlace": enlace,
            }))
            if len(out) >= f.get("maximo", POR_FUENTE["bluesky"]):
                break
    if not usados:
        raise RuntimeError("no se encontró ningún feed de Bluesky")
    return out, "feed: " + ", ".join(usados)


def main():
    cfg = json.loads((RAIZ / "fuentes.json").read_text(encoding="utf-8"))
    anterior = {}
    if SALIDA.exists():
        try:
            anterior = json.loads(SALIDA.read_text(encoding="utf-8"))
        except Exception:
            anterior = {}
    items, estado = [], []
    for f in cfg["fuentes"]:
        tipo = f.get("tipo")
        t0 = time.time()
        try:
            if tipo == "noticias":
                nuevos, detalle = recoger_noticias(f)
            elif tipo == "video":
                nuevos, detalle = recoger_youtube(f)
            elif tipo == "bluesky":
                nuevos, detalle = recoger_bluesky(f)
            else:
                raise RuntimeError(f"tipo desconocido: {tipo}")
            items += nuevos
            estado.append({"nombre": f["nombre"], "tipo": tipo, "ok": True, "items": len(nuevos), "detalle": detalle})
            print(f"OK    {f['nombre']}: {len(nuevos)} ({detalle}) {time.time()-t0:.1f}s")
        except Exception as e:
            # si una fuente falla, conservamos lo que teníamos de ella
            viejos = [i for i in anterior.get("items", []) if i.get("fuente") == f["nombre"]]
            items += viejos
            estado.append({"nombre": f["nombre"], "tipo": tipo, "ok": False, "items": len(viejos), "detalle": str(e)[:300]})
            print(f"FALLO {f['nombre']}: {e}")
    vistos, unicos = set(), []
    for i in items:
        if i["id"] not in vistos:
            vistos.add(i["id"])
            unicos.append(i)
    unicos.sort(key=lambda i: i.get("fecha") or "", reverse=True)
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps({
        "generado": AHORA.isoformat(),
        "fuentes": estado,
        "items": unicos[:MAX_ITEMS],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for e in estado if e["ok"])
    print(f"\n{ok}/{len(estado)} fuentes OK · {len(unicos[:MAX_ITEMS])} elementos guardados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
