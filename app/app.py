from __future__ import annotations

import os
import re
import tempfile
import threading
import traceback
from pathlib import Path

import owlrl
from flask import Flask, jsonify, render_template, request
from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL

BASE_DIR = Path(os.path.abspath(__file__)).parent


def _find_first(*candidates: Path) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _listing(directory: Path) -> str:
    try:
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in directory.iterdir())
        return ", ".join(names) if names else "(empty)"
    except OSError as exc:
        return f"(could not list: {exc})"


# Support two layouts without asking the person to move anything:
#   (a) the shipped layout — project/app/app.py  with project/ontology/ as a sibling of app/
#   (b) a flattened copy   — project/app.py       with project/ontology/ as a sibling of app.py
_ONTOLOGY_TTL = _find_first(
    BASE_DIR.parent / "ontology" / "srilanka-tourism.ttl",  # (a)
    BASE_DIR / "ontology" / "srilanka-tourism.ttl",         # (b)
    BASE_DIR / "srilanka-tourism.ttl",                      # ontology file dropped next to app.py
)
if _ONTOLOGY_TTL is None:
    raise FileNotFoundError(
        "Could not find srilanka-tourism.ttl.\n"
        f"This script is running from : {BASE_DIR}\n"
        f"Its contents               : {_listing(BASE_DIR)}\n"
        f"Its parent's contents      : {_listing(BASE_DIR.parent)}\n"
        "Put srilanka-tourism.ttl in an ontology/ folder next to app.py (or next to app.py's parent)."
    )
ONTOLOGY_TTL = _ONTOLOGY_TTL

_TEMPLATES_DIR = _find_first(BASE_DIR / "templates", BASE_DIR / "app" / "templates")
_STATIC_DIR = _find_first(BASE_DIR / "static", BASE_DIR / "app" / "static")

if _TEMPLATES_DIR is None or not (_TEMPLATES_DIR / "index.html").exists():
    found_dir = _TEMPLATES_DIR is not None
    raise FileNotFoundError(
        "Could not find templates/index.html.\n"
        f"This script is running from : {BASE_DIR}\n"
        f"Its contents                : {_listing(BASE_DIR)}\n"
        + (
            f"templates/ was found at     : {_TEMPLATES_DIR}\n"
            f"but templates/ contains     : {_listing(_TEMPLATES_DIR)}\n"
            if found_dir else
            f"No templates/ folder exists at {BASE_DIR / 'templates'} either.\n"
        )
        + "Fix: make sure templates/index.html sits right beside app.py (same folder)."
    )

SLT = Namespace("http://www.semanticweb.org/lk/ontologies/2026/srilanka-tourism#")

app = Flask(
    __name__,
    template_folder=str(_TEMPLATES_DIR),
    static_folder=str(_STATIC_DIR) if _STATIC_DIR else str(BASE_DIR / "static"),
)

# The reasoner is not re-entrant; serialise access to it.
_lock = threading.Lock()


# --------------------------------------------------------------------------- #
#  Reasoning pipeline
# --------------------------------------------------------------------------- #

class Store:
    """Holds the asserted graph, the inferred graph, and the delta between them."""

    def __init__(self) -> None:
        self.asserted = Graph()
        self.inferred = Graph()
        self.engine = "none"
        self.consistent = True
        self.inconsistency_reason = ""
        self.user_triples = Graph()   # individuals added through the UI

    # -- loading ------------------------------------------------------------ #

    def load(self) -> None:
        self.asserted = Graph()
        self.asserted.parse(str(ONTOLOGY_TTL), format="turtle")
        self.rebuild()

    def working_graph(self) -> Graph:
        g = Graph()
        for triple in self.asserted:
            g.add(triple)
        for triple in self.user_triples:
            g.add(triple)
        return g

    # -- inference ---------------------------------------------------------- #

    def rebuild(self) -> None:
        source = self.working_graph()
        self.consistent = True
        self.inconsistency_reason = ""

        hermit_graph, engine = self._run_hermit(source)
        target = hermit_graph if hermit_graph is not None else source

        closed = Graph()
        for triple in target:
            closed.add(triple)
        try:
            owlrl.DeductiveClosure(
                owlrl.OWLRL_Semantics,
                axiomatic_triples=False,
                datatype_axioms=False,
            ).expand(closed)
            engine = f"{engine} + OWL RL closure" if engine != "none" else "OWL RL closure only"
        except Exception:  # pragma: no cover - defensive
            traceback.print_exc()

        self.inferred = closed
        self.engine = engine

    def _run_hermit(self, source: Graph):
        """Return (classified graph, engine name) or (None, 'none') if unavailable."""
        try:
            from owlready2 import World, OwlReadyInconsistentOntologyError, sync_reasoner_hermit
        except ImportError:
            return None, "none"

        tmpdir = tempfile.mkdtemp(prefix="slt_")
        in_path = os.path.join(tmpdir, "input.owl")
        out_path = os.path.join(tmpdir, "classified.owl")
        source.serialize(destination=in_path, format="xml")

        try:
            world = World()
            onto = world.get_ontology("file://" + in_path).load()
            with onto:
                sync_reasoner_hermit(
                    world,
                    infer_property_values=True,
                    debug=0,
                )
            onto.save(file=out_path, format="rdfxml")
        except Exception as exc:
            name = type(exc).__name__
            if "Inconsistent" in name:
                self.consistent = False
                self.inconsistency_reason = str(exc)[:400]
                return None, "HermiT (reported an inconsistency)"
            print("HermiT unavailable:", name, str(exc)[:200])
            return None, "none"

        classified = Graph()
        classified.parse(out_path, format="xml")
        return classified, "HermiT (OWL 2 DL)"

    # -- helpers ------------------------------------------------------------ #

    def graph_for(self, mode: str) -> Graph:
        return self.inferred if mode == "inferred" else self.working_graph()

    def is_asserted(self, triple) -> bool:
        return triple in self.asserted or triple in self.user_triples


STORE = Store()


# --------------------------------------------------------------------------- #
#  Predefined competency queries
# --------------------------------------------------------------------------- #

PREFIXES = """PREFIX slt:  <http://www.semanticweb.org/lk/ontologies/2026/srilanka-tourism#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
"""

QUERIES = [
    {
        "id": "CQ1",
        "question": "Which attractions exist, and in which city is each one?",
        "feature": "Basic SELECT",
        "needs": "asserted",
        "sparql": PREFIXES + """
SELECT DISTINCT ?attraction ?city
WHERE {
  ?a slt:locatedInCity ?c .
  ?a rdfs:label ?attraction .
  ?c rdfs:label ?city .
}
ORDER BY ?city""",
    },
    {
        "id": "CQ2",
        "question": "Which religious sites host a festival, and which festival?",
        "feature": "Basic SELECT",
        "needs": "asserted",
        "sparql": PREFIXES + """
SELECT DISTINCT ?site ?festival
WHERE {
  ?s slt:hostsFestival ?f .
  ?s rdfs:label ?site .
  ?f rdfs:label ?festival .
}""",
    },
    {
        "id": "CQ3",
        "question": "Which attractions can a visitor enter for under LKR 2500?",
        "feature": "FILTER",
        "needs": "asserted",
        "sparql": PREFIXES + """
SELECT DISTINCT ?attraction ?fee
WHERE {
  ?a slt:entranceFeeLKR ?fee .
  ?a rdfs:label ?attraction .
  FILTER (?fee < 2500)
}
ORDER BY ?fee""",
    },
    {
        "id": "CQ4",
        "question": "Which attractions have nearby accommodation, and which do not?",
        "feature": "OPTIONAL",
        "needs": "inferred",
        "sparql": PREFIXES + """
SELECT DISTINCT ?attraction ?hotel
WHERE {
  ?a slt:entranceFeeLKR ?fee .          # every attraction carries a fee
  ?a rdfs:label ?attraction .
  OPTIONAL {                             # hasNearbyAccommodation is never asserted:
    ?a slt:hasNearbyAccommodation ?h .   # it is the inverse of servesAttraction
    ?h rdfs:label ?hotel .
  }
}
ORDER BY ?attraction""",
    },
    {
        "id": "CQ5",
        "question": "What is the average and maximum entrance fee across all attractions?",
        "feature": "Aggregation",
        "needs": "asserted",
        "sparql": PREFIXES + """
SELECT (COUNT(?a) AS ?sites)
       (ROUND(AVG(?fee)) AS ?averageFee)
       (MIN(?fee) AS ?cheapest)
       (MAX(?fee) AS ?dearest)
WHERE {
  ?a slt:entranceFeeLKR ?fee .
}""",
    },
    {
        "id": "CQ6",
        "question": "How many attractions does each province hold, richest first?",
        "feature": "GROUP BY + ORDER BY",
        "needs": "inferred",
        "sparql": PREFIXES + """
SELECT ?province (COUNT(?a) AS ?attractions)
WHERE {
  ?a slt:locatedInProvince ?p .
  ?p rdfs:label ?province .
}
GROUP BY ?province
ORDER BY DESC(?attractions) ?province""",
    },
    {
        "id": "CQ7",
        "question": "Which sites are UNESCO World Heritage sites?",
        "feature": "Reasoning",
        "needs": "inferred",
        "sparql": PREFIXES + """
SELECT DISTINCT ?site ?inscribed
WHERE {
  ?s rdf:type slt:UNESCOWorldHeritageSite .
  ?s rdfs:label ?site .
  OPTIONAL { ?s slt:inscribedYear ?inscribed }
}
ORDER BY ?inscribed""",
    },
    {
        "id": "CQ8",
        "question": "Which attractions are in the Central Province?",
        "feature": "Reasoning",
        "needs": "inferred",
        "sparql": PREFIXES + """
SELECT DISTINCT ?attraction ?city
WHERE {
  ?a rdf:type slt:CentralProvinceAttraction .
  ?a rdfs:label ?attraction .
  ?a slt:locatedInCity ?c .
  ?c rdfs:label ?city .
}""",
    },
    {
        "id": "CQ9",
        "question": "Which attractions offer three or more different activities?",
        "feature": "Reasoning",
        "needs": "inferred",
        "sparql": PREFIXES + """
SELECT ?attraction (COUNT(DISTINCT ?act) AS ?activities)
WHERE {
  ?a rdf:type slt:MultiActivityDestination .
  ?a rdfs:label ?attraction .
  ?a slt:offersActivity ?act .
}
GROUP BY ?attraction
ORDER BY DESC(?activities)""",
    },
    {
        "id": "CQ10",
        "question": "Which coastal attractions can be paired with a wildlife experience?",
        "feature": "Reasoning",
        "needs": "inferred",
        "sparql": PREFIXES + """
SELECT DISTINCT ?attraction ?city
WHERE {
  ?a rdf:type slt:CoastalAttraction .
  ?a rdf:type slt:WildlifeDestination .
  ?a rdfs:label ?attraction .
  ?a slt:locatedInCity ?c .
  ?c rdfs:label ?city .
}""",
    },
]


# --------------------------------------------------------------------------- #
#  Formatting helpers
# --------------------------------------------------------------------------- #

def normalise(value) -> str:
    """Canonicalise a binding for set comparison.

    HermiT round-trips decimals through Owlready2, so an asserted "0" comes back
    as "0.0". Without this, every numeric row would look like new knowledge.
    """
    if value is None:
        return ""
    text = str(value)
    try:
        return format(float(text), ".6f")
    except ValueError:
        return text


def tidy(value) -> str:
    """Render a binding for display. Owlready2 hands decimals back as "0.0";
    show whole numbers as whole numbers, keep genuine fractions intact."""
    text = str(value)
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def shorten(term) -> str:
    text = str(term)
    if text.startswith(str(SLT)):
        return "slt:" + text[len(str(SLT)):]
    if text.startswith(str(RDFS)):
        return "rdfs:" + text[len(str(RDFS)):]
    if text.startswith(str(RDF)):
        return "rdf:" + text[len(str(RDF)):]
    if text.startswith(str(OWL)):
        return "owl:" + text[len(str(OWL)):]
    if text.startswith(str(XSD)):
        return "xsd:" + text[len(str(XSD)):]
    return text


def local_name(term) -> str:
    return re.split(r"[#/]", str(term))[-1]


def label_of(graph: Graph, subject) -> str:
    label = graph.value(subject, RDFS.label)
    return str(label) if label else local_name(subject)


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    asserted = STORE.working_graph()
    classes = {s for s in asserted.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}
    obj_props = set(asserted.subjects(RDF.type, OWL.ObjectProperty))
    data_props = set(asserted.subjects(RDF.type, OWL.DatatypeProperty))
    named = {
        s for s in asserted.subjects(RDF.type, None)
        if isinstance(s, URIRef) and str(s).startswith(str(SLT))
        and (s, RDF.type, OWL.Class) not in asserted
        and (s, RDF.type, OWL.ObjectProperty) not in asserted
        and (s, RDF.type, OWL.DatatypeProperty) not in asserted
        and (s, RDF.type, OWL.AnnotationProperty) not in asserted
    }
    defined = [
        {"iri": shorten(s), "label": label_of(asserted, s)}
        for s in asserted.subjects(OWL.equivalentClass, None)
        if isinstance(s, URIRef)
    ]
    return jsonify({
        "engine": STORE.engine,
        "consistent": STORE.consistent,
        "inconsistencyReason": STORE.inconsistency_reason,
        "assertedTriples": len(asserted),
        "inferredTriples": len(STORE.inferred),
        "derivedTriples": max(len(STORE.inferred) - len(asserted), 0),
        "classes": len(classes),
        "objectProperties": len(obj_props),
        "dataProperties": len(data_props),
        "individuals": len(named),
        "definedClasses": sorted(defined, key=lambda d: d["label"]),
        "userAdditions": len(set(STORE.user_triples.subjects())),
    })


@app.route("/api/queries")
def api_queries():
    return jsonify([
        {k: v for k, v in q.items()} for q in QUERIES
    ])


@app.route("/api/sparql", methods=["POST"])
def api_sparql():
    payload = request.get_json(force=True) or {}
    query = (payload.get("query") or "").strip()
    mode = payload.get("mode", "inferred")

    if not query:
        return jsonify({"error": "Enter a SPARQL query to run."}), 400
    if not re.match(r"(?is)^\s*(prefix|base|select|ask|construct|describe)\b", query):
        return jsonify({"error": "Only SELECT, ASK, CONSTRUCT and DESCRIBE queries can be run here."}), 400

    graph = STORE.graph_for(mode)
    try:
        with _lock:
            result = graph.query(query)
    except Exception as exc:
        return jsonify({"error": f"The query could not be parsed: {exc}"}), 400

    columns = [str(v) for v in (result.vars or [])]
    rows = []
    for binding in result:
        row = {"cells": {}, "inferred": False}
        for column in columns:
            value = binding[column] if column in binding.labels else None
            row["cells"][column] = "" if value is None else tidy(value)
        rows.append(row)

    # Mark rows that only exist once reasoning has been applied, by re-running the
    # same query against the asserted graph and diffing the result sets.
    if mode == "inferred":
        try:
            with _lock:
                asserted_rows = {
                    tuple(normalise(b[c] if c in b.labels else None) for c in columns)
                    for b in STORE.working_graph().query(query)
                }
            for row in rows:
                key = tuple(normalise(row["cells"][c]) for c in columns)
                row["inferred"] = key not in asserted_rows
        except Exception:
            pass

    return jsonify({
        "columns": columns,
        "rows": rows,
        "count": len(rows),
        "mode": mode,
        "engine": STORE.engine,
        "inferredRows": sum(1 for r in rows if r["inferred"]),
    })


@app.route("/api/search")
def api_search():
    term = (request.args.get("q") or "").strip().lower()
    if len(term) < 2:
        return jsonify({"results": []})

    graph = STORE.working_graph()
    seen, results = set(), []
    for subject, _, label in graph.triples((None, RDFS.label, None)):
        if not isinstance(subject, URIRef) or subject in seen:
            continue
        if term in str(label).lower() or term in local_name(subject).lower():
            types = sorted({
                label_of(graph, t) for t in graph.objects(subject, RDF.type)
                if isinstance(t, URIRef) and str(t).startswith(str(SLT))
            })
            seen.add(subject)
            results.append({
                "iri": str(subject),
                "short": shorten(subject),
                "label": str(label),
                "types": types,
            })
    results.sort(key=lambda r: r["label"])
    return jsonify({"results": results[:25]})


@app.route("/api/entity")
def api_entity():
    iri = request.args.get("iri", "")
    if not iri:
        return jsonify({"error": "No entity requested."}), 400
    subject = URIRef(iri)

    asserted = STORE.working_graph()
    inferred = STORE.inferred

    if (subject, None, None) not in asserted and (subject, None, None) not in inferred:
        return jsonify({"error": "That entity is not in the ontology."}), 404

    # Facts already stated, keyed so that a literal re-serialised by the reasoner
    # ("349" -> "349.0") is not mistaken for something new.
    asserted_keys = {
        (p, normalise(o)) for p, o in asserted.predicate_objects(subject)
    }

    def collect(graph, only_new=False):
        rows, seen = [], set()
        for predicate, obj in graph.predicate_objects(subject):
            # Anonymous classes, owl bookkeeping and self-identity are machinery,
            # not knowledge a reader wants to see.
            if not isinstance(obj, URIRef) and not isinstance(obj, Literal):
                continue
            if predicate in (RDFS.comment, OWL.sameAs, OWL.differentFrom):
                continue
            if isinstance(obj, URIRef):
                if str(obj).startswith(str(OWL)):
                    continue
                if predicate != RDF.type and not str(obj).startswith(str(SLT)):
                    continue
            key = (predicate, normalise(obj))
            if key in seen:
                continue
            if only_new and key in asserted_keys:
                continue
            seen.add(key)
            rows.append({
                "predicate": shorten(predicate),
                "predicateLabel": label_of(asserted, predicate),
                "object": label_of(asserted, obj) if isinstance(obj, URIRef) else str(obj),
                "objectIri": str(obj) if isinstance(obj, URIRef) else None,
            })
        # rdf:type first, then alphabetical — the classification is the headline.
        rows.sort(key=lambda r: (r["predicate"] != "rdf:type", r["predicate"], r["object"]))
        return rows

    return jsonify({
        "iri": iri,
        "label": label_of(asserted, subject),
        "comment": str(asserted.value(subject, RDFS.comment) or ""),
        "asserted": collect(asserted),
        "inferred": collect(inferred, only_new=True),
    })


@app.route("/api/vocabulary")
def api_vocabulary():
    """Options needed by the 'add an attraction' form."""
    graph = STORE.working_graph()

    def instances_of(class_iri):
        out = []
        for subject in graph.subjects(RDF.type, class_iri):
            if isinstance(subject, URIRef):
                out.append({"iri": str(subject), "label": label_of(graph, subject)})
        return sorted(out, key=lambda d: d["label"])

    cities = instances_of(SLT.City) + instances_of(SLT.CoastalCity)
    activities = []
    for cls in (SLT.AdventureActivity, SLT.WildlifeActivity, SLT.CulturalActivity):
        activities += instances_of(cls)

    types = [
        {"iri": str(SLT[name]), "label": label_of(graph, SLT[name])}
        for name in ("Beach", "NationalPark", "Waterfall", "Mountain",
                     "BuddhistTemple", "HinduKovil", "Mosque", "Church",
                     "ArchaeologicalSite", "ColonialFort", "HistoricLandmark")
    ]
    access = [
        {"iri": str(SLT[name]), "label": label_of(graph, SLT[name])}
        for name in ("EasyAccess", "ModerateAccess", "DifficultAccess")
    ]
    heritage = instances_of(SLT.HeritageStatus)

    return jsonify({
        "cities": sorted({c["iri"]: c for c in cities}.values(), key=lambda d: d["label"]),
        "activities": sorted({a["iri"]: a for a in activities}.values(), key=lambda d: d["label"]),
        "types": types,
        "accessibility": access,
        "heritage": heritage,
    })


@app.route("/api/individual", methods=["POST"])
def api_add_individual():
    """Add an attraction, re-run the reasoner, and report what it classified it as."""
    data = request.get_json(force=True) or {}
    label = (data.get("label") or "").strip()
    type_iri = (data.get("type") or "").strip()
    city_iri = (data.get("city") or "").strip()
    access_iri = (data.get("accessibility") or "").strip()
    heritage_iri = (data.get("heritage") or "").strip()
    activities = [a for a in (data.get("activities") or []) if a]
    fee_raw = data.get("fee")

    errors = {}
    if len(label) < 3:
        errors["label"] = "Give the attraction a name of at least three characters."
    if not type_iri:
        errors["type"] = "Choose what kind of attraction this is."
    if not city_iri:
        errors["city"] = "Choose the city it sits in."
    if not access_iri:
        errors["accessibility"] = "Choose how easy it is to reach."
    if not activities:
        errors["activities"] = "Select at least one activity."
    try:
        fee = float(fee_raw)
        if fee < 0:
            errors["fee"] = "An entrance fee cannot be negative."
    except (TypeError, ValueError):
        errors["fee"] = "Enter the entrance fee as a number, in rupees."

    local = re.sub(r"[^A-Za-z0-9]", "", label.title())
    if not local:
        errors["label"] = "Use at least some letters or digits in the name."
    subject = SLT[local]
    if (subject, None, None) in STORE.working_graph():
        errors["label"] = f"{label} is already in the ontology. Choose a different name."

    if errors:
        return jsonify({"errors": errors}), 400

    additions = Graph()
    additions.add((subject, RDF.type, URIRef(type_iri)))
    additions.add((subject, RDF.type, OWL.NamedIndividual))
    additions.add((subject, RDFS.label, Literal(label, lang="en")))
    additions.add((subject, SLT.locatedInCity, URIRef(city_iri)))
    additions.add((subject, SLT.hasAccessibility, URIRef(access_iri)))
    additions.add((subject, SLT.entranceFeeLKR, Literal(fee, datatype=XSD.decimal)))
    for activity in activities:
        additions.add((subject, SLT.offersActivity, URIRef(activity)))
    if heritage_iri:
        additions.add((subject, SLT.hasHeritageStatus, URIRef(heritage_iri)))

    with _lock:
        snapshot = Graph()
        for triple in STORE.user_triples:
            snapshot.add(triple)
        for triple in additions:
            STORE.user_triples.add(triple)
        STORE.rebuild()

        if not STORE.consistent:
            STORE.user_triples = snapshot
            STORE.rebuild()
            return jsonify({
                "errors": {"_form": "Those facts contradict the ontology, so the addition was rolled back. "
                                    "Check the attraction type against the disjointness axioms."}
            }), 400

    derived = []
    for obj in STORE.inferred.objects(subject, RDF.type):
        if not isinstance(obj, URIRef) or not str(obj).startswith(str(SLT)):
            continue
        if (subject, RDF.type, obj) in STORE.working_graph():
            continue
        derived.append(label_of(STORE.working_graph(), obj))

    province = None
    for obj in STORE.inferred.objects(subject, SLT.locatedInProvince):
        province = label_of(STORE.working_graph(), obj)

    return jsonify({
        "iri": str(subject),
        "label": label,
        "inferredTypes": sorted(set(derived)),
        "inferredProvince": province,
        "engine": STORE.engine,
    })


@app.route("/api/reset", methods=["POST"])
def api_reset():
    with _lock:
        STORE.user_triples = Graph()
        STORE.rebuild()
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("Resolved paths:")
    print(f"  ontology  : {ONTOLOGY_TTL}")
    print(f"  templates : {_TEMPLATES_DIR}")
    print(f"  static    : {_STATIC_DIR or BASE_DIR / 'static'}")
    print("Loading ontology and running the reasoner ...")
    STORE.load()
    print(f"  engine       : {STORE.engine}")
    print(f"  consistent   : {STORE.consistent}")
    print(f"  asserted     : {len(STORE.asserted)} triples")
    print(f"  after reason : {len(STORE.inferred)} triples")
    print("Open http://127.0.0.1:5000")
    app.run(debug=False, port=5000)