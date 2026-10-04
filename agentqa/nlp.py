"""Reusable NLP utilities.

* DocumentClassifier — embedding-based attribute tagging with pluggable label sets
  (confidentiality level, jurisdiction, document type...). Optional LLM zero-shot mode.
* extract_entities   — regex extraction of dates, amounts, emails, legal references...
                       with an optional LLM pass for free-form fields.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .agent.embeddings import Embedder, build_prototypes, default_embedder, nearest_label
from .agent.llm import LLMClient, OfflineLLM, parse_json

PRESETS: dict[str, dict[str, list[str]]] = {
    "confidentiality": {
        "public": ["press release published publicly available to everyone", "communiqué de presse public website announcement"],
        "internal": [
            "internal memo for employees only not for external distribution",
            "note interne réservée au personnel meeting minutes team",
        ],
        "confidential": [
            "confidential do not share client data contract terms salary",
            "confidentiel données client accord de confidentialité NDA",
        ],
        "secret": [
            "strictly confidential board only merger acquisition undisclosed",
            "secret défense strictement confidentiel comité de direction fusion",
        ],
    },
    "jurisdiction": {
        "France": [
            "Cour de cassation Conseil d'État tribunal judiciaire Code civil article L. France Paris",
            "cour d'appel de Paris jurisprudence française décret loi française",
        ],
        "European Union": [
            "Court of Justice of the European Union CJEU regulation directive GDPR Article",
            "Union européenne règlement RGPD directive CJUE Luxembourg",
        ],
        "United States": [
            "Supreme Court of the United States federal circuit U.S.C. plaintiff v. defendant",
            "district court state of California New York federal statute",
        ],
        "United Kingdom": ["UK Supreme Court High Court England and Wales EWCA Act of Parliament", "Court of Appeal London UKSC Crown"],
    },
    "document_type": {
        "contract": [
            "agreement between the parties hereby agree terms conditions termination clause",
            "contrat entre les parties clause de résiliation conditions générales",
        ],
        "court decision": ["the court holds judgment appeal dismissed ruling", "la cour statue arrêt rejette le pourvoi décision"],
        "invoice": ["invoice amount due payment total VAT number", "facture montant TTC TVA à payer échéance"],
        "incident report": ["incident outage root cause impact remediation", "rapport d'incident panne cause impact actions correctives"],
    },
}


@dataclass
class Classification:
    label: str
    confidence: float
    method: str


class DocumentClassifier:
    def __init__(self, labels: dict[str, list[str]], embedder: Embedder | None = None, llm: LLMClient | None = None):
        self.labels = labels
        self.embedder = embedder or default_embedder()
        self.prototypes = build_prototypes(self.embedder, labels)
        self.llm = llm or OfflineLLM()

    @classmethod
    def preset(cls, name: str, **kwargs) -> DocumentClassifier:
        if name not in PRESETS:
            raise KeyError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(PRESETS[name], **kwargs)

    def classify(self, texts: list[str]) -> list[Classification]:
        if not isinstance(self.llm, OfflineLLM):
            try:
                return [self._llm_classify(t) for t in texts]
            except Exception:
                pass
        vectors = self.embedder.embed(texts)
        return [Classification(label, round(max(sim, 0.0), 3), "embedding") for label, sim in nearest_label(vectors, self.prototypes)]

    def _llm_classify(self, text: str) -> Classification:
        answer = parse_json(
            self.llm.complete(
                'Classify the document. Answer JSON only: {"label": str, "confidence": number 0-1}.',
                f"Allowed labels and their descriptions: {json.dumps(self.labels)}\n\nDocument:\n{text[:6000]}",
                max_tokens=100,
            )
        )
        label = answer.get("label")
        if label not in self.labels:
            raise ValueError(f"LLM returned unknown label {label!r}")
        return Classification(label, float(answer.get("confidence", 0.5)), "llm")


_PATTERNS = {
    "emails": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "dates": re.compile(
        r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}(?:er)? (?:janvier|février|mars|avril|mai|juin|juillet|"
        r"août|septembre|octobre|novembre|décembre|January|February|March|April|May|June|July|August|September|"
        r"October|November|December) \d{4})\b",
        re.IGNORECASE,
    ),
    "amounts": re.compile(r"(?:[€$£]\s?\d[\d\s.,]*\d|\b\d[\d\s.,]*\d?\s?(?:€|EUR|USD|\$|£|k€|M€))"),
    "percentages": re.compile(r"\b\d+(?:[.,]\d+)?\s?%"),
    "legal_references": re.compile(
        r"\b(?:article|art\.)\s+[LRD]?\.?\s?\d+(?:-\d+)*|\bn°\s?\d{2}-\d{2}\.\d{3}|\b\d+ U\.S\.C\. § ?\d+", re.IGNORECASE
    ),
    "ticket_references": re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b"),
}


def extract_entities(text: str, llm: LLMClient | None = None, fields: list[str] | None = None) -> dict:
    out: dict = {k: sorted({m.group(0).strip() for m in p.finditer(text)}) for k, p in _PATTERNS.items()}
    llm = llm or OfflineLLM()
    if fields and not isinstance(llm, OfflineLLM):
        try:
            out["llm_fields"] = parse_json(
                llm.complete(
                    "Extract the requested fields from the text. JSON object only; use null when absent.",
                    f"Fields: {fields}\n\nText:\n{text[:8000]}",
                    max_tokens=600,
                )
            )
        except Exception as exc:
            out["llm_fields_error"] = str(exc)
    return out
