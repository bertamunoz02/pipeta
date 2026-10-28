"""
Clasificador gratuito de Pipeta.

Usa un modelo de lenguaje pequeño y multilingüe (paraphrase-multilingual-MiniLM-L12-v2, vía fastembed)
que se ejecuta dentro de GitHub, sin coste. Convierte cada texto en un "vector de significado" y lo
compara con descripciones de cada tema y con una lista de conceptos científicos. Así:
  - el tema se elige por significado (en español o inglés), no por palabras sueltas;
  - cada pieza recibe etiquetas de conceptos (ES + EN) que el buscador de la web aprovecha.
Si el modelo no está disponible, el recolector sigue usando las palabras clave.
"""
import numpy as np

MODELO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Descripción de cada tema, en los dos idiomas (mismos id que la web)
TEMAS_DESC = {
    "gen": "Genética y genómica: ADN, genes, CRISPR, edición genética, herencia, mutaciones, secuenciación del genoma, epigenética. "
           "Genetics and genomics: DNA, genes, gene editing, heredity, mutations, genome sequencing, epigenetics.",
    "neu": "Neurociencia: cerebro, neuronas, memoria, Alzheimer, Parkinson, salud mental, sueño, comportamiento, cognición. "
           "Neuroscience: brain, neurons, memory, dementia, mental health, sleep, behaviour, cognition, psychology.",
    "eco": "Ecología y medio ambiente: biodiversidad, especies, animales salvajes, ecosistemas, cambio climático, conservación, contaminación. "
           "Ecology and environment: biodiversity, wildlife, ecosystems, climate change, conservation, pollution, insects, birds.",
    "mic": "Microbiología: bacterias, virus, microbiota, infecciones, antibióticos, resistencia antimicrobiana, hongos, pandemias. "
           "Microbiology: bacteria, viruses, microbiome, infections, antibiotics, antimicrobial resistance, fungi, epidemics.",
    "evo": "Evolución y origen: fósiles, dinosaurios, evolución humana, neandertales, arqueología, especies ancestrales, origen de la vida. "
           "Evolution and origins: fossils, dinosaurs, human evolution, Neanderthals, archaeology, ancient DNA, origin of life.",
    "bio": "Biomedicina y salud: cáncer, inmunología, vacunas, fármacos, ensayos clínicos, enfermedades, corazón, diabetes, envejecimiento. "
           "Biomedicine and health: cancer, immunology, vaccines, drugs, clinical trials, disease, heart, diabetes, ageing, medicine.",
    "bot": "Botánica y agricultura: plantas, árboles, bosques, fotosíntesis, cultivos, semillas, flores, agricultura. "
           "Plant biology and agriculture: plants, trees, forests, photosynthesis, crops, seeds, flowers, farming.",
    "mar": "Biología marina: océanos, mares, peces, ballenas, tiburones, corales, plancton, vida marina. "
           "Marine biology: oceans, seas, fish, whales, sharks, coral reefs, plankton, marine life.",
    # "Ciencia y sociedad": absorbe lo que no es de ningún tema concreto (se queda solo en "Todo")
    "todo": "Ciencia y sociedad: política científica, financiación de la investigación, carrera investigadora, divulgación, desinformación, "
            "tecnología, inteligencia artificial, eventos y jornadas de centros de investigación, educación, matemáticas. "
            "Science and society: science policy, research funding, scientific careers, misinformation, technology, artificial intelligence, education.",
    "fis": "Física, espacio y ciencias de la Tierra: astronomía, planetas, estrellas, agujeros negros, física cuántica, partículas, volcanes, terremotos. "
           "Physics, space and Earth science: astronomy, planets, galaxies, black holes, quantum physics, particles, volcanoes, earthquakes.",
}

# Conceptos para etiquetar (etiqueta en español, etiqueta en inglés, descripción para el modelo)
CONCEPTOS = [
    ("Alzheimer", "Alzheimer's", "enfermedad de Alzheimer, demencia, neurodegeneración, pérdida de memoria"),
    ("Parkinson", "Parkinson's", "enfermedad de Parkinson, trastorno del movimiento, dopamina"),
    ("salud mental", "mental health", "depresión, ansiedad, salud mental, psiquiatría, bienestar emocional"),
    ("sueño", "sleep", "sueño, dormir, insomnio, ritmos circadianos"),
    ("memoria", "memory", "memoria, aprendizaje, recuerdos, cognición"),
    ("autismo", "autism", "autismo, trastorno del espectro autista, neurodesarrollo"),
    ("cáncer", "cancer", "cáncer, tumores, oncología, metástasis, quimioterapia"),
    ("inmunoterapia", "immunotherapy", "inmunoterapia, terapia CAR-T, sistema inmunitario contra el cáncer"),
    ("vacunas", "vaccines", "vacunas, vacunación, inmunización, ARN mensajero"),
    ("sistema inmunitario", "immune system", "sistema inmunitario, anticuerpos, linfocitos, inflamación, autoinmunidad"),
    ("fármacos", "drugs", "fármacos, medicamentos, desarrollo de fármacos, industria farmacéutica, ensayos clínicos"),
    ("corazón", "heart", "corazón, enfermedades cardiovasculares, infarto, presión arterial"),
    ("diabetes y obesidad", "diabetes and obesity", "diabetes, obesidad, metabolismo, insulina, nutrición"),
    ("envejecimiento", "ageing", "envejecimiento, longevidad, senescencia celular"),
    ("células madre", "stem cells", "células madre, medicina regenerativa, organoides"),
    ("nutrición", "nutrition", "alimentación, dieta, nutrición, comida y salud"),
    ("ejercicio", "exercise", "ejercicio físico, deporte, actividad física y salud"),
    ("salud pública", "public health", "salud pública, epidemiología, políticas sanitarias"),
    ("CRISPR", "CRISPR", "CRISPR, edición genética, terapia génica"),
    ("ADN", "DNA", "ADN, genoma, secuenciación, genes"),
    ("ARN", "RNA", "ARN, transcripción, expresión génica, ARN mensajero"),
    ("epigenética", "epigenetics", "epigenética, metilación del ADN, regulación génica"),
    ("enfermedades raras", "rare diseases", "enfermedades raras, enfermedades genéticas hereditarias"),
    ("microbiota", "microbiome", "microbiota intestinal, microbioma, bacterias del intestino"),
    ("virus", "viruses", "virus, virología, infecciones víricas, gripe, COVID"),
    ("bacterias", "bacteria", "bacterias, infecciones bacterianas"),
    ("resistencia a antibióticos", "antibiotic resistance", "resistencia a antibióticos, superbacterias, antimicrobianos"),
    ("pandemias", "pandemics", "pandemias, epidemias, brotes, enfermedades emergentes"),
    ("hongos", "fungi", "hongos, setas, micología, levaduras"),
    ("parásitos", "parasites", "parásitos, malaria, enfermedades tropicales, mosquitos"),
    ("cambio climático", "climate change", "cambio climático, calentamiento global, emisiones, gases de efecto invernadero"),
    ("biodiversidad", "biodiversity", "biodiversidad, pérdida de especies, extinción"),
    ("conservación", "conservation", "conservación de especies, espacios protegidos, especies amenazadas"),
    ("contaminación", "pollution", "contaminación, plásticos, microplásticos, contaminación del aire y del agua"),
    ("incendios", "wildfires", "incendios forestales, fuego"),
    ("sequía y agua", "drought and water", "sequía, recursos hídricos, agua"),
    ("insectos", "insects", "insectos, polinizadores, abejas, mariposas"),
    ("aves", "birds", "aves, pájaros, migración de aves"),
    ("mamíferos", "mammals", "mamíferos, lobos, linces, osos, murciélagos"),
    ("reptiles y anfibios", "reptiles and amphibians", "reptiles, anfibios, ranas, serpientes"),
    ("comportamiento animal", "animal behaviour", "comportamiento animal, etología, inteligencia animal"),
    ("especies invasoras", "invasive species", "especies invasoras, especies exóticas"),
    ("océanos", "oceans", "océanos, mares, ecosistemas marinos"),
    ("corales", "coral reefs", "arrecifes de coral, blanqueamiento de corales"),
    ("ballenas y delfines", "whales and dolphins", "ballenas, delfines, cetáceos"),
    ("peces y pesca", "fish and fisheries", "peces, pesca, pesquerías"),
    ("plantas", "plants", "plantas, botánica, biología vegetal"),
    ("bosques", "forests", "bosques, árboles, deforestación"),
    ("agricultura", "agriculture", "agricultura, cultivos, seguridad alimentaria, plagas"),
    ("fósiles", "fossils", "fósiles, paleontología, dinosaurios"),
    ("evolución humana", "human evolution", "evolución humana, homínidos, neandertales, Homo sapiens"),
    ("arqueología", "archaeology", "arqueología, yacimientos, prehistoria, civilizaciones antiguas"),
    ("origen de la vida", "origin of life", "origen de la vida, química prebiótica, astrobiología"),
    ("evolución", "evolution", "evolución, selección natural, adaptación, especiación"),
    ("espacio", "space", "exploración espacial, misiones espaciales, astronautas, NASA, ESA"),
    ("astronomía", "astronomy", "astronomía, estrellas, galaxias, telescopios"),
    ("planetas", "planets", "planetas, exoplanetas, sistema solar, Marte, la Luna"),
    ("agujeros negros", "black holes", "agujeros negros, ondas gravitacionales, cosmología"),
    ("física cuántica", "quantum physics", "física cuántica, computación cuántica, partículas"),
    ("volcanes y terremotos", "volcanoes and earthquakes", "volcanes, erupciones, terremotos, geología"),
    ("energía", "energy", "energía, renovables, baterías, energía solar"),
    ("inteligencia artificial", "artificial intelligence", "inteligencia artificial, aprendizaje automático, algoritmos"),
    ("química", "chemistry", "química, moléculas, materiales, reacciones químicas"),
    ("matemáticas", "mathematics", "matemáticas, teoremas, demostraciones"),
    ("política científica", "science policy", "financiación de la ciencia, política científica, investigadores, carrera científica"),
    ("divulgación", "science communication", "divulgación científica, comunicación de la ciencia, desinformación"),
]

UMBRAL_TEMA = 0.25       # similitud mínima absoluta con el tema ganador
MARGEN_TEMA = 0.03       # ventaja mínima (ya corregida) sobre la media de ese tema
UMBRAL_CONCEPTO = 0.40   # similitud mínima absoluta para poner una etiqueta
MARGEN_CONCEPTO = 0.10   # ventaja mínima sobre lo "normal" para ese concepto
MAX_ETIQUETAS = 4


def _norm(m):
    m = np.asarray(m, dtype=np.float32)
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


class Clasificador:
    def __init__(self, cache_dir=None):
        from fastembed import TextEmbedding  # importación aquí: si falla, el recolector usa palabras clave
        self.modelo = TextEmbedding(MODELO, cache_dir=cache_dir)
        self.temas = list(TEMAS_DESC)
        self.v_temas = self._emb(list(TEMAS_DESC.values()))
        self.v_conc = self._emb([f"{es}. {en}. {d}" for es, en, d in CONCEPTOS])

    def _emb(self, textos):
        return _norm(list(self.modelo.embed(textos, batch_size=32)))

    def clasificar(self, items):
        """Devuelve, para cada item, (tema o None si dudoso, similitud, [etiquetas])."""
        if not items:
            return []
        textos = [((i.get("titulo") or "") + ". " + (i.get("resumen") or ""))[:700] for i in items]
        v = self._emb(textos)
        s_t = v @ self.v_temas.T
        s_c = v @ self.v_conc.T
        # Corrección de "temas comodín": algunos temas o conceptos se parecen un poco a todo.
        # Restamos a cada uno su similitud media con todas las piezas para quedarnos con lo distintivo.
        a_t = s_t - s_t.mean(axis=0, keepdims=True)
        a_c = s_c - s_c.mean(axis=0, keepdims=True)
        out = []
        for k in range(len(items)):
            j = int(np.argmax(a_t[k]))
            sim = float(s_t[k, j])
            tema = self.temas[j]
            if tema == "todo" or sim < UMBRAL_TEMA or a_t[k, j] < MARGEN_TEMA:
                tema = None
            orden = np.argsort(-a_c[k])[:MAX_ETIQUETAS]
            etiquetas = []
            for c in orden:
                if s_c[k, c] >= UMBRAL_CONCEPTO and a_c[k, c] >= MARGEN_CONCEPTO:
                    es, en, _ = CONCEPTOS[c]
                    etiquetas += [es] if es == en else [es, en]
            out.append((tema, round(sim, 3), etiquetas))
        return out
