"""Field-aware validation messages without echoing submitted values or credentials."""

LABELS = {
    "title": "평가 이름",
    "year": "평가 연도",
    "projects": "프로젝트",
    "id": "ID",
    "name": "이름",
    "start": "평가 시작일",
    "end": "평가 종료일",
    "weight": "가중치",
    "member_ids": "참여 팀원",
    "github_urls": "GitHub 근거 링크",
    "confluence_urls": "Confluence 근거 링크",
    "rubric": "평가 기준",
    "dimensions": "평가 항목",
    "description": "평가 내용",
    "anchors": "점수 구간 설명",
    "level_weights": "CL 계수",
    "reason": "변경 사유",
    "revision": "수정 버전",
    "career_years": "연차",
    "career_reference_year": "연차 기준 연도",
    "github_ids": "GitHub ID",
    "confluence_ids": "Confluence ID",
    "api_url": "API URL",
    "web_url": "웹 URL",
    "secret": "인증키",
    "password": "비밀번호",
    "model": "LLM 모델",
    "username": "사용자 이름",
}


def validation_details(errors):
    details = []
    for error in errors:
        loc = [part for part in error.get("loc", []) if part != "body"]
        label = (
            " · ".join(
                f"{part + 1}번"
                if isinstance(part, int)
                else LABELS.get(part, "입력 항목")
                for part in loc
            )
            or "평가 설정"
        )
        kind, context = error["type"], error.get("ctx", {})
        if kind == "string_too_short":
            message = f"{context['min_length']}자 이상 입력하세요."
        elif kind == "string_too_long":
            message = f"{context['max_length']}자 이하로 입력하세요."
        elif kind == "too_short":
            message = f"{context['min_length']}개 이상 추가하거나 선택하세요."
        elif kind == "too_long":
            message = f"{context['max_length']}개 이하로 입력하세요."
        elif kind == "missing":
            message = "필수 항목을 입력하세요."
        elif kind.startswith("date"):
            message = "올바른 날짜를 입력하세요."
        elif kind in (
            "greater_than",
            "greater_than_equal",
            "less_than",
            "less_than_equal",
        ):
            operator = {
                "greater_than": ("gt", "초과"),
                "greater_than_equal": ("ge", "이상"),
                "less_than": ("lt", "미만"),
                "less_than_equal": ("le", "이하"),
            }[kind]
            message = f"{context[operator[0]]} {operator[1]} 값을 입력하세요."
        elif kind == "value_error":
            message = error["msg"].removeprefix("Value error, ")
        elif kind == "extra_forbidden":
            message = "지원하지 않는 입력 항목입니다."
        else:
            message = "입력 형식을 확인하세요."
        details.append(
            {"loc": ["body", *loc], "msg": f"{label}: {message}", "type": kind}
        )
    return details
