# Sri Lanka Tourism and Cultural Heritage Ontology

Semantic Web and Ontologies coursework - OWL 2 DL ontology, HermiT reasoning, SPARQL, and a web application that shows asserted and inferred knowledge side by side.

```
├── ontology/
│   ├── srilanka-tourism.ttl      - main deliverable (Turtle)
│   └── srilanka-tourism.ttl      - same ontology, RDF/XML
    └── srilanka-tourism.owx
    └── srilanka-tourism.rdf    
├── queries/
│   └── competency-queries.sparql - ten queries, CQ1–CQ10
├── docs/
│   ├── REASONING-EVIDENCE.md     - Task 4: nine inferences, traced to axioms
script
│   ├── ontology-diagram.png - schema figure for the report
│   └── ontology-diagram.graph      
└── app/
    ├── app.py                    - Flask + RDFLib + Owlready2/HermiT + OWL RL
    ├── requirements.txt
    ├── templates/index.html
    └── static/css/style.css, static/js/app.js
```

---

## 1. Open the ontology in Protégé

1. Protégé 5.6 or later. `File ▸ Open…` -> `ontology/srilanka-tourism.ttl`
2. `Reasoner -> HermiT`, then `Reasoner -> Start reasoner`
3. Inferred entries appear in **yellow**. Good places to look:
   - `UNESCOWorldHeritageSite` - *Instances* (six, all inferred)
   - `SigiriyaRockFortress` - *Property assertions* -> `locatedInProvince Central Province`
   - The **Class hierarchy (inferred)** tab, where the computed multiple inheritance appears
4. `Window -> Tabs -> SPARQL Query` to run the queries from `queries/`
5. Protégé also ships **OntoGraf** (`Window -> Tabs -> OntoGraf`) 

---

## 2. Run the web application

**Requirements:** Python 3.10+ and a Java runtime (HermiT is a Java program).

```bash
cd app
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**.

First start takes a few seconds: the ontology is parsed, HermiT classifies it, and an OWL RL closure materialises the entailments. The status bar reports which engine ran and how many facts were derived.

### Check Java is present

```bash
java -version
```

If that fails, install a JRE (`sudo apt install default-jre`, or Temurin on Windows/macOS). **The application still runs without Java** - it falls back to OWL RL alone and says so - but two inferences fall outside the OWL RL profile and will be missing:

- `MultiActivityDestination` (qualified cardinality) -> CQ9 returns nothing
- `BudgetAttraction` (datatype restriction)

Everything else works either way.

---

## 3. Using the application

**Ask** - the ten competency questions. Pick one, or edit the SPARQL directly. `Ctrl/Cmd + Enter` runs it.

The **Asserted only / With reasoning** switch is the heart of the demo. It runs the identical query against both graphs. Rows that exist only after reasoning are lit blue and tagged `inferred`. CQ6–CQ10 return **zero rows** with reasoning off.

**Explore** - search any individual. The card splits into *Stated in the ontology* and *Added by the reasoner*. Try `Sigiriya` (the city): three facts stated, four derived.

**Add an attraction** - describe a new site and the reasoner classifies it live. Suggested demo: *Pigeon Island Marine Park*, National park, Trincomalee, Moderate access, LKR 2000, activities Birdwatching + Surfing + Photography. It comes back with Eastern Province and six inferred class memberships. **Reset additions** restores the submitted ontology; additions are in memory only and never touch the `.ttl` file.

---

## Ontology at a glance

| | |
|---|---|
| Namespace | `http://www.semanticweb.org/lk/ontologies/2026/srilanka-tourism#` (prefix `slt:`) |
| Profile | OWL 2 DL |
| Classes | 44 (7 of them defined) |
| Object properties | 15 |
| Data properties | 7 |
| Named individuals | 73 |
| Asserted triples | 915 |
| After reasoning | 2,503 — **1,588 derived** |
| Consistent | Yes, no unsatisfiable classes |
