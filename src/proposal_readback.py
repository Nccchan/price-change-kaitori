"""proposal・Supabase・公開表示の三者一致検証。"""
from typing import Any, Dict, Mapping, Tuple


class ReadbackMismatchError(RuntimeError):
    pass


def verify_three_way(
    proposal: Dict[str, Any],
    supabase_values: Mapping[Tuple[str, str], int],
    public_values: Mapping[Tuple[str, str], int],
) -> None:
    expected = {
        (item["code"].strip().upper(), item["unit"]): int(item["proposed"])
        for item in proposal.get("items", [])
    }
    errors = []
    for key, value in expected.items():
        if supabase_values.get(key) != value:
            errors.append(f"{key}: Supabase={supabase_values.get(key)} expected={value}")
        if public_values.get(key) != value:
            errors.append(f"{key}: public={public_values.get(key)} expected={value}")
    extra_sb = set(supabase_values) - set(expected)
    extra_public = set(public_values) - set(expected)
    if extra_sb or extra_public:
        errors.append(f"対象外キー: Supabase={sorted(extra_sb)} public={sorted(extra_public)}")
    if errors:
        raise ReadbackMismatchError("read-back不一致: " + "; ".join(errors))
