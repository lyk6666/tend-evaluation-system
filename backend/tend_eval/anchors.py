from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from .config import Settings
from .contracts import (
    ANCHOR_STAGE_NAMES,
    AmbiguityExtraction,
    AnchorAmbiguity,
    AnchorBundle,
    AnchorGraph,
    AnchorKind,
    AnchorRelation,
    AnchorRunCreate,
    AnchorRunMode,
    AnchorRunStatus,
    AnchorRunView,
    AnchorSource,
    AnchorStageTrace,
    DeterministicAnchorExtraction,
    NormalizationCue,
    NormalizedClause,
    NormalizedQuestion,
    NormalizedScalar,
    RetrievalSpecification,
    RetrievalSpecificationSet,
    SemanticAnchorExtraction,
    TypedSemanticAnchor,
    anchor_now,
)
from .store import AnchorRunStore


T = TypeVar("T", bound=BaseModel)


NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


@dataclass(frozen=True)
class CueRule:
    pattern: str
    category: str
    canonical: str
    source: str = "phrase_rule"


CUE_RULES = [
    CueRule(r"\b(?:at least|no fewer than|a minimum of)\b", "comparison", "GTE"),
    CueRule(r"\b(?:at most|no more than|a maximum of)\b", "comparison", "LTE"),
    CueRule(r"\b(?:more than|greater than|over)\b", "comparison", "GT"),
    CueRule(r"\b(?:less than|fewer than|under)\b", "comparison", "LT"),
    CueRule(r"\b(?:equal to|equals?|exactly)\b", "comparison", "EQ"),
    CueRule(
        r"\b(?:highest[- ]scoring|top[- ]scoring|highest|largest|most)\b",
        "ranking",
        "ARGMAX",
        "morphology",
    ),
    CueRule(
        r"\b(?:lowest[- ]scoring|lowest|smallest|least)\b",
        "ranking",
        "ARGMIN",
        "morphology",
    ),
    CueRule(r"\b(?:number of|how many|count of)\b", "aggregation", "COUNT"),
    CueRule(r"\b(?:total|sum of|combined)\b", "aggregation", "SUM"),
    CueRule(r"\b(?:average|mean)\b", "aggregation", "AVG"),
    CueRule(r"\b(?:minimum|min of)\b", "aggregation", "MIN"),
    CueRule(r"\b(?:maximum|max of)\b", "aggregation", "MAX"),
    CueRule(r"\b(?:for each|per|for every|grouped by|by each)\b", "grouping", "PARTITION"),
    CueRule(r"\b(?:descending|highest first|decreasing)\b", "sorting", "DESC"),
    CueRule(r"\b(?:ascending|lowest first|increasing)\b", "sorting", "ASC"),
    CueRule(r"\b(?:all tied|including ties|keep ties|return ties)\b", "tie_policy", "KEEP_ALL_TIES"),
    CueRule(r"\b(?:return|show|list|display|find|give)\b", "output", "RETURN"),
    CueRule(r"\b(?:not|without|excluding|except|never)\b", "negation", "NOT"),
    CueRule(r"\b(?:each|every|any|all)\b", "quantifier", "QUANTIFIER"),
]


class StructuredAnchorLLM:
    """Small strict Chat Completions adapter used only for semantic enrichment."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.api_key_configured and not self.settings.llm_stub

    def parse(
        self,
        *,
        schema: type[T],
        schema_name: str,
        instructions: str,
        user_input: str,
    ) -> T:
        if not self.enabled:
            raise RuntimeError("The semantic model is not configured")
        strict_schema = self._strict_schema(schema.model_json_schema())
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_input},
            ],
            "reasoning_effort": self.settings.reasoning_effort,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": strict_schema,
                },
            },
        }
        request = urllib.request.Request(
            f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Semantic model returned HTTP {error.code}: {detail}") from error
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Semantic model returned no structured content")
        return schema.model_validate_json(content)

    @classmethod
    def _strict_schema(cls, value: Any) -> Any:
        """Apply the closed-object rules required by strict structured output."""
        if isinstance(value, list):
            return [cls._strict_schema(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: cls._strict_schema(item) for key, item in value.items()}
        result.pop("default", None)
        properties = result.get("properties")
        if isinstance(properties, dict):
            result["additionalProperties"] = False
            result["required"] = list(properties)
        return result


class QuestionNormalizer:
    def normalize(self, question: str, *, locale: str, timezone: str) -> NormalizedQuestion:
        original = question.strip()
        normalized = unicodedata.normalize("NFKC", original)
        translations = str.maketrans(
            {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}
        )
        normalized = re.sub(r"[ \t]+", " ", normalized.translate(translations))
        clauses = [
            NormalizedClause(
                clause_id=f"clause_{index}",
                text=match.group(0).strip(),
                start=match.start(),
                end=match.end(),
            )
            for index, match in enumerate(
                re.finditer(r"[^.!?]+(?:[.!?]+|$)", normalized), start=1
            )
            if match.group(0).strip()
        ]
        scalars = self._scalars(normalized)
        cues = self._cues(normalized)
        semantic_spans = self._semantic_spans(normalized)
        notes = [
            "Unicode, whitespace, and typography were normalized without paraphrasing the question.",
            "Canonical cues are semantic evidence, not query-plan or schema commitments.",
        ]
        if any(item.scalar_type == "year" for item in scalars):
            notes.append("Year-like values remain domain scalars until later evidence establishes time semantics.")
        return NormalizedQuestion(
            original_text=original,
            normalized_text=normalized,
            locale=locale,
            timezone=timezone,
            clauses=clauses,
            scalars=scalars,
            cues=cues,
            semantic_spans=semantic_spans,
            notes=notes,
        )

    def _scalars(self, text: str) -> list[NormalizedScalar]:
        values: list[NormalizedScalar] = []
        occupied: list[tuple[int, int]] = []

        def add(surface: str, value: Any, scalar_type: str, start: int, end: int) -> None:
            values.append(
                NormalizedScalar(
                    scalar_id="",
                    surface=surface,
                    normalized=value,
                    scalar_type=scalar_type,  # type: ignore[arg-type]
                    context=self._context(text, start, end),
                    start=start,
                    end=end,
                )
            )
            occupied.append((start, end))

        for match in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", text):
            add(match.group(0), match.group(0), "date", match.start(), match.end())
        for match in re.finditer(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", text):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            surface = match.group(0)
            value: int | float = float(surface) if "." in surface else int(surface)
            scalar_type = "year" if re.fullmatch(r"(?:19|20)\d{2}", surface) else (
                "number" if isinstance(value, float) else "integer"
            )
            add(surface, value, scalar_type, match.start(), match.end())
        word_pattern = r"\b(?:" + "|".join(NUMBER_WORDS) + r")\b"
        for match in re.finditer(word_pattern, text, re.I):
            if not any(start <= match.start() < end for start, end in occupied):
                add(
                    match.group(0),
                    NUMBER_WORDS[match.group(0).lower()],
                    "integer",
                    match.start(),
                    match.end(),
                )
        for match in re.finditer(r"\b(?:true|false)\b", text, re.I):
            add(
                match.group(0),
                match.group(0).lower() == "true",
                "boolean",
                match.start(),
                match.end(),
            )
        for match in re.finditer(r"(['\"])(.+?)\1", text):
            if not any(start <= match.start(2) < end for start, end in occupied):
                add(match.group(2), match.group(2), "string", match.start(2), match.end(2))
        values.sort(key=lambda item: item.start)
        for index, item in enumerate(values, start=1):
            item.scalar_id = f"scalar_{index}"
        return values

    def _cues(self, text: str) -> list[NormalizationCue]:
        candidates: list[tuple[int, int, CueRule, str]] = []
        for rule in CUE_RULES:
            for match in re.finditer(rule.pattern, text, re.I):
                candidates.append((match.start(), match.end(), rule, match.group(0)))
        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        selected: list[tuple[int, int, CueRule, str]] = []
        for candidate in candidates:
            start, end, _, _ = candidate
            if any(start < other_end and end > other_start for other_start, other_end, _, _ in selected):
                continue
            selected.append(candidate)
        selected.sort(key=lambda item: item[0])
        return [
            NormalizationCue(
                cue_id=f"cue_{index}",
                surface=surface,
                category=rule.category,  # type: ignore[arg-type]
                canonical=rule.canonical,
                scope_hint=text[max(0, start - 34) : min(len(text), end + 58)].strip(),
                start=start,
                end=end,
                source=rule.source,  # type: ignore[arg-type]
            )
            for index, (start, end, rule, surface) in enumerate(selected, start=1)
        ]

    @staticmethod
    def _context(text: str, start: int, end: int) -> str:
        before = re.findall(r"[A-Za-z][\w-]*", text[max(0, start - 30) : start])
        after = re.findall(r"[A-Za-z][\w-]*", text[end : end + 30])
        return " ".join([*before[-2:], *after[:2]]).lower()

    @staticmethod
    def _semantic_spans(text: str) -> list[str]:
        patterns = [
            r"\b(?:highest|lowest|top|most|least)[- A-Za-z]*?\b[A-Za-z][\w-]*",
            r"\b(?:total|average|number of|count of)\s+[A-Za-z][A-Za-z -]*?(?=,|\.|\band\b|$)",
            r"\b(?:for each|per|for every)\s+[A-Za-z][A-Za-z -]*?(?=\bthat\b|,|\.|$)",
            r"\b(?:that|which|who)\s+[A-Za-z][A-Za-z -]*?(?=,|\.|$)",
            r"\b[A-Za-z][\w-]*(?:'s)?\s+(?:full\s+)?(?:name|title|id|score|status|date|type)\b",
        ]
        found: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.I):
                phrase = match.group(0).strip()
                if phrase and phrase.lower() not in {item.lower() for item in found}:
                    found.append(phrase)
        return found


class DeterministicAnchorExtractor:
    def extract(self, normalized: NormalizedQuestion) -> DeterministicAnchorExtraction:
        anchors: list[TypedSemanticAnchor] = []
        relations: list[AnchorRelation] = []

        def add(
            kind: AnchorKind,
            surface: str,
            canonical: str,
            description: str,
            role: str,
            types: list[str],
            start: int | None,
            end: int | None,
            *,
            confidence: float = 0.84,
            alternatives: list[str] | None = None,
            retrieval_required: bool = True,
        ) -> TypedSemanticAnchor:
            anchor = TypedSemanticAnchor(
                anchor_id=f"d{len(anchors) + 1}",
                kind=kind,
                surface=surface,
                canonical=canonical,
                description=description,
                semantic_role=role,
                expected_bson_types=types,
                source=AnchorSource.RULE,
                start=start,
                end=end,
                confidence=confidence,
                alternatives=alternatives or [],
                retrieval_required=retrieval_required,
            )
            anchors.append(anchor)
            return anchor

        for scalar in normalized.scalars:
            temporal = scalar.scalar_type in {"year", "date"} or any(
                token in scalar.context for token in ("season", "year", "date", "month")
            )
            stored = scalar.scalar_type == "string"
            kind = AnchorKind.STORED_LITERAL if stored else (
                AnchorKind.TEMPORAL if temporal else AnchorKind.QUERY_CONSTANT
            )
            add(
                kind,
                scalar.surface,
                str(scalar.normalized),
                f"Explicit {scalar.scalar_type} scalar in context: {scalar.context}",
                "stored_literal" if stored else ("temporal_filter" if temporal else "constraint_value"),
                ["string"] if stored else (
                    ["date", "string", "int", "long"] if temporal else ["int", "long", "double", "decimal"]
                ),
                scalar.start,
                scalar.end,
                confidence=0.99,
                retrieval_required=stored or temporal,
            )

        cue_kind = {
            "comparison": AnchorKind.COMPARISON,
            "ranking": AnchorKind.OPERATION,
            "aggregation": AnchorKind.OPERATION,
            "grouping": AnchorKind.GROUPING,
            "sorting": AnchorKind.SORT,
            "tie_policy": AnchorKind.TIE_POLICY,
            "output": AnchorKind.OPERATION,
            "negation": AnchorKind.NEGATION,
            "quantifier": AnchorKind.QUANTIFIER,
        }
        cue_anchors: dict[str, list[TypedSemanticAnchor]] = defaultdict(list)
        for cue in normalized.cues:
            anchor = add(
                cue_kind[cue.category],
                cue.surface,
                cue.canonical,
                f"Canonical {cue.category} cue scoped by: {cue.scope_hint}",
                cue.category,
                [],
                cue.start,
                cue.end,
                confidence=0.98 if cue.source == "phrase_rule" else 0.91,
                retrieval_required=False,
            )
            cue_anchors[cue.canonical].append(anchor)

        entity_anchors = self._entities(normalized.normalized_text, add)
        output_anchors = self._outputs(normalized.normalized_text, add)
        measure_anchors = self._measures(normalized.normalized_text, add)
        relationship_anchors = self._relationships(normalized.normalized_text, add)

        def relate(source: TypedSemanticAnchor, target: TypedSemanticAnchor, kind: str, text: str) -> None:
            key = (source.anchor_id, target.anchor_id, kind)
            if any((item.source_anchor_id, item.target_anchor_id, item.relation_type) == key for item in relations):
                return
            relations.append(
                AnchorRelation(
                    relation_id=f"dr{len(relations) + 1}",
                    source_anchor_id=source.anchor_id,
                    target_anchor_id=target.anchor_id,
                    relation_type=kind,  # type: ignore[arg-type]
                    description=text,
                    confidence=0.84,
                    source=AnchorSource.INFERRED,
                )
            )

        constants = [item for item in anchors if item.kind in {AnchorKind.QUERY_CONSTANT, AnchorKind.STORED_LITERAL, AnchorKind.TEMPORAL}]
        for comparison in [item for item in anchors if item.kind == AnchorKind.COMPARISON]:
            target = self._nearest(comparison, constants)
            if target:
                relate(comparison, target, "compares_to", "Comparison applies to the nearest explicit scalar.")
        groups = cue_anchors.get("PARTITION", [])
        if groups and entity_anchors:
            target = self._nearest(groups[0], entity_anchors)
            if target:
                relate(groups[0], target, "partitioned_by", "The operation is partitioned by this entity.")
        rankings = [*cue_anchors.get("ARGMAX", []), *cue_anchors.get("ARGMIN", [])]
        if rankings and measure_anchors:
            target = self._nearest(rankings[0], measure_anchors)
            if target:
                relate(rankings[0], target, "ranked_by", "Ranking uses this measure.")
        for canonical in ("SUM", "AVG", "MIN", "MAX", "COUNT"):
            for operation in cue_anchors.get(canonical, []):
                target = self._nearest(operation, measure_anchors)
                if target:
                    relate(operation, target, "aggregates", "Aggregation applies to this measure.")
        for sort_anchor in [item for item in anchors if item.kind == AnchorKind.SORT]:
            target = self._nearest(sort_anchor, measure_anchors)
            if target:
                relate(sort_anchor, target, "sorted_by", "Final ordering uses this measure.")
        if cue_anchors.get("KEEP_ALL_TIES") and rankings:
            relate(cue_anchors["KEEP_ALL_TIES"][0], rankings[0], "modifies", "Tie policy modifies ranking.")
        for relationship in relationship_anchors:
            for entity in sorted(entity_anchors, key=lambda item: self._distance(relationship, item))[:2]:
                relate(relationship, entity, "related_to", "Relationship connects the nearby entity concept.")
        for output in output_anchors:
            target = self._best_lexical_target(output, [*entity_anchors, *measure_anchors])
            if target:
                relate(target, output, "outputs", "This concept is explicitly requested in the result.")

        return DeterministicAnchorExtraction(
            anchors=anchors,
            relations=relations,
            unresolved_phrases=normalized.semantic_spans,
            notes=[
                "Rules cover closed-vocabulary operators, explicit values, result phrases, and conservative semantic candidates.",
                "Unresolved phrases are forwarded intact to semantic extraction.",
            ],
        )

    def _entities(self, text: str, add: Callable[..., TypedSemanticAnchor]) -> list[TypedSemanticAnchor]:
        candidates: list[tuple[str, int, int, str]] = []
        patterns = [
            (r"\b(?:for each|per|for every)\s+([A-Za-z][\w-]*)", "group_entity"),
            (r"\b(?:highest[- ]scoring|lowest[- ]scoring|top[- ]scoring|highest|lowest)\s+([A-Za-z][\w-]*)", "ranked_entity"),
            (r"\b([A-Za-z][\w-]*)\s+(?:that|which|who)\s+", "constrained_entity"),
            (r"\b(?:one|a|an|any|each|every)\s+([A-Za-z][\w-]*)\b", "related_entity"),
        ]
        stop = {"least", "most", "minimum", "maximum", "total", "number", "result"}
        for pattern, role in patterns:
            for match in re.finditer(pattern, text, re.I):
                surface = match.group(1)
                canonical = self._singular(surface.lower())
                if canonical in stop or any(item[0].lower() == canonical for item in candidates):
                    continue
                candidates.append((surface, match.start(1), match.end(1), role))
        return [
            add(
                AnchorKind.ENTITY,
                surface,
                self._singular(surface.lower()),
                f"Entity candidate explicitly used as a {role.replace('_', ' ')}.",
                role,
                [],
                start,
                end,
                confidence=0.82,
            )
            for surface, start, end, role in candidates
        ]

    def _outputs(self, text: str, add: Callable[..., TypedSemanticAnchor]) -> list[TypedSemanticAnchor]:
        outputs: list[TypedSemanticAnchor] = []
        for match in re.finditer(r"\b(?:return|show|list|display|give)\s+(.+?)(?=\.(?:\s|$)|$)", text, re.I):
            body = match.group(1).strip()
            if body.lower().startswith(("all tied", "the result", "results")):
                continue
            cursor = match.start(1)
            for part in re.split(r",|\band\b", body, flags=re.I):
                surface = part.strip(" ,")
                if not surface or surface.lower().startswith(("order ", "sort ")):
                    continue
                start = text.lower().find(surface.lower(), cursor)
                start = match.start(1) if start < 0 else start
                cursor = start + len(surface)
                canonical = re.sub(r"^(?:the|a|an)\s+", "", surface.lower())
                canonical = re.sub(r"([a-z])'s\b", r"\1", canonical)
                outputs.append(
                    add(
                        AnchorKind.OUTPUT,
                        surface,
                        canonical,
                        f"Requested result field or derived value: {surface}",
                        "requested_output",
                        self._types(canonical),
                        start,
                        start + len(surface),
                        confidence=0.91,
                    )
                )
        return outputs

    def _measures(self, text: str, add: Callable[..., TypedSemanticAnchor]) -> list[TypedSemanticAnchor]:
        measures: list[TypedSemanticAnchor] = []
        patterns = [
            (r"\b(?:total|sum of)\s+[A-Za-z][A-Za-z -]*?(?=\s+earned|,|\.|\band\b|$)", "sum_measure"),
            (r"\b(?:number of|count of)\s+[A-Za-z][A-Za-z -]*?(?=,|\.|\band\b|$)", "count_measure"),
            (r"\b(?:average|mean)\s+[A-Za-z][A-Za-z -]*?(?=,|\.|\band\b|$)", "average_measure"),
            (r"\b[A-Za-z][\w-]*\s+(?:score|points?|amount|salary|price|revenue|duration|count)\b", "measure"),
        ]
        for pattern, role in patterns:
            for match in re.finditer(pattern, text, re.I):
                surface = match.group(0).strip()
                canonical = surface.lower()
                if any(item.canonical == canonical for item in measures):
                    continue
                measures.append(
                    add(
                        AnchorKind.MEASURE,
                        surface,
                        canonical,
                        "Numeric or aggregatable semantic measure.",
                        role,
                        ["int", "long", "double", "decimal"],
                        match.start(),
                        match.end(),
                        confidence=0.87,
                        alternatives=["stored aggregate", "computed from lower-level records"],
                    )
                )
        return measures

    def _relationships(self, text: str, add: Callable[..., TypedSemanticAnchor]) -> list[TypedSemanticAnchor]:
        values: list[TypedSemanticAnchor] = []
        for match in re.finditer(r"\b(?:that|which|who)\s+(.+?)(?=,|\.|$)", text, re.I):
            surface = match.group(0).strip()
            values.append(
                add(
                    AnchorKind.RELATIONSHIP,
                    surface,
                    match.group(1).strip().lower(),
                    "Relative-clause relationship or eligibility condition.",
                    "relationship_constraint",
                    [],
                    match.start(),
                    match.end(),
                    confidence=0.79,
                )
            )
        return values

    @staticmethod
    def _types(text: str) -> list[str]:
        if any(token in text for token in ("name", "title", "label", "type", "status")):
            return ["string"]
        if any(token in text for token in ("count", "number", "total", "points", "score", "amount", "price")):
            return ["int", "long", "double", "decimal"]
        if any(token in text for token in ("date", "time", "year", "season")):
            return ["date", "string", "int", "long"]
        return []

    @staticmethod
    def _singular(value: str) -> str:
        return value[:-3] + "y" if value.endswith("ies") else (value[:-1] if value.endswith("s") and not value.endswith("ss") else value)

    @staticmethod
    def _distance(left: TypedSemanticAnchor, right: TypedSemanticAnchor) -> int:
        return abs((left.start or 0) - (right.start or 0))

    def _nearest(self, source: TypedSemanticAnchor, values: list[TypedSemanticAnchor]) -> TypedSemanticAnchor | None:
        return min(values, key=lambda item: self._distance(source, item)) if values else None

    @staticmethod
    def _best_lexical_target(source: TypedSemanticAnchor, values: list[TypedSemanticAnchor]) -> TypedSemanticAnchor | None:
        source_tokens = set(re.findall(r"[a-z]+", source.canonical)) - {
            "the", "a", "an", "name", "full", "total", "number", "of", "for", "that", "earned"
        }
        ranked = [
            (len(source_tokens & set(re.findall(r"[a-z]+", item.canonical))), item)
            for item in values
        ]
        ranked = [item for item in ranked if item[0] > 0]
        return max(ranked, key=lambda item: item[0])[1] if ranked else None


class SemanticAnchorExtractor:
    def __init__(self, llm: StructuredAnchorLLM):
        self.llm = llm

    def extract(
        self,
        normalized: NormalizedQuestion,
        deterministic: DeterministicAnchorExtraction,
        *,
        use_llm: bool,
        fallback_on_error: bool,
    ) -> SemanticAnchorExtraction:
        if use_llm and self.llm.enabled:
            try:
                result = self.llm.parse(
                    schema=SemanticAnchorExtraction,
                    schema_name="typed_semantic_anchors",
                    instructions=(
                        "Extract schema-independent typed semantic anchors. Return only the strict structured object. "
                        "Never select collections, fields, paths, stored values, MongoDB operators, or a query plan. "
                        "Create s1, s2, ... anchors for entities, attributes, measures, outputs, relationships, stored "
                        "literal mentions, and implicit scope. Preserve exact spans for explicit anchors and mark inferred "
                        "anchors explicit=false. Add sr1, sr2, ... relations, including useful links to supplied d-ids. "
                        "Retain stored-versus-computed alternatives instead of resolving them. Use source='llm'."
                    ),
                    user_input=json.dumps(
                        {
                            "question": normalized.original_text,
                            "normalized": normalized.model_dump(mode="json"),
                            "deterministic": deterministic.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                )
                return self._sanitize(result, normalized.original_text, deterministic)
            except Exception as error:
                if not fallback_on_error:
                    raise
                result = self._fallback(normalized, deterministic)
                result.notes.insert(
                    0,
                    f"Semantic model unavailable ({type(error).__name__}); auto mode used deterministic enrichment.",
                )
                return result
        return self._fallback(normalized, deterministic)

    def _sanitize(
        self,
        result: SemanticAnchorExtraction,
        question: str,
        deterministic: DeterministicAnchorExtraction,
    ) -> SemanticAnchorExtraction:
        semantic_ids: set[str] = set()
        for index, anchor in enumerate(result.anchors, start=1):
            anchor.anchor_id = f"s{index}"
            anchor.source = AnchorSource.LLM
            if anchor.explicit:
                start = question.lower().find(anchor.surface.lower())
                if start >= 0:
                    anchor.start = start
                    anchor.end = start + len(anchor.surface)
                else:
                    anchor.explicit = False
                    anchor.start = None
                    anchor.end = None
            semantic_ids.add(anchor.anchor_id)
        allowed = semantic_ids | {item.anchor_id for item in deterministic.anchors}
        clean: list[AnchorRelation] = []
        for relation in result.relations:
            if relation.source_anchor_id in allowed and relation.target_anchor_id in allowed:
                relation.relation_id = f"sr{len(clean) + 1}"
                relation.source = AnchorSource.LLM
                clean.append(relation)
        result.relations = clean
        return result

    def _fallback(
        self,
        normalized: NormalizedQuestion,
        deterministic: DeterministicAnchorExtraction,
    ) -> SemanticAnchorExtraction:
        anchors: list[TypedSemanticAnchor] = []
        relations: list[AnchorRelation] = []

        def add_from(item: TypedSemanticAnchor, kind: AnchorKind, role: str) -> TypedSemanticAnchor:
            anchor = TypedSemanticAnchor(
                anchor_id=f"s{len(anchors) + 1}",
                kind=kind,
                surface=item.surface,
                canonical=item.canonical,
                description=f"Semantic concept underlying: {item.surface}",
                semantic_role=role,
                expected_bson_types=item.expected_bson_types,
                explicit=item.explicit,
                source=AnchorSource.INFERRED,
                start=item.start,
                end=item.end,
                confidence=max(0.68, item.confidence - 0.12),
                alternatives=item.alternatives,
                retrieval_required=True,
            )
            anchors.append(anchor)
            return anchor

        for item in deterministic.anchors:
            if item.kind in {AnchorKind.ENTITY, AnchorKind.RELATIONSHIP, AnchorKind.STORED_LITERAL}:
                add_from(item, item.kind, item.semantic_role)
            elif item.kind == AnchorKind.OUTPUT:
                kind = AnchorKind.MEASURE if any(
                    token in item.canonical for token in ("total", "number", "count", "score", "points", "average")
                ) else AnchorKind.ATTRIBUTE
                concept = add_from(
                    item,
                    kind,
                    "result_measure" if kind == AnchorKind.MEASURE else "result_attribute",
                )
                relations.append(
                    AnchorRelation(
                        relation_id=f"sr{len(relations) + 1}",
                        source_anchor_id=concept.anchor_id,
                        target_anchor_id=item.anchor_id,
                        relation_type="outputs",
                        description="The semantic concept realizes this requested output.",
                        confidence=0.82,
                        source=AnchorSource.INFERRED,
                    )
                )
        entities = [item for item in anchors if item.kind == AnchorKind.ENTITY]
        measures = [item for item in anchors if item.kind == AnchorKind.MEASURE]
        for measure in measures:
            for entity in entities[:2]:
                relations.append(
                    AnchorRelation(
                        relation_id=f"sr{len(relations) + 1}",
                        source_anchor_id=measure.anchor_id,
                        target_anchor_id=entity.anchor_id,
                        relation_type="same_scope",
                        description="The measure may be scoped to this query entity.",
                        confidence=0.68,
                        source=AnchorSource.INFERRED,
                    )
                )
        return SemanticAnchorExtraction(
            anchors=anchors,
            relations=relations,
            notes=[
                "Deterministic semantic enrichment was used; no schema or database was inspected.",
                "Output concepts remain distinct from their requested output slots.",
            ],
        )


class AmbiguityExtractor:
    def __init__(self, llm: StructuredAnchorLLM):
        self.llm = llm

    def extract(
        self,
        normalized: NormalizedQuestion,
        deterministic: DeterministicAnchorExtraction,
        semantic: SemanticAnchorExtraction,
        *,
        use_llm: bool,
        fallback_on_error: bool,
    ) -> AmbiguityExtraction:
        if use_llm and self.llm.enabled:
            try:
                result = self.llm.parse(
                    schema=AmbiguityExtraction,
                    schema_name="anchor_ambiguities",
                    instructions=(
                        "Identify only material schema-independent ambiguities in attachment, coreference, scope, "
                        "measure identity, operator meaning, lexical reading, or physical realization. Return only "
                        "the structured object. Use supplied anchor ids. Recommend the grammatically strongest reading "
                        "but preserve alternatives. Do not invent paths and do not call storage alternatives blocking."
                    ),
                    user_input=json.dumps(
                        {
                            "question": normalized.original_text,
                            "deterministic": deterministic.model_dump(mode="json"),
                            "semantic": semantic.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                )
                allowed = {item.anchor_id for item in [*deterministic.anchors, *semantic.anchors]}
                for index, item in enumerate(result.ambiguities, start=1):
                    item.ambiguity_id = f"ambiguity_{index}"
                    item.anchor_ids = [value for value in item.anchor_ids if value in allowed]
                return result
            except Exception as error:
                if not fallback_on_error:
                    raise
                result = self._fallback(normalized, deterministic, semantic)
                result.notes.insert(
                    0,
                    f"Ambiguity model unavailable ({type(error).__name__}); auto mode used deterministic checks.",
                )
                return result
        return self._fallback(normalized, deterministic, semantic)

    def _fallback(
        self,
        normalized: NormalizedQuestion,
        deterministic: DeterministicAnchorExtraction,
        semantic: SemanticAnchorExtraction,
    ) -> AmbiguityExtraction:
        text = normalized.normalized_text
        anchors = [*deterministic.anchors, *semantic.anchors]
        entity_ids = [item.anchor_id for item in anchors if item.kind == AnchorKind.ENTITY]
        ambiguities: list[AnchorAmbiguity] = []

        def add(
            label: str,
            kind: str,
            ids: list[str],
            interpretations: list[str],
            recommendation: str,
            reason: str,
            confidence: float,
        ) -> None:
            ambiguities.append(
                AnchorAmbiguity(
                    ambiguity_id=f"ambiguity_{len(ambiguities) + 1}",
                    text=label,
                    ambiguity_type=kind,  # type: ignore[arg-type]
                    anchor_ids=ids,
                    interpretations=interpretations,
                    recommended_interpretation=recommendation,
                    reason=reason,
                    blocking=False,
                    confidence=confidence,
                )
            )

        relative = re.search(r"\b([A-Za-z][\w-]*)\s+(that|which|who)\s+([^,.]+)", text, re.I)
        if relative:
            add(
                f"Attachment of '{relative.group(2)} {relative.group(3).strip()}'",
                "attachment",
                entity_ids,
                [
                    f"The relative clause modifies {relative.group(1)}.",
                    "The relative clause modifies another nearby entity.",
                ],
                f"Prefer attachment to {relative.group(1)}, the nearest noun phrase.",
                "Changing attachment changes eligibility or grouping.",
                0.86,
            )
        if re.search(r"\b(?:its|their|that\s+(?:entity|group|constructor|team|user))\b", text, re.I):
            add(
                "Reference resolution for a contextual noun phrase",
                "coreference",
                entity_ids,
                ["Resolve to the nearest compatible entity.", "Retain other compatible entity readings."],
                "Prefer the nearest grammatically compatible antecedent.",
                "The reference may change the scope of a returned measure.",
                0.75,
            )
        realization_ids = [
            item.anchor_id
            for item in anchors
            if item.kind == AnchorKind.MEASURE and item.alternatives
        ]
        if realization_ids:
            add(
                "Physical realization of derived measures",
                "realization",
                realization_ids,
                ["Use a stored aggregate.", "Compute from lower-level records."],
                "Carry both realizations into later retrieval.",
                "The question fixes the meaning, not how the database stores it.",
                0.94,
            )
        ranking = any(item.canonical in {"ARGMAX", "ARGMIN"} for item in anchors)
        ties_explicit = any(item.kind == AnchorKind.TIE_POLICY for item in anchors)
        if ranking and not ties_explicit:
            ranking_ids = [item.anchor_id for item in anchors if item.canonical in {"ARGMAX", "ARGMIN"}]
            add(
                "Tie behavior for an extremum",
                "operator",
                ranking_ids,
                ["Return every tied result.", "Return one deterministic representative."],
                "Retain all ties unless the question requests a fixed limit.",
                "Extremum wording alone may not define tie cardinality.",
                0.69,
            )
        return AmbiguityExtraction(
            ambiguities=ambiguities,
            notes=["Uncertainty is explicit and remains available for later evidence-based resolution."],
        )


class AnchorGraphBuilder:
    def build(
        self,
        question: str,
        deterministic: DeterministicAnchorExtraction,
        semantic: SemanticAnchorExtraction,
    ) -> AnchorGraph:
        nodes = [*deterministic.anchors, *semantic.anchors]
        by_id: dict[str, TypedSemanticAnchor] = {}
        warnings: list[str] = []
        for node in nodes:
            if node.anchor_id in by_id:
                warnings.append(f"Duplicate anchor id {node.anchor_id} was dropped.")
                continue
            if node.explicit and node.surface and node.surface.lower() not in question.lower():
                warnings.append(f"Explicit anchor {node.anchor_id} has no exact source text.")
            by_id[node.anchor_id] = node
        edges: list[AnchorRelation] = []
        seen: set[tuple[str, str, str]] = set()
        for relation in [*deterministic.relations, *semantic.relations]:
            if relation.source_anchor_id not in by_id or relation.target_anchor_id not in by_id:
                warnings.append(f"Relation {relation.relation_id} referenced a missing anchor.")
                continue
            key = (relation.source_anchor_id, relation.target_anchor_id, relation.relation_type)
            if key in seen:
                continue
            seen.add(key)
            relation.relation_id = f"edge_{len(edges) + 1}"
            edges.append(relation)
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            adjacency[edge.source_anchor_id].add(edge.target_anchor_id)
            adjacency[edge.target_anchor_id].add(edge.source_anchor_id)
        unseen = set(by_id)
        components: list[list[str]] = []
        while unseen:
            start = min(unseen)
            unseen.remove(start)
            queue = deque([start])
            component: list[str] = []
            while queue:
                current = queue.popleft()
                component.append(current)
                for neighbor in sorted(adjacency[current]):
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        queue.append(neighbor)
            components.append(component)
        roots = [item.anchor_id for item in by_id.values() if item.kind == AnchorKind.OUTPUT]
        if not roots:
            roots = [item.anchor_id for item in by_id.values() if item.kind == AnchorKind.ENTITY]
        if not any(item.kind == AnchorKind.ENTITY for item in by_id.values()):
            warnings.append("No entity anchor was extracted.")
        if not any(item.kind == AnchorKind.OUTPUT for item in by_id.values()):
            warnings.append("No explicit output anchor was extracted.")
        return AnchorGraph(
            nodes=list(by_id.values()),
            edges=edges,
            root_anchor_ids=roots,
            connected_components=components,
            validation_warnings=warnings,
        )


class RetrievalSpecificationBuilder:
    def build(self, graph: AnchorGraph) -> RetrievalSpecificationSet:
        by_id = {item.anchor_id: item for item in graph.nodes}
        neighbors: dict[str, list[tuple[TypedSemanticAnchor, AnchorRelation]]] = defaultdict(list)
        for edge in graph.edges:
            left, right = by_id[edge.source_anchor_id], by_id[edge.target_anchor_id]
            neighbors[left.anchor_id].append((right, edge))
            neighbors[right.anchor_id].append((left, edge))
        specifications: list[RetrievalSpecification] = []
        for anchor in graph.nodes:
            related = neighbors[anchor.anchor_id]
            search_kind = self._search_kind(anchor)
            terms: list[str] = []
            for text in [anchor.surface, anchor.canonical, *anchor.alternatives, *(item.canonical for item, _ in related)]:
                for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text.lower()):
                    if token not in terms and token not in {"the", "a", "an", "of", "for", "that", "and"}:
                        terms.append(token)
            structural = [
                f"{edge.relation_type}: {item.canonical}"
                for item, edge in related
                if edge.relation_type in {"related_to", "same_scope", "grouped_by", "partitioned_by", "filters"}
            ]
            context = "; ".join(item.description for item, _ in related[:4])
            specifications.append(
                RetrievalSpecification(
                    specification_id=f"spec_{len(specifications) + 1}",
                    anchor_ids=[anchor.anchor_id, *(item.anchor_id for item, _ in related)],
                    search_kind=search_kind,
                    query_terms=terms[:20],
                    semantic_query=(
                        f"{anchor.description} Semantic role: {anchor.semantic_role}. Related context: {context}"
                    ).strip(),
                    expected_bson_types=anchor.expected_bson_types,
                    structural_constraints=structural,
                    required=anchor.retrieval_required and search_kind != "none",
                    rationale=self._rationale(search_kind),
                )
            )
        return RetrievalSpecificationSet(
            specifications=specifications,
            notes=[
                "Specifications describe future index requests; this run executed no schema retrieval.",
                "Related path, value, and group concepts remain bundled to preserve scope.",
            ],
        )

    @staticmethod
    def _search_kind(anchor: TypedSemanticAnchor) -> str:
        if anchor.kind == AnchorKind.STORED_LITERAL:
            return "value_path_group"
        if anchor.kind == AnchorKind.RELATIONSHIP:
            return "relationship"
        if anchor.kind == AnchorKind.TEMPORAL:
            return "type_compatible_path"
        if anchor.kind in {AnchorKind.ENTITY, AnchorKind.ATTRIBUTE, AnchorKind.MEASURE, AnchorKind.OUTPUT}:
            return "path"
        return "none"

    @staticmethod
    def _rationale(search_kind: str) -> str:
        return {
            "value_path_group": "Retrieve a stored value together with its containing path and schema group.",
            "relationship": "Retrieve structural evidence capable of realizing the relationship.",
            "type_compatible_path": "Retrieve paths whose observed type can represent the temporal concept.",
            "path": "Retrieve paths using anchor meaning, expected types, and neighboring scope.",
            "none": "This control anchor constrains later scoring without an independent retrieval request.",
        }[search_kind]


class AnchorBundleBuilder:
    def build(
        self,
        question: str,
        normalized: NormalizedQuestion,
        graph: AnchorGraph,
        ambiguities: AmbiguityExtraction,
        specifications: RetrievalSpecificationSet,
    ) -> AnchorBundle:
        categories = [
            any(item.kind in {AnchorKind.ENTITY, AnchorKind.ATTRIBUTE, AnchorKind.MEASURE} for item in graph.nodes),
            any(item.kind == AnchorKind.OUTPUT for item in graph.nodes),
            any(item.kind in {AnchorKind.OPERATION, AnchorKind.COMPARISON, AnchorKind.GROUPING, AnchorKind.SORT} for item in graph.nodes),
            bool(graph.edges),
            any(item.required for item in specifications.specifications),
        ]
        weights = [0.3, 0.2, 0.2, 0.15, 0.15]
        coverage = round(sum(weight for present, weight in zip(categories, weights, strict=True) if present), 3)
        warnings = list(graph.validation_warnings)
        if any(item.blocking for item in ambiguities.ambiguities):
            warnings.append("At least one blocking semantic ambiguity remains unresolved.")
        return AnchorBundle(
            question=question,
            normalized_question=normalized,
            anchors=graph.nodes,
            relations=graph.edges,
            ambiguities=ambiguities.ambiguities,
            retrieval_specifications=specifications.specifications,
            coverage_score=coverage,
            ready_for_retrieval=coverage >= 0.75 and not any("No entity" in item for item in warnings),
            validation_warnings=warnings,
        )


class AnchorExtractionPipeline:
    def __init__(self, settings: Settings, store: AnchorRunStore):
        self.settings = settings
        self.store = store
        self.llm = StructuredAnchorLLM(settings)
        self.normalizer = QuestionNormalizer()
        self.deterministic = DeterministicAnchorExtractor()
        self.semantic = SemanticAnchorExtractor(self.llm)
        self.ambiguity = AmbiguityExtractor(self.llm)
        self.graph = AnchorGraphBuilder()
        self.specifications = RetrievalSpecificationBuilder()
        self.bundle = AnchorBundleBuilder()
        self._locks: dict[str, threading.Lock] = {}

    def create_run(self, request: AnchorRunCreate) -> AnchorRunView:
        run = AnchorRunView(
            run_id=uuid.uuid4().hex,
            question=request.question.strip(),
            mode=request.mode,
            locale=request.locale,
            timezone=request.timezone,
            status=AnchorRunStatus.CREATED,
            model_id=self.settings.model,
            stages=[AnchorStageTrace(stage=name, status="pending") for name in ANCHOR_STAGE_NAMES],
        )
        self.store.save(run)
        self._locks[run.run_id] = threading.Lock()
        return run

    def run(self, run_id: str) -> None:
        lock = self._locks.setdefault(run_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return
        try:
            run = self._require(run_id)
            run.status = AnchorRunStatus.RUNNING
            self.store.save(run)
            use_llm = run.mode == AnchorRunMode.LLM or (
                run.mode == AnchorRunMode.AUTO and self.llm.enabled
            )
            fallback = run.mode == AnchorRunMode.AUTO
            if run.mode == AnchorRunMode.LLM and not self.llm.enabled:
                raise RuntimeError("GPT semantic mode requires a configured, non-stub provider")

            normalized = self._stage(
                run,
                "normalization",
                lambda: self.normalizer.normalize(
                    run.question, locale=run.locale, timezone=run.timezone
                ),
                lambda value: f"{len(value.clauses)} clauses, {len(value.scalars)} scalars, {len(value.cues)} cues",
            )
            run.normalized_question = normalized
            deterministic = self._stage(
                run,
                "deterministic_extraction",
                lambda: self.deterministic.extract(normalized),
                lambda value: f"{len(value.anchors)} deterministic anchors and {len(value.relations)} relations",
            )
            run.deterministic_extraction = deterministic
            semantic = self._stage(
                run,
                "semantic_extraction",
                lambda: self.semantic.extract(
                    normalized,
                    deterministic,
                    use_llm=use_llm,
                    fallback_on_error=fallback,
                ),
                lambda value: f"{len(value.anchors)} semantic anchors and {len(value.relations)} relations",
            )
            run.semantic_extraction = semantic
            ambiguity = self._stage(
                run,
                "ambiguity_extraction",
                lambda: self.ambiguity.extract(
                    normalized,
                    deterministic,
                    semantic,
                    use_llm=use_llm,
                    fallback_on_error=fallback,
                ),
                lambda value: f"{len(value.ambiguities)} ambiguity candidates retained",
            )
            run.ambiguity_extraction = ambiguity
            graph = self._stage(
                run,
                "anchor_graph",
                lambda: self.graph.build(run.question, deterministic, semantic),
                lambda value: f"{len(value.nodes)} nodes and {len(value.edges)} edges",
            )
            run.anchor_graph = graph
            specifications = self._stage(
                run,
                "retrieval_specifications",
                lambda: self.specifications.build(graph),
                lambda value: f"{sum(item.required for item in value.specifications)} future retrieval requests",
            )
            run.retrieval_specifications = specifications
            bundle = self._stage(
                run,
                "anchor_bundle",
                lambda: self.bundle.build(
                    run.question, normalized, graph, ambiguity, specifications
                ),
                lambda value: f"AnchorBundle coverage {value.coverage_score:.0%}",
            )
            run.anchor_bundle = bundle
            run.status = AnchorRunStatus.COMPLETED
            self.store.save(run)
        except Exception as error:
            current = self.store.get(run_id)
            if current:
                current.status = AnchorRunStatus.FAILED
                current.failure = f"{type(error).__name__}: {error}"
                active = next((item for item in current.stages if item.status == "running"), None)
                if active:
                    active.status = "failed"
                    active.error = current.failure
                    active.completed_at = anchor_now()
                self.store.save(current)
        finally:
            lock.release()

    def _stage(
        self,
        run: AnchorRunView,
        stage_name: str,
        operation: Callable[[], Any],
        summarize: Callable[[Any], str],
    ) -> Any:
        trace = next(item for item in run.stages if item.stage == stage_name)
        trace.status = "running"
        trace.started_at = anchor_now()
        self.store.save(run)
        started = time.perf_counter()
        try:
            value = operation()
            trace.status = "completed"
            trace.summary = summarize(value)
            trace.artifact = value.model_dump(mode="json")
            return value
        except Exception as error:
            trace.status = "failed"
            trace.error = f"{type(error).__name__}: {error}"
            raise
        finally:
            trace.completed_at = anchor_now()
            trace.duration_ms = round((time.perf_counter() - started) * 1000, 3)
            self.store.save(run)

    def _require(self, run_id: str) -> AnchorRunView:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run
