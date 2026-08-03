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
    DeferredPlanCue,
    NormalizationCue,
    NormalizedClause,
    NormalizedQuestion,
    NormalizedScalar,
    RestrictionBinding,
    RetrievalRestriction,
    RetrievalSpecification,
    RetrievalSpecificationSet,
    SupportInference,
    TargetExtraction,
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
    CueRule(r"\b(?:highest[- ]scoring|top[- ]scoring|highest|largest|most)\b", "ranking", "ARGMAX", "morphology"),
    CueRule(r"\b(?:lowest[- ]scoring|lowest|smallest|least)\b", "ranking", "ARGMIN", "morphology"),
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
    """Strict structured-output adapter used only for retrieval-concept enrichment."""

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
                    "schema": self._strict_schema(schema.model_json_schema()),
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
        return NormalizedQuestion(
            original_text=original,
            normalized_text=normalized,
            locale=locale,
            timezone=timezone,
            clauses=clauses,
            scalars=scalars,
            cues=cues,
            semantic_spans=self._semantic_spans(normalized),
            notes=[
                "Normalization preserves the user's wording while exposing values and retrieval-relevant cues.",
                "Operations are evidence for restrictions or supporting fields; they never become graph nodes.",
                "Sorting, tie handling, and presentation cues are deferred beyond schema retrieval.",
            ],
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
            if any(start < right and end > left for left, right, _, _ in selected):
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


def singular(value: str) -> str:
    if value.endswith("ies"):
        return value[:-3] + "y"
    if value.endswith("s") and not value.endswith(("ss", "us")):
        return value[:-1]
    return value


def tokens(value: str) -> set[str]:
    normalized: set[str] = set()
    replacements = {
        "won": "win",
        "winner": "win",
        "winning": "win",
        "scoring": "score",
        "scored": "score",
    }
    stop = {"the", "a", "an", "of", "for", "that", "and", "to", "by", "each", "all"}
    for token in re.findall(r"[a-z0-9]+", value.lower()):
        if token in stop:
            continue
        normalized.add(replacements.get(token, singular(token)))
    return normalized


def expected_types(value: str) -> list[str]:
    lowered = value.lower()
    if any(token in lowered for token in ("name", "title", "label", "type", "status", "category")):
        return ["string"]
    if any(token in lowered for token in ("count", "number", "total", "points", "score", "amount", "price", "position", "rank")):
        return ["int", "long", "double", "decimal"]
    if any(token in lowered for token in ("date", "time", "year", "season")):
        return ["date", "string", "int", "long"]
    return []


def aliases_for(value: str) -> list[str]:
    lowered = value.lower()
    aliases: list[str] = []
    if "name" in lowered:
        aliases.extend(["name", "full_name", "display name"])
    if any(token in lowered for token in ("points", "score", "scoring")):
        aliases.extend(["points", "score", "total points"])
    if "podium" in lowered:
        aliases.extend(["podium", "finishing position", "position", "rank"])
    if any(token in lowered for token in ("win", "won", "winner")):
        aliases.extend(["winner", "is_winner", "position", "finishing position"])
    if any(token in lowered for token in ("season", "year")):
        aliases.extend(["season", "year", "season_year"])
    return list(dict.fromkeys(aliases))


class RetrievalTargetExtractor:
    """Extract only concepts that can become schema or path retrieval targets."""

    ENTITY_STOPWORDS = {
        "result", "results", "least", "most", "minimum", "maximum", "total", "number",
        "one", "all", "each", "every", "season", "year", "name", "points", "score",
        "for", "in", "on", "with", "from", "by", "than", "return", "show", "list",
    }

    def extract(self, normalized: NormalizedQuestion) -> TargetExtraction:
        text = normalized.normalized_text
        anchors: list[TypedSemanticAnchor] = []
        relations: list[AnchorRelation] = []
        by_key: dict[tuple[AnchorKind, str], TypedSemanticAnchor] = {}

        def add(
            kind: AnchorKind,
            surface: str,
            canonical: str,
            description: str,
            *,
            role: str = "primary",
            output_requested: bool = False,
            parent_hints: list[str] | None = None,
            derivation_hints: list[str] | None = None,
            start: int | None = None,
            end: int | None = None,
            confidence: float = 0.86,
        ) -> TypedSemanticAnchor:
            canonical = re.sub(r"\s+", " ", canonical.strip().lower())
            key = (kind, canonical)
            existing = by_key.get(key)
            if existing:
                existing.output_requested = existing.output_requested or output_requested
                existing.parent_hints = list(dict.fromkeys([*existing.parent_hints, *(parent_hints or [])]))
                existing.derivation_hints = list(
                    dict.fromkeys([*existing.derivation_hints, *(derivation_hints or [])])
                )
                existing.confidence = max(existing.confidence, confidence)
                return existing
            anchor = TypedSemanticAnchor(
                anchor_id=f"t{len(anchors) + 1}",
                kind=kind,
                surface=surface,
                canonical=canonical,
                description=description,
                retrieval_role=role,  # type: ignore[arg-type]
                expected_bson_types=expected_types(canonical),
                aliases=aliases_for(canonical),
                parent_hints=parent_hints or [],
                output_requested=output_requested,
                derivation_hints=derivation_hints or [],
                explicit=True,
                source=AnchorSource.RULE,
                start=start,
                end=end,
                confidence=confidence,
            )
            anchors.append(anchor)
            by_key[key] = anchor
            return anchor

        entity_patterns = [
            (r"\b(?:for each|per|for every)\s+([A-Za-z][\w-]*)", "grouping context"),
            (r"\b(?:highest[- ]scoring|lowest[- ]scoring|top[- ]scoring|highest|lowest)\s+([A-Za-z][\w-]*)", "ranked result entity"),
            (r"\b([A-Za-z][\w-]*)\s+(?:that|which|who)\s+", "constrained entity"),
            (r"\b([A-Za-z][\w-]*)['’]s\s+(?:full\s+)?(?:name|title|id|score|status)", "field owner"),
        ]
        entities: list[TypedSemanticAnchor] = []
        for pattern, evidence in entity_patterns:
            for match in re.finditer(pattern, text, re.I):
                surface = match.group(1)
                canonical = singular(surface.lower())
                if canonical in self.ENTITY_STOPWORDS:
                    continue
                entities.append(
                    add(
                        AnchorKind.ENTITY,
                        surface,
                        canonical,
                        f"Schema entity or containing object indicated by the {evidence}.",
                        start=match.start(1),
                        end=match.end(1),
                        confidence=0.9 if evidence == "field owner" else 0.86,
                    )
                )

        output_parts: list[tuple[str, int]] = []
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
                output_parts.append((surface, start))

        for surface, start in output_parts:
            lowered = re.sub(r"^(?:the|a|an)\s+", "", surface.lower())
            owner_match = re.match(r"([a-z][\w-]*)['’]s\s+(.+)", lowered)
            parent_hints: list[str] = []
            if owner_match:
                owner = singular(owner_match.group(1))
                parent_hints.append(owner)
                lowered = f"{owner} {owner_match.group(2)}"
                entities.append(
                    add(
                        AnchorKind.ENTITY,
                        owner_match.group(1),
                        owner,
                        "Entity owning an explicitly requested field.",
                        start=start,
                        end=start + len(owner_match.group(1)),
                        confidence=0.92,
                    )
                )
            for entity in entities:
                if entity.canonical in lowered and entity.canonical not in parent_hints:
                    parent_hints.append(entity.canonical)
            contextual_parent = re.search(r"\bfor\s+(?:that|the|each)\s+([a-z][\w-]*)", lowered)
            if contextual_parent:
                parent_hints.append(singular(contextual_parent.group(1)))

            derived = bool(re.search(r"\b(?:number of|count of)\b", lowered)) or "podium" in lowered
            canonical = re.sub(r"^(?:total|sum of|number of|count of|average)\s+", "", lowered)
            canonical = re.sub(r"\s+earned\s+for\s+.+$", "", canonical)
            canonical = re.sub(r"\s+", " ", canonical).strip()
            derivation_hints: list[str] = []
            if re.search(r"\b(?:total|sum of)\b", lowered):
                derivation_hints.append("May be stored directly or summed from lower-level records.")
            if re.search(r"\b(?:number of|count of)\b", lowered):
                derivation_hints.append("Requires countable records or a stored count field.")
            if "podium" in lowered:
                derivation_hints.append("Can be derived from finishing position when no podium field exists.")
            target = add(
                AnchorKind.DERIVED_CONCEPT if derived else AnchorKind.FIELD,
                surface,
                canonical,
                "Requested result concept to ground to a schema path or supporting evidence.",
                output_requested=True,
                parent_hints=list(dict.fromkeys(parent_hints)),
                derivation_hints=derivation_hints,
                start=start,
                end=start + len(surface),
                confidence=0.94,
            )

            for entity in entities:
                if entity.canonical in target.parent_hints:
                    relations.append(
                        AnchorRelation(
                            relation_id=f"tr{len(relations) + 1}",
                            source_anchor_id=target.anchor_id,
                            target_anchor_id=entity.anchor_id,
                            relation_type="belongs_to",
                            description=f"{target.canonical} should be retrieved in {entity.canonical} context.",
                            confidence=0.9,
                            source=AnchorSource.RULE,
                        )
                    )

        deferred: list[DeferredPlanCue] = []
        for cue in normalized.cues:
            kind = "sorting" if cue.category == "sorting" else (
                "tie_policy" if cue.category == "tie_policy" else None
            )
            if kind:
                deferred.append(
                    DeferredPlanCue(
                        cue_id=f"deferred_{len(deferred) + 1}",
                        surface=cue.surface,
                        kind=kind,  # type: ignore[arg-type]
                        canonical=cue.canonical,
                        reason="This affects final query execution, not which schema paths should be retrieved.",
                        start=cue.start,
                        end=cue.end,
                    )
                )

        covered = " ".join(anchor.surface.lower() for anchor in anchors)
        unresolved = [span for span in normalized.semantic_spans if span.lower() not in covered]
        return TargetExtraction(
            anchors=anchors,
            relations=self._dedupe_relations(relations),
            unresolved_phrases=unresolved,
            deferred_plan_cues=deferred,
            notes=[
                "Only entities, requested fields, and requested derived concepts become retrieval targets.",
                "Operators and constants are intentionally excluded from the target set.",
            ],
        )

    @staticmethod
    def _dedupe_relations(relations: list[AnchorRelation]) -> list[AnchorRelation]:
        seen: set[tuple[str, str, str]] = set()
        result: list[AnchorRelation] = []
        for relation in relations:
            key = (relation.source_anchor_id, relation.target_anchor_id, relation.relation_type)
            if key in seen:
                continue
            seen.add(key)
            relation.relation_id = f"tr{len(result) + 1}"
            result.append(relation)
        return result


class SupportingFieldInferer:
    def __init__(self, llm: StructuredAnchorLLM):
        self.llm = llm

    def infer(
        self,
        normalized: NormalizedQuestion,
        targets: TargetExtraction,
        *,
        use_llm: bool,
        fallback_on_error: bool,
    ) -> SupportInference:
        if use_llm and self.llm.enabled:
            try:
                result = self.llm.parse(
                    schema=SupportInference,
                    schema_name="retrieval_support_concepts",
                    instructions=(
                        "Infer only schema-retrieval concepts. Add missing entities or requested fields when the rule "
                        "pass missed them, and add supporting fields needed to filter or derive requested results. "
                        "Allowed node kinds are entity, field, and derived_concept. Never create operation, grouping, "
                        "comparison, sorting, aggregation, constant, temporal-value, output-slot, or query-plan nodes. "
                        "Operations may only justify a supporting field: for example, 'won' suggests winner/position; "
                        "'podium finishes' suggests finishing position; a year suggests a season/year field. Connect "
                        "concepts only with belongs_to, requires_connection, scoped_with, supports, or derived_from. "
                        "Use supplied t-ids when linking existing targets and source='llm'. Return the strict object."
                    ),
                    user_input=json.dumps(
                        {
                            "question": normalized.original_text,
                            "normalized": normalized.model_dump(mode="json"),
                            "retrieval_targets": targets.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                )
                return self._sanitize(result, normalized.original_text, targets)
            except Exception as error:
                if not fallback_on_error:
                    raise
                result = self._fallback(normalized, targets)
                result.notes.insert(
                    0,
                    f"Semantic model unavailable ({type(error).__name__}); auto mode used deterministic support inference.",
                )
                return result
        return self._fallback(normalized, targets)

    def _sanitize(
        self,
        result: SupportInference,
        question: str,
        targets: TargetExtraction,
    ) -> SupportInference:
        existing = {(item.kind, item.canonical.lower()): item.anchor_id for item in targets.anchors}
        remap: dict[str, str] = {}
        clean_anchors: list[TypedSemanticAnchor] = []
        for anchor in result.anchors:
            original_id = anchor.anchor_id
            duplicate = existing.get((anchor.kind, anchor.canonical.lower()))
            if duplicate:
                remap[original_id] = duplicate
                continue
            anchor.anchor_id = f"s{len(clean_anchors) + 1}"
            remap[original_id] = anchor.anchor_id
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
            clean_anchors.append(anchor)
            existing[(anchor.kind, anchor.canonical.lower())] = anchor.anchor_id

        allowed = {item.anchor_id for item in targets.anchors} | {
            item.anchor_id for item in clean_anchors
        }
        clean_relations: list[AnchorRelation] = []
        seen: set[tuple[str, str, str]] = set()
        for relation in result.relations:
            source = remap.get(relation.source_anchor_id, relation.source_anchor_id)
            target = remap.get(relation.target_anchor_id, relation.target_anchor_id)
            key = (source, target, relation.relation_type)
            if source == target or source not in allowed or target not in allowed or key in seen:
                continue
            seen.add(key)
            relation.relation_id = f"sr{len(clean_relations) + 1}"
            relation.source_anchor_id = source
            relation.target_anchor_id = target
            relation.source = AnchorSource.LLM
            clean_relations.append(relation)
        result.anchors = clean_anchors
        result.relations = clean_relations
        result.notes.append("Plan-like nodes returned by the model are impossible under the closed target schema.")
        return result

    def _fallback(
        self,
        normalized: NormalizedQuestion,
        targets: TargetExtraction,
    ) -> SupportInference:
        text = normalized.normalized_text
        anchors: list[TypedSemanticAnchor] = []
        relations: list[AnchorRelation] = []
        base = list(targets.anchors)
        by_key = {(item.kind, item.canonical): item for item in base}

        def add(
            kind: AnchorKind,
            surface: str,
            canonical: str,
            description: str,
            *,
            parent_hints: list[str] | None = None,
            types: list[str] | None = None,
            aliases: list[str] | None = None,
            derivation_hints: list[str] | None = None,
            explicit: bool = True,
            confidence: float = 0.82,
        ) -> TypedSemanticAnchor:
            key = (kind, canonical)
            if key in by_key:
                return by_key[key]
            start = text.lower().find(surface.lower()) if explicit else -1
            anchor = TypedSemanticAnchor(
                anchor_id=f"s{len(anchors) + 1}",
                kind=kind,
                surface=surface,
                canonical=canonical,
                description=description,
                retrieval_role="supporting",
                expected_bson_types=types if types is not None else expected_types(canonical),
                aliases=list(dict.fromkeys(aliases if aliases is not None else aliases_for(canonical))),
                parent_hints=parent_hints or [],
                output_requested=False,
                derivation_hints=derivation_hints or [],
                explicit=explicit and start >= 0,
                source=AnchorSource.INFERRED,
                start=start if explicit and start >= 0 else None,
                end=start + len(surface) if explicit and start >= 0 else None,
                confidence=confidence,
            )
            anchors.append(anchor)
            by_key[key] = anchor
            return anchor

        def relate(source: TypedSemanticAnchor, target: TypedSemanticAnchor, kind: str, description: str, confidence: float = 0.82) -> None:
            key = (source.anchor_id, target.anchor_id, kind)
            if source.anchor_id == target.anchor_id or any(
                (item.source_anchor_id, item.target_anchor_id, item.relation_type) == key
                for item in [*targets.relations, *relations]
            ):
                return
            relations.append(
                AnchorRelation(
                    relation_id=f"sr{len(relations) + 1}",
                    source_anchor_id=source.anchor_id,
                    target_anchor_id=target.anchor_id,
                    relation_type=kind,  # type: ignore[arg-type]
                    description=description,
                    confidence=confidence,
                    source=AnchorSource.INFERRED,
                )
            )

        entity_by_name = {
            item.canonical: item for item in base if item.kind == AnchorKind.ENTITY
        }
        if re.search(r"\brace(?:s)?\b", text, re.I):
            race = add(
                AnchorKind.ENTITY,
                "race",
                "race",
                "Event entity required by the eligibility condition.",
                confidence=0.86,
            )
            entity_by_name["race"] = race

        season_field: TypedSemanticAnchor | None = None
        if any(item.scalar_type in {"year", "date"} for item in normalized.scalars) or re.search(r"\bseason\b", text, re.I):
            season_field = add(
                AnchorKind.FIELD,
                "season" if "season" in text.lower() else "year",
                "season year",
                "Field needed to attach the temporal value to stored records.",
                parent_hints=["race"],
                types=["date", "string", "int", "long"],
                aliases=["season", "year", "season_year"],
                confidence=0.9,
            )
            if "race" in entity_by_name:
                relate(season_field, entity_by_name["race"], "belongs_to", "Season/year is expected in race or event context.")

        outcome: TypedSemanticAnchor | None = None
        if re.search(r"\b(?:won|win|winner|victory)\b", text, re.I):
            outcome = add(
                AnchorKind.FIELD,
                "won" if "won" in text.lower() else "winner",
                "race outcome",
                "Supporting field needed to determine whether an entity won an event.",
                parent_hints=["race", "race result"],
                types=["bool", "int", "long", "string"],
                aliases=["winner", "is_winner", "winning position", "finishing position", "position"],
                derivation_hints=["May be a winner flag or be inferred from finishing position equal to one."],
                confidence=0.9,
            )
            if "race" in entity_by_name:
                relate(outcome, entity_by_name["race"], "belongs_to", "Race outcome belongs to an event or result record.")

        podium_targets = [item for item in base if "podium" in item.canonical]
        position: TypedSemanticAnchor | None = None
        if podium_targets:
            position = add(
                AnchorKind.FIELD,
                "podium finishes",
                "finishing position",
                "Supporting field from which podium membership can be derived.",
                parent_hints=["race", "race result", "driver"],
                types=["int", "long"],
                aliases=["position", "rank", "finish_position", "finishing order"],
                derivation_hints=["A podium finish is commonly represented by position less than or equal to three."],
                confidence=0.91,
            )
            for target in podium_targets:
                relate(position, target, "supports", "Finishing position supplies evidence for the requested podium concept.", 0.94)
            if "race" in entity_by_name:
                relate(position, entity_by_name["race"], "belongs_to", "Finishing position is recorded per race result.")

        all_targets = [*base, *anchors]
        entities = [item for item in all_targets if item.kind == AnchorKind.ENTITY]
        for item in all_targets:
            if item.kind == AnchorKind.ENTITY:
                continue
            for parent in item.parent_hints:
                parent_tokens = tokens(parent)
                candidates = [entity for entity in entities if tokens(entity.canonical) & parent_tokens]
                if candidates:
                    relate(item, candidates[0], "belongs_to", f"{item.canonical} is retrieved under {candidates[0].canonical} context.")

        driver = next((item for item in entities if item.canonical == "driver"), None)
        constructor = next((item for item in entities if item.canonical == "constructor"), None)
        race = next((item for item in entities if item.canonical == "race"), None)
        if driver and constructor:
            relate(driver, constructor, "requires_connection", "The question requires driver evidence scoped to a constructor.", 0.92)
        if constructor and race and outcome:
            relate(constructor, race, "requires_connection", "Winning eligibility connects the constructor to race results.", 0.9)
        if driver and race and (position or outcome):
            relate(driver, race, "requires_connection", "Driver measures require race-result evidence.", 0.86)
        if season_field and constructor:
            relate(season_field, constructor, "scoped_with", "The temporal scope also constrains constructor results.", 0.76)

        return SupportInference(
            anchors=anchors,
            relations=relations,
            notes=[
                "Supporting fields are inferred only when a filter or requested derived concept needs schema evidence.",
                "No operation, comparison, grouping, sorting, or constant node is produced.",
            ],
        )


class RestrictionBinder:
    """Turn values and operation language into metadata on retrieval targets."""

    def bind(
        self,
        normalized: NormalizedQuestion,
        targets: TargetExtraction,
        support: SupportInference,
    ) -> RestrictionBinding:
        anchors = [*targets.anchors, *support.anchors]
        restrictions: list[RetrievalRestriction] = []

        def add(
            kind: str,
            surface: str,
            canonical: str,
            description: str,
            anchor_ids: list[str],
            effect: str,
            *,
            operator: str | None = None,
            value: str | int | float | bool | None = None,
            start: int | None = None,
            end: int | None = None,
            confidence: float = 0.88,
        ) -> RetrievalRestriction:
            restriction = RetrievalRestriction(
                restriction_id=f"restriction_{len(restrictions) + 1}",
                kind=kind,  # type: ignore[arg-type]
                surface=surface,
                canonical=canonical,
                description=description,
                anchor_ids=list(dict.fromkeys(anchor_ids)),
                operator=operator,
                normalized_value=value,
                retrieval_effect=effect,  # type: ignore[arg-type]
                source=AnchorSource.RULE,
                start=start,
                end=end,
                confidence=confidence,
            )
            restrictions.append(restriction)
            return restriction

        comparison_cues = [cue for cue in normalized.cues if cue.category == "comparison"]
        consumed_scalars: set[str] = set()
        for cue in comparison_cues:
            nearby = min(
                normalized.scalars,
                key=lambda scalar: abs(scalar.start - cue.end),
                default=None,
            )
            if nearby is None or abs(nearby.start - cue.end) > 24:
                continue
            consumed_scalars.add(nearby.scalar_id)
            local = normalized.normalized_text[max(0, cue.start - 28) : min(len(normalized.normalized_text), nearby.end + 28)]
            cardinality = nearby.normalized in {0, 1} and bool(
                re.search(r"\b(?:race|event|record|item|result|time)\b", local, re.I)
            )
            if cardinality:
                evidence = [
                    item for item in anchors
                    if tokens(" ".join([item.canonical, *item.aliases]))
                    & {"win", "race", "event", "outcome", "position", "result"}
                ]
                relevant = self._best_targets(local, evidence or anchors, limit=3)
            else:
                relevant = self._best_targets(local, anchors, limit=3)
            add(
                "cardinality" if cardinality else "comparison",
                normalized.normalized_text[cue.start : nearby.end],
                "minimum matching records" if cardinality and cue.canonical == "GTE" else cue.canonical.lower(),
                "Comparison is retained as a restriction on target evidence, not as a graph node.",
                [item.anchor_id for item in relevant],
                "support_requirement" if cardinality else "filter_value",
                operator=cue.canonical,
                value=nearby.normalized,
                start=cue.start,
                end=nearby.end,
                confidence=0.96,
            )

        for scalar in normalized.scalars:
            if scalar.scalar_id in consumed_scalars:
                continue
            temporal = scalar.scalar_type in {"year", "date"} or any(
                word in scalar.context for word in ("season", "year", "date", "month")
            )
            if temporal:
                temporal_targets = [
                    item for item in anchors
                    if tokens(item.canonical) & {"season", "year", "date", "time"}
                ]
                temporal_context = [
                    item for item in anchors
                    if item.kind == AnchorKind.ENTITY and tokens(item.canonical) & {"race", "event", "season"}
                ]
                relevant = [*temporal_targets[:1], *temporal_context[:1]]
                if not relevant:
                    relevant = self._best_targets("season year date time", anchors, limit=2)
                kind, canonical, effect = "temporal", "temporal scope", "filter_value"
            else:
                relevant = self._best_targets(scalar.context, anchors, limit=3)
                kind, canonical, effect = "value", "query value", "filter_value"
            add(
                kind,
                scalar.surface,
                canonical,
                f"The explicit {scalar.scalar_type} value restricts matching records.",
                [item.anchor_id for item in relevant],
                effect,
                operator="EQ",
                value=scalar.normalized,
                start=scalar.start,
                end=scalar.end,
                confidence=0.98,
            )

        for cue in normalized.cues:
            local = cue.scope_hint
            if cue.category == "ranking":
                score_targets = [
                    item for item in anchors
                    if tokens(" ".join([item.canonical, *item.aliases])) & {"score", "point"}
                ]
                numeric_targets = [
                    item for item in anchors
                    if item.expected_bson_types and any(value in item.expected_bson_types for value in ("int", "long", "double", "decimal"))
                ]
                relevant = self._best_targets(local, score_targets or numeric_targets or anchors, limit=2)
                add(
                    "role_hint",
                    cue.surface,
                    "ranking measure",
                    "Ranking language strengthens numeric score/measure paths during retrieval.",
                    [item.anchor_id for item in relevant],
                    "role_hint",
                    operator=cue.canonical,
                    start=cue.start,
                    end=cue.end,
                    confidence=0.88,
                )
            elif cue.category == "aggregation":
                preferred = [
                    item for item in anchors
                    if item.kind == AnchorKind.DERIVED_CONCEPT
                    or any(value in item.expected_bson_types for value in ("int", "long", "double", "decimal"))
                ]
                relevant = self._best_targets(
                    normalized.normalized_text[cue.start : min(len(normalized.normalized_text), cue.end + 42)],
                    preferred or anchors,
                    limit=2 if cue.canonical == "COUNT" else 1,
                )
                add(
                    "role_hint",
                    cue.surface,
                    "derived measure evidence",
                    "Aggregation wording identifies the target's semantic role and possible supporting records.",
                    [item.anchor_id for item in relevant],
                    "role_hint",
                    operator=cue.canonical,
                    start=cue.start,
                    end=cue.end,
                    confidence=0.9,
                )
            elif cue.category == "grouping":
                entities = [item for item in anchors if item.kind == AnchorKind.ENTITY]
                following = re.match(r"\s+([A-Za-z][\w-]*)", normalized.normalized_text[cue.end :])
                grouped_name = singular(following.group(1).lower()) if following else ""
                exact = [item for item in entities if item.canonical == grouped_name]
                relevant = exact or self._best_targets(local, entities, limit=1)
                add(
                    "scope",
                    cue.surface,
                    "entity scope",
                    "Grouping wording makes the nearby entity a structural context for path retrieval.",
                    [item.anchor_id for item in relevant],
                    "relation_scope",
                    operator=cue.canonical,
                    start=cue.start,
                    end=cue.end,
                    confidence=0.92,
                )

        for match in re.finditer(r"\b([A-Za-z][\w-]*)\s+(?:that|which|who)\s+([^,.]+)", normalized.normalized_text, re.I):
            phrase = match.group(0)
            if not re.search(r"\b(?:won|win|has|have|with|without|contains?|includes?)\b", phrase, re.I):
                continue
            subject = singular(match.group(1).lower())
            subject_targets = [
                item for item in anchors
                if item.kind == AnchorKind.ENTITY and item.canonical == subject
            ]
            evidence = [
                item for item in anchors
                if tokens(" ".join([item.canonical, *item.aliases]))
                & {"win", "race", "event", "outcome", "position", "result"}
            ]
            relevant = [*subject_targets, *self._best_targets(phrase, evidence, limit=3)]
            add(
                "eligibility",
                phrase,
                "eligibility evidence",
                "The relative clause restricts eligible entities and identifies supporting relationship evidence.",
                [item.anchor_id for item in relevant],
                "support_requirement",
                operator="EXISTS",
                start=match.start(),
                end=match.end(),
                confidence=0.91,
            )

        return RestrictionBinding(
            restrictions=self._dedupe(restrictions),
            deferred_plan_cues=targets.deferred_plan_cues,
            notes=[
                "Temporal values, constants, comparisons, aggregation, and grouping are attached as target metadata.",
                "Sorting and tie policy are preserved for later planning but excluded from schema retrieval.",
            ],
        )

    @staticmethod
    def _best_targets(text: str, anchors: list[TypedSemanticAnchor], *, limit: int) -> list[TypedSemanticAnchor]:
        query = tokens(text)
        scored: list[tuple[int, int, TypedSemanticAnchor]] = []
        for anchor in anchors:
            vocabulary = tokens(
                " ".join([anchor.canonical, anchor.surface, *anchor.aliases, *anchor.parent_hints])
            )
            overlap = len(query & vocabulary)
            role_bonus = 1 if anchor.retrieval_role == "supporting" else 0
            if overlap:
                scored.append((overlap, role_bonus, anchor))
        scored.sort(key=lambda item: (item[0], item[1], item[2].confidence), reverse=True)
        if scored:
            return [item[2] for item in scored[:limit]]
        return sorted(anchors, key=lambda item: item.confidence, reverse=True)[:1]

    @staticmethod
    def _dedupe(values: list[RetrievalRestriction]) -> list[RetrievalRestriction]:
        seen: set[tuple[str, str, tuple[str, ...], str | None, str]] = set()
        result: list[RetrievalRestriction] = []
        for value in values:
            key = (
                value.kind,
                value.canonical,
                tuple(value.anchor_ids),
                value.operator,
                str(value.normalized_value),
            )
            if key in seen:
                continue
            seen.add(key)
            value.restriction_id = f"restriction_{len(result) + 1}"
            result.append(value)
        return result


class RetrievalGraphBuilder:
    def build(
        self,
        question: str,
        targets: TargetExtraction,
        support: SupportInference,
        binding: RestrictionBinding,
    ) -> AnchorGraph:
        by_id: dict[str, TypedSemanticAnchor] = {}
        warnings: list[str] = []
        for node in [*targets.anchors, *support.anchors]:
            if node.anchor_id in by_id:
                warnings.append(f"Duplicate retrieval target id {node.anchor_id} was dropped.")
                continue
            if node.explicit and node.surface and node.surface.lower() not in question.lower():
                warnings.append(f"Explicit target {node.anchor_id} has no exact source text.")
            by_id[node.anchor_id] = node

        edges: list[AnchorRelation] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for relation in [*targets.relations, *support.relations]:
            if relation.source_anchor_id not in by_id or relation.target_anchor_id not in by_id:
                warnings.append(f"Relation {relation.relation_id} referenced a missing retrieval target.")
                continue
            key = (relation.source_anchor_id, relation.target_anchor_id, relation.relation_type)
            reverse = (relation.target_anchor_id, relation.source_anchor_id, relation.relation_type)
            if key in seen_edges or reverse in seen_edges:
                continue
            seen_edges.add(key)
            relation.relation_id = f"edge_{len(edges) + 1}"
            edges.append(relation)

        restrictions: list[RetrievalRestriction] = []
        for restriction in binding.restrictions:
            restriction.anchor_ids = [item for item in restriction.anchor_ids if item in by_id]
            if not restriction.anchor_ids:
                warnings.append(f"Restriction {restriction.restriction_id} remains globally scoped.")
            restrictions.append(restriction)

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

        roots = [
            item.anchor_id
            for item in by_id.values()
            if item.output_requested and item.retrieval_role == "primary"
        ]
        if not roots:
            roots = [
                item.anchor_id for item in by_id.values()
                if item.kind == AnchorKind.ENTITY and item.retrieval_role == "primary"
            ]
        if not any(item.retrieval_role == "primary" for item in by_id.values()):
            warnings.append("No primary retrieval target was extracted.")
        if not any(item.kind == AnchorKind.ENTITY for item in by_id.values()):
            warnings.append("No containing entity or schema group was extracted.")

        return AnchorGraph(
            nodes=list(by_id.values()),
            edges=edges,
            restrictions=restrictions,
            root_anchor_ids=roots,
            connected_components=components,
            validation_warnings=warnings,
        )


class RetrievalSpecificationBuilder:
    def build(self, graph: AnchorGraph) -> RetrievalSpecificationSet:
        by_id = {item.anchor_id: item for item in graph.nodes}
        neighbors: dict[str, list[tuple[TypedSemanticAnchor, AnchorRelation]]] = defaultdict(list)
        for edge in graph.edges:
            left = by_id[edge.source_anchor_id]
            right = by_id[edge.target_anchor_id]
            neighbors[left.anchor_id].append((right, edge))
            neighbors[right.anchor_id].append((left, edge))
        restrictions_by_anchor: dict[str, list[RetrievalRestriction]] = defaultdict(list)
        for restriction in graph.restrictions:
            for anchor_id in restriction.anchor_ids:
                restrictions_by_anchor[anchor_id].append(restriction)

        specifications: list[RetrievalSpecification] = []
        for anchor in graph.nodes:
            related = neighbors[anchor.anchor_id]
            attached = restrictions_by_anchor[anchor.anchor_id]
            terms: list[str] = []
            source_terms = [
                anchor.surface,
                anchor.canonical,
                *anchor.aliases,
                *anchor.parent_hints,
                *(item.canonical for item, _ in related),
                *(item.canonical for item in attached),
                *(str(item.normalized_value) for item in attached if item.normalized_value is not None),
            ]
            for source in source_terms:
                for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", source.lower()):
                    if token not in terms and token not in {"the", "a", "an", "of", "for", "that", "and"}:
                        terms.append(token)
            structural = [
                f"{edge.relation_type}: {item.canonical}" for item, edge in related
            ] + [
                f"{restriction.kind}: {restriction.description}" for restriction in attached
            ]
            search_kind = self._search_kind(anchor)
            specifications.append(
                RetrievalSpecification(
                    specification_id=f"spec_{len(specifications) + 1}",
                    anchor_ids=[anchor.anchor_id, *(item.anchor_id for item, _ in related)],
                    restriction_ids=[item.restriction_id for item in attached],
                    search_kind=search_kind,
                    query_terms=terms[:24],
                    semantic_query=(
                        f"Retrieve {anchor.retrieval_role} {anchor.kind.value} concept '{anchor.canonical}'. "
                        f"{anchor.description} Parent context: {', '.join(anchor.parent_hints) or 'unknown'}. "
                        f"Evidence constraints: {'; '.join(item.description for item in attached) or 'none'}."
                    ),
                    expected_bson_types=anchor.expected_bson_types,
                    structural_constraints=structural,
                    required=True,
                    rationale=self._rationale(search_kind),
                )
            )
        return RetrievalSpecificationSet(
            specifications=specifications,
            notes=[
                "Each request targets an entity/group, field path, or evidence needed by a derived concept.",
                "Restrictions enrich retrieval terms and scoring context without becoming independent searches.",
                "This stage prepares retrieval requests but does not inspect the schema or database.",
            ],
        )

    @staticmethod
    def _search_kind(anchor: TypedSemanticAnchor) -> str:
        if anchor.kind == AnchorKind.ENTITY:
            return "entity_or_group"
        if anchor.kind == AnchorKind.DERIVED_CONCEPT:
            return "derived_support"
        return "field_path"

    @staticmethod
    def _rationale(search_kind: str) -> str:
        return {
            "entity_or_group": "Retrieve collection, object, array, or document-group candidates for the entity.",
            "field_path": "Retrieve schema paths using concept meaning, aliases, types, parent context, and restrictions.",
            "derived_support": "Retrieve stored realizations and lower-level evidence capable of deriving the concept.",
            "relationship": "Retrieve structural evidence connecting the participating concepts.",
        }[search_kind]


class RetrievalBundleBuilder:
    def build(
        self,
        question: str,
        normalized: NormalizedQuestion,
        graph: AnchorGraph,
        binding: RestrictionBinding,
        specifications: RetrievalSpecificationSet,
    ) -> AnchorBundle:
        categories = [
            any(item.retrieval_role == "primary" for item in graph.nodes),
            any(item.kind == AnchorKind.ENTITY for item in graph.nodes),
            any(item.output_requested for item in graph.nodes),
            bool(graph.edges),
            bool(graph.restrictions),
            bool(specifications.specifications),
        ]
        weights = [0.25, 0.15, 0.2, 0.15, 0.1, 0.15]
        coverage = round(
            sum(weight for present, weight in zip(categories, weights, strict=True) if present),
            3,
        )
        blocking = any("No primary" in warning for warning in graph.validation_warnings)
        return AnchorBundle(
            question=question,
            normalized_question=normalized,
            anchors=graph.nodes,
            relations=graph.edges,
            restrictions=graph.restrictions,
            deferred_plan_cues=binding.deferred_plan_cues,
            retrieval_specifications=specifications.specifications,
            coverage_score=coverage,
            ready_for_retrieval=coverage >= 0.7 and not blocking,
            validation_warnings=graph.validation_warnings,
        )


class AnchorExtractionPipeline:
    """Monitored retrieval-concept extraction; retained name preserves the public run API."""

    def __init__(self, settings: Settings, store: AnchorRunStore):
        self.settings = settings
        self.store = store
        self.llm = StructuredAnchorLLM(settings)
        self.normalizer = QuestionNormalizer()
        self.targets = RetrievalTargetExtractor()
        self.support = SupportingFieldInferer(self.llm)
        self.binding = RestrictionBinder()
        self.graph = RetrievalGraphBuilder()
        self.specifications = RetrievalSpecificationBuilder()
        self.bundle = RetrievalBundleBuilder()
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
                lambda value: f"{len(value.clauses)} clauses, {len(value.scalars)} values, {len(value.cues)} cues",
            )
            run.normalized_question = normalized
            targets = self._stage(
                run,
                "target_extraction",
                lambda: self.targets.extract(normalized),
                lambda value: f"{len(value.anchors)} primary entity or field targets",
            )
            run.target_extraction = targets
            support = self._stage(
                run,
                "support_inference",
                lambda: self.support.infer(
                    normalized,
                    targets,
                    use_llm=use_llm,
                    fallback_on_error=fallback,
                ),
                lambda value: f"{len(value.anchors)} supporting retrieval targets inferred",
            )
            run.support_inference = support
            binding = self._stage(
                run,
                "restriction_binding",
                lambda: self.binding.bind(normalized, targets, support),
                lambda value: f"{len(value.restrictions)} restrictions bound; {len(value.deferred_plan_cues)} plan cues deferred",
            )
            run.restriction_binding = binding
            graph = self._stage(
                run,
                "retrieval_graph",
                lambda: self.graph.build(run.question, targets, support, binding),
                lambda value: f"{len(value.nodes)} targets, {len(value.edges)} relations, {len(value.restrictions)} restrictions",
            )
            run.retrieval_graph = graph
            specifications = self._stage(
                run,
                "retrieval_specifications",
                lambda: self.specifications.build(graph),
                lambda value: f"{len(value.specifications)} future schema retrieval requests",
            )
            run.retrieval_specifications = specifications
            bundle = self._stage(
                run,
                "retrieval_bundle",
                lambda: self.bundle.build(
                    run.question, normalized, graph, binding, specifications
                ),
                lambda value: f"RetrievalBundle coverage {value.coverage_score:.0%}",
            )
            run.retrieval_bundle = bundle
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
