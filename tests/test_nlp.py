from agentqa.nlp import DocumentClassifier, extract_entities


def test_jurisdiction_preset():
    clf = DocumentClassifier.preset("jurisdiction")
    out = clf.classify(
        [
            "Arrêt de la Cour de cassation, chambre sociale, rendu en application de l'article L. 1234-5 du Code du travail.",
            "The Court of Justice of the European Union interpreted Article 17 of the GDPR regulation.",
            "Plaintiff v. Defendant, United States District Court for the Northern District of California.",
        ]
    )
    assert [c.label for c in out] == ["France", "European Union", "United States"]
    assert all(c.method == "embedding" for c in out)


def test_confidentiality_preset():
    out = DocumentClassifier.preset("confidentiality").classify(
        [
            "Press release: the company publicly announced its new product today.",
            "Confidential: client data and contract terms, covered by NDA, do not share.",
        ]
    )
    assert [c.label for c in out] == ["public", "confidential"]


def test_llm_classification_path(fake_llm):
    [c] = DocumentClassifier.preset("confidentiality", llm=fake_llm).classify(["anything"])
    assert (c.label, c.method) == ("confidential", "llm")


def test_custom_labels():
    clf = DocumentClassifier({"water": ["leak pipe water network pressure"], "waste": ["landfill recycling bins waste"]})
    assert clf.classify(["Burst pipe causing a water leak on the network"])[0].label == "water"


def test_extraction():
    text = (
        "Facture n°12 du 15 mars 2026 : montant 1 250,00 € TTC, TVA 20 %, contact billing@suez.example. "
        "Voir article L. 1234-5 et ticket TF-42. Échéance 2026-04-30."
    )
    e = extract_entities(text)
    assert "billing@suez.example" in e["emails"]
    assert "15 mars 2026" in e["dates"] and "2026-04-30" in e["dates"]
    assert any("1 250,00 €" in a for a in e["amounts"])
    assert "20 %" in e["percentages"]
    assert "TF-42" in e["ticket_references"]
    assert any(r.lower().startswith("article l") for r in e["legal_references"])
