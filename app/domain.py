import hashlib
import math
import re
from datetime import date, datetime, timezone
from urllib.parse import urlparse, parse_qs, unquote
from pydantic import BaseModel, Field, model_validator, field_validator, ConfigDict

RUBRIC = {
    "version": "1.0",
    "dimensions": [
        {
            "id": "quality",
            "name": "산출물 품질",
            "weight": 35,
            "description": "정확성, 구조, 유지보수성, 설명의 명확성. 실제 결함과 설계 장단점으로 판단한다.",
        },
        {
            "id": "complexity",
            "name": "문제 난이도",
            "weight": 25,
            "description": "기술적 제약, 해결 과정과 대안의 난이도. 변경량이나 장황함은 난이도가 아니다.",
        },
        {
            "id": "validation",
            "name": "검증 완성도",
            "weight": 25,
            "description": "테스트, 예외 처리, 검증 절차, 근거와 재현성. 문서는 운영 절차와 사실 검증을 본다.",
        },
        {
            "id": "collaboration",
            "name": "협업 전달력",
            "weight": 15,
            "description": "인수인계, 의사결정 기록, 재사용과 타인의 이해를 돕는 설명. 외부 평판은 사용하지 않는다.",
        },
    ],
    "anchors": "0–19 심각한 결함 / 20–39 개선 필요 / 40–59 기본 충족 / 60–79 견고함 / 80–100 탁월함. 관찰 불가=null.",
}
DIMS = [d["id"] for d in RUBRIC["dimensions"]]
DEFAULT_LEVEL_WEIGHTS = {"CL2": 1.0, "CL3": 0.9, "CL4": 0.8}


def career_level(years):
    return "CL2" if years <= 8 else "CL3" if years <= 16 else "CL4"


def career_at(member, year):
    years = member.get("career_years")
    reference = member.get("career_reference_year")
    if years is None or reference is None:
        raise ValueError(f"{member['name']}: 평가 전에 연차를 등록하세요.")
    years += year - reference
    if not 1 <= years <= 100:
        raise ValueError(f"{member['name']}: {year}년 기준 연차가 유효하지 않습니다.")
    return years


def level_policy(cfg):
    weights = cfg.get("evaluation", {}).get("level_weights", DEFAULT_LEVEL_WEIGHTS)
    if (
        not isinstance(weights, dict)
        or set(weights) != set(DEFAULT_LEVEL_WEIGHTS)
        or any(
            type(v) not in (int, float) or not math.isfinite(v)
            for v in weights.values()
        )
        or not 0 < weights["CL4"] < weights["CL3"] < weights["CL2"] <= 1
    ):
        raise ValueError("레벨 계수는 0 < CL4 < CL3 < CL2 ≤ 1이어야 합니다.")
    return {"version": "1.0", "weights": dict(weights)}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Member(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    name: str = Field(min_length=1, max_length=80)
    github_ids: list[str] = Field(default_factory=list, max_length=20)
    confluence_ids: list[str] = Field(default_factory=list, max_length=20)
    active: bool = True
    career_years: int = Field(ge=1, le=100, strict=True)
    career_reference_year: int = Field(
        default_factory=lambda: datetime.now(timezone.utc).year,
        ge=2000,
        le=2200,
        strict=True,
    )

    @field_validator("github_ids", "confluence_ids")
    @classmethod
    def clean_ids(cls, vals):
        vals = [v.strip().lower() for v in vals if v.strip()]
        if any(len(v) > 200 or "," in v or any(c.isspace() for c in v) for v in vals):
            raise ValueError("ID는 공백·쉼표 없이 하나씩, 최대 200자로 입력하세요.")
        return list(dict.fromkeys(vals))


class DraftProject(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")
    name: str = Field(default="", max_length=120)
    start: date | None = None
    end: date | None = None
    weight: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    member_ids: list[str] = Field(default_factory=list, max_length=500)
    confluence_urls: list[str] = Field(default_factory=list, max_length=30)
    github_urls: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("start", "end", mode="before")
    @classmethod
    def empty_date(cls, value):
        return None if value == "" else value


class Project(DraftProject):
    name: str = Field(min_length=1, max_length=120)
    start: date
    end: date
    weight: float = Field(gt=0, le=100, allow_inf_nan=False)
    member_ids: list[str] = Field(min_length=1, max_length=500)
    confluence_urls: list[str] = Field(default_factory=list, max_length=30)
    github_urls: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def valid(self):
        if self.start > self.end:
            raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
        if not self.github_urls and not self.confluence_urls:
            raise ValueError("하나 이상의 근거 링크가 필요합니다.")
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("참여자가 중복되었습니다.")
        return self


class CriterionInput(StrictModel):
    id: str
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=3, max_length=6000)
    weight: float = Field(gt=0, le=100, allow_inf_nan=False)


class RubricInput(StrictModel):
    version: str = Field(default="custom", max_length=100)
    dimensions: list[CriterionInput] = Field(min_length=4, max_length=4)
    anchors: str = Field(min_length=3, max_length=6000)

    @model_validator(mode="after")
    def valid(self):
        if {d.id for d in self.dimensions} != set(DIMS):
            raise ValueError("네 가지 평가 기준 ID를 모두 포함해야 합니다.")
        if not math.isclose(sum(d.weight for d in self.dimensions), 100, abs_tol=0.001):
            raise ValueError("평가 기준 비율의 합은 100이어야 합니다.")
        return self


class DraftEvaluationInput(StrictModel):
    title: str = Field(default="", max_length=120)
    year: int = Field(ge=2000, le=2200)
    projects: list[DraftProject] = Field(default_factory=list, max_length=50)
    revision: int = 0
    reason: str = Field(default="", max_length=3000)
    refresh_members: bool = False
    use_current_rubric: bool = False
    rubric: RubricInput | None = None
    level_weights: dict[str, float] | None = None

    @model_validator(mode="after")
    def valid_draft(self):
        if self.level_weights is not None:
            level_policy({"evaluation": {"level_weights": self.level_weights}})
        if len({p.id for p in self.projects}) != len(self.projects):
            raise ValueError("프로젝트 ID가 중복되었습니다.")
        return self


class EvaluationInput(DraftEvaluationInput):
    title: str = Field(min_length=1, max_length=120)
    projects: list[Project] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def valid(self):
        if not math.isclose(sum(p.weight for p in self.projects), 100, abs_tol=0.001):
            raise ValueError("프로젝트 가중치의 합은 100이어야 합니다.")
        if any(
            p.start.year != self.year or p.end.year != self.year for p in self.projects
        ):
            raise ValueError("프로젝트 기간은 평가 연도 안에 있어야 합니다.")
        return self


def parse_source(url, cfg, kind):
    if len(url) > 2000:
        raise ValueError("URL이 너무 깁니다.")
    u, base = urlparse(url), urlparse(cfg[kind]["web_url"])
    if (
        u.scheme != base.scheme
        or u.netloc.lower() != base.netloc.lower()
        or u.username
        or u.password
    ):
        raise ValueError(f"{kind}: 설정된 서비스의 URL만 사용할 수 있습니다.")
    path = unquote(u.path)
    prefix = base.path.rstrip("/")
    if prefix and not (path == prefix or path.startswith(prefix + "/")):
        raise ValueError("설정된 서비스 경로 밖의 URL입니다.")
    path = path[len(prefix) :].strip("/")
    if kind == "github":
        path = path.removesuffix(".git")
        parts = path.split("/")
        if len(parts) == 2 and parts[0] == "orgs":
            parts = parts[1:]
        if len(parts) not in (1, 2) or any(
            not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in (".", "..") for p in parts
        ):
            raise ValueError("GitHub repo 또는 org 루트 URL을 입력하세요.")
        return {"kind": "repo" if len(parts) == 2 else "org", "name": "/".join(parts)}
    match = re.search(r"(?:^|/)pages/(\d+)(?:/|$)", path)
    page = match.group(1) if match else parse_qs(u.query).get("pageId", [""])[0]
    if not page.isdigit():
        raise ValueError("Confluence page ID를 포함하는 페이지 URL이 필요합니다.")
    return {"kind": "page", "name": page}


def hash_text(s):
    return hashlib.sha256(s.encode()).hexdigest()


def aggregate(ev):
    results = ev.get("results", [])
    rubric = ev.get("rubric", RUBRIC)
    by_project = {p["id"]: p for p in ev["projects"]}
    rows = []
    for member in ev.get("members_snapshot", []):
        participating = [p for p in ev["projects"] if member["id"] in p["member_ids"]]
        if not participating:
            continue
        denominator = sum(p["weight"] for p in participating)
        weighted = known = 0
        details = []
        for result in results:
            if (
                result["member_id"] != member["id"]
                or result["project_id"] not in by_project
                or member["id"] not in by_project[result["project_id"]]["member_ids"]
            ):
                continue
            dims = result["dimensions"]
            scores = {k: dims[k].get("adjusted_score", dims[k]["score"]) for k in DIMS}
            score = (
                None
                if any(v is None for v in scores.values())
                else sum(
                    scores[d["id"]] * d["weight"] / 100 for d in rubric["dimensions"]
                )
            )
            weight = by_project[result["project_id"]]["weight"]
            if score is not None:
                weighted += score * weight
                known += weight
            details.append(
                {
                    "project_id": result["project_id"],
                    "score": round(score, 2) if score is not None else None,
                    "scores": scores,
                    "weight": weight,
                    "collection_incomplete": bool(
                        result.get("collection_incomplete")
                        or ev.get("scopes", {})
                        .get(result["project_id"], {})
                        .get("collection_incomplete")
                    ),
                }
            )
        raw_score = weighted / known if known else None
        policy = ev.get("level_policy")
        years = career_at(member, ev["year"]) if policy else None
        level = career_level(years) if years is not None else None
        factor = policy["weights"][level] if policy else 1.0
        rows.append(
            {
                "member_id": member["id"],
                "name": member["name"],
                "score": round(raw_score * factor, 2)
                if raw_score is not None
                else None,
                "raw_score": round(raw_score, 2) if raw_score is not None else None,
                "career_years": years,
                "level": level,
                "level_weight": factor,
                "level_applied": bool(policy),
                "coverage": round(known / denominator * 100, 1) if denominator else 0,
                "participating_weight": denominator,
                "assessed_weight": known,
                "collection_incomplete": any(
                    p["collection_incomplete"] for p in details
                ),
                "provisional": known < denominator
                or any(p["collection_incomplete"] for p in details),
                "projects": details,
            }
        )
    return rows


def validate_judgment(data, evidence):
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("dimensions"), dict)
        or set(data["dimensions"]) != set(DIMS)
    ):
        raise ValueError("LLM 차원 스키마 불일치")
    allowed = {e["id"]: e["content"] for e in evidence}
    dims = {}
    for key in DIMS:
        d = data["dimensions"][key]
        if not isinstance(d, dict):
            raise ValueError("LLM 차원 형식 오류")
        score = d.get("score")
        if score is not None and (
            type(score) not in (int, float)
            or not math.isfinite(score)
            or not 0 <= score <= 100
        ):
            raise ValueError("LLM 점수 범위 오류")
        citations = d.get("citations", [])
        if not isinstance(citations, list) or (score is not None and not citations):
            raise ValueError("LLM 점수에 근거가 없습니다.")
        for c in citations:
            if (
                not isinstance(c, dict)
                or c.get("evidence_id") not in allowed
                or not isinstance(c.get("quote"), str)
                or len(c["quote"].strip()) < 8
                or c["quote"] not in allowed[c["evidence_id"]]
            ):
                raise ValueError("LLM 근거 ID 또는 인용 불일치")
        if not isinstance(d.get("reason"), str) or not 1 <= len(d["reason"]) <= 6000:
            raise ValueError("LLM 평가 설명 누락")
        confidence = d.get("confidence", 0)
        if (
            type(confidence) not in (float, int)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("LLM 신뢰도 오류")
        dims[key] = {
            "score": score,
            "reason": d["reason"],
            "confidence": confidence,
            "citations": citations,
        }
    summary = data.get("summary", "")
    limitations = data.get("limitations", "")
    if (
        not isinstance(summary, str)
        or not isinstance(limitations, str)
        or len(summary) > 10000
        or len(limitations) > 10000
    ):
        raise ValueError("LLM 요약 형식 오류")
    return {"dimensions": dims, "summary": summary, "limitations": limitations}
