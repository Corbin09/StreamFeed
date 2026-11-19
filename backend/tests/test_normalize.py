import pytest

from app.pipeline.classify import is_hiring_post
from app.pipeline.language import detect_language
from app.pipeline.normalize import (
    detect_levels,
    extract_skills,
    normalize_locations,
    normalize_skill_tags,
    normalize_work_mode,
)
from app.pipeline.text import fold, slug


def test_fold_and_slug() -> None:
    assert fold("  Đà   Nẵng ") == "da nang"
    assert fold("Lập Trình Viên") == "lap trinh vien"
    assert slug("Business Analysis (BA)") == "business-analysis-ba"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["Hà Nội"], ["hanoi"]),
        (["Ha Noi"], ["hanoi"]),
        (["TP.HCM"], ["hcmc"]),
        (["Thành phố Thủ Đức"], ["hcmc"]),
        (["Hồ Chí Minh; Hà Nội"], ["hcmc", "hanoi"]),
        (["Đà Nẵng", None, "Làm từ xa"], ["danang", "remote"]),
        (["Hanoi", "Ha Noi"], ["hanoi"]),
        (["Mars"], []),
    ],
)
def test_locations(raw, expected) -> None:
    assert normalize_locations(raw) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Middle/Senior Back End Developer (Java)", ["mid", "senior"]),
        ("Tuyển Thực tập sinh ReactJS", ["intern"]),
        ("Team Lead .NET", ["lead"]),
        ("Assistant Manager Cloud Engineer", ["manager"]),
        ("Sr. Python Engineer", ["senior"]),
        ("Backend Developer", []),
    ],
)
def test_levels(title, expected) -> None:
    assert detect_levels(title) == expected


def test_skill_tags_are_canonicalized_and_unknown_kept_as_slug() -> None:
    tags = ["C language", "HTML5", "ReactJS", "Node.js", "C#", "Golang", "Waterfall Methodology"]
    assert normalize_skill_tags(tags) == [
        "c",
        "html",
        "react",
        "nodejs",
        "csharp",
        "go",
        "waterfall-methodology",
    ]


def test_free_text_skills_skip_ambiguous_aliases() -> None:
    text = "Tuyển dev ReactJS + Node.js, biết C++ và Docker. Go to office 5 days, C-level support."
    assert extract_skills(text) == ["react", "nodejs", "cpp", "docker"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("At office", "onsite"),
        ("Hybrid", "hybrid"),
        ("Remote", "remote"),
        ("Làm từ xa", "remote"),
        ("Tại văn phòng", "onsite"),
        (None, None),
        ("???", None),
    ],
)
def test_work_mode(raw, expected) -> None:
    assert normalize_work_mode(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tuyển lập trình viên Python, lương 15-25 triệu, làm việc tại Hà Nội", "vi"),
        ("tuyen dev reactjs gap, luong thoa thuan, lam viec tai cau giay", "vi"),
        ("We are looking for a Senior Backend Developer to join our team", "en"),
        ("Short", None),
    ],
)
def test_language(text, expected) -> None:
    assert detect_language(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Contact rules (the fallback when the LLM is off): one strong signal, or weak
        # signals from two groups.
        ("Cần tuyển gấp 2 bạn dev PHP, lh 0912 345 678", True),
        ("We're hiring a QA engineer, send your CV to jobs@example.com", True),
        ("Tuyen BA, apply: https://forms.gle/abc123", True),
        ("tuyen dev php, lam viec tai Cau Giay, ib zalo", True),
        ("We're hiring a QA engineer", False),  # no way to reach the employer
        ("Có ai biết quán cafe nào yên tĩnh ở Cầu Giấy không?", False),
        ("Bán bàn phím cơ cũ, giá 500k", False),
    ],
)
def test_hiring_classifier(text, expected) -> None:
    assert is_hiring_post(text) is expected


def test_district_and_short_city_names() -> None:
    from app.pipeline.normalize import normalize_locations

    assert normalize_locations(["[Cầu Giấy, HN] tuyển Senior Fullstack"]) == ["hanoi"]
    assert normalize_locations(["Văn phòng Tân Bình"]) == ["hcmc"]
