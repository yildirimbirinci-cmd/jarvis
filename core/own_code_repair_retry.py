from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Mapping


_MAX_TARGET_FILES = 8
_MAX_TARGET_SYMBOLS = 16
_MAX_ISSUE_CODES = 16
_PATH_TOKEN = re.compile(
    r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.(?:py|json|toml|xml|ui|svg))(?::\d+)?",
    re.IGNORECASE,
)
_SYMBOL_GROUP = re.compile(
    r"(?:sembol\s+kayb[ıi]|api\s+imzas[ıi]\s+değişikliği|api\s+imzasi\s+degisikligi)\s*\(([^)]*)\)",
    re.IGNORECASE,
)
_DOTTED_SYMBOL = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")
_ISSUE_CODE = re.compile(r"\[([A-Za-z0-9_.-]+)\]")


@dataclass(frozen=True, slots=True)
class RepairRetryPolicy:
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.max_attempts < 0 or self.max_attempts > 3:
            raise ValueError("Repair retry deneme sayısı 0 ile 3 arasında olmalıdır.")


@dataclass(frozen=True, slots=True)
class RepairTargets:
    """The smallest safe slice of a rejected proposal that may be regenerated."""

    paths: tuple[str, ...]
    symbols: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()
    used_fallback: bool = False

    def __post_init__(self) -> None:
        if not self.paths:
            raise ValueError("Onarım için en az bir hedef dosya gereklidir.")
        if len(self.paths) > _MAX_TARGET_FILES:
            raise ValueError("Onarım hedefi güvenli dosya sınırını aşıyor.")


def _normalize_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    return PurePosixPath(raw).as_posix()


def _unique(values: Iterable[str], *, limit: int) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return tuple(result)


def proposal_as_payload(proposal: object) -> dict[str, object]:
    """Return the model-facing full-file proposal without internal old content."""

    if isinstance(proposal, Mapping):
        summary = str(proposal.get("summary", ""))
        rows = proposal.get("files", ())
    else:
        summary = str(getattr(proposal, "summary", ""))
        rows = getattr(proposal, "files", ())

    files: list[dict[str, str]] = []
    if isinstance(rows, (str, bytes)) or rows is None:
        rows = ()
    try:
        iterable = tuple(rows)
    except TypeError:
        iterable = ()

    for change in iterable:
        if isinstance(change, Mapping):
            path = _normalize_path(change.get("path", ""))
            reason = str(change.get("reason", ""))
            content = change.get("content", change.get("new_content", ""))
        else:
            path = _normalize_path(getattr(change, "path", ""))
            reason = str(getattr(change, "reason", ""))
            content = getattr(change, "new_content", getattr(change, "content", ""))
        files.append({
            "path": path,
            "reason": reason,
            "content": str(content) if isinstance(content, str) else "",
        })
    return {"summary": summary, "files": files}


def proposal_as_json(proposal: object) -> str:
    return json.dumps(
        proposal_as_payload(proposal),
        ensure_ascii=False,
        sort_keys=True,
    )


def _proposal_paths(proposal: object) -> tuple[str, ...]:
    payload = proposal_as_payload(proposal)
    rows = payload.get("files", ())
    return _unique(
        (_normalize_path(row.get("path", "")) for row in rows if isinstance(row, Mapping)),
        limit=_MAX_TARGET_FILES,
    )


def extract_repair_targets(report: str, proposal: object) -> RepairTargets:
    """Derive repair paths/symbols only from the validator report and rejected draft.

    Paths not present in the rejected proposal are ignored, preventing a validator
    message or model response from expanding the approved change scope.
    """

    original_paths = _proposal_paths(proposal)
    if not original_paths:
        raise ValueError("Reddedilen taslakta hedeflenebilir dosya yok.")

    normalized_report = str(report or "").replace("\\", "/")
    report_folded = normalized_report.casefold()
    matched_paths = [
        path for path in original_paths
        if path.casefold() in report_folded
    ]

    # Some validator messages normalize path spelling. Parse path-like tokens,
    # but still intersect them with the original proposal's exact path set.
    original_by_key = {path.casefold(): path for path in original_paths}
    for token in _PATH_TOKEN.findall(normalized_report):
        candidate = _normalize_path(token)
        original = original_by_key.get(candidate.casefold())
        if original is not None and original not in matched_paths:
            matched_paths.append(original)

    used_fallback = not matched_paths
    paths = tuple(matched_paths or original_paths)

    symbols: list[str] = []
    for group in _SYMBOL_GROUP.findall(normalized_report):
        symbols.extend(part.strip() for part in group.split(","))
    for symbol in _DOTTED_SYMBOL.findall(normalized_report):
        if not symbol.casefold().endswith(
            (".py", ".json", ".toml", ".xml", ".ui", ".svg")
        ):
            symbols.append(symbol)

    issue_codes = _unique(_ISSUE_CODE.findall(normalized_report), limit=_MAX_ISSUE_CODES)
    return RepairTargets(
        paths=paths,
        symbols=_unique(symbols, limit=_MAX_TARGET_SYMBOLS),
        issue_codes=issue_codes,
        used_fallback=used_fallback,
    )


def _target_payload(proposal: object, targets: RepairTargets) -> dict[str, object]:
    payload = proposal_as_payload(proposal)
    target_keys = {path.casefold() for path in targets.paths}
    rows = [
        row for row in payload.get("files", ())
        if isinstance(row, Mapping)
        and _normalize_path(row.get("path", "")).casefold() in target_keys
    ]
    return {"summary": payload.get("summary", ""), "files": rows}


def build_validation_repair_prompt(
    instruction: str,
    report: str,
    proposal: object,
    *,
    stage: str,
    targets: RepairTargets | None = None,
) -> str:
    """Build a bounded prompt that regenerates only validator-identified files."""

    selected = targets or extract_repair_targets(report, proposal)
    target_files = "\n".join(f"- {path}" for path in selected.paths)
    target_symbols = (
        "\n".join(f"- {symbol}" for symbol in selected.symbols)
        if selected.symbols else "- Doğrulayıcı belirli bir sembol bildirmedi."
    )
    issue_codes = (
        ", ".join(selected.issue_codes)
        if selected.issue_codes else "belirtilmedi"
    )
    fallback_note = (
        "Doğrulayıcı dosya adı vermediği için hedef, reddedilen taslağın mevcut "
        "dosyalarıyla sınırlandırıldı.\n"
        if selected.used_fallback else ""
    )
    schema_example = {
        "summary": "Kısa onarım özeti",
        "files": [
            {
                "path": path,
                "reason": "Doğrulayıcı hatasını düzeltir",
                "operations": [
                    {
                        "op": "replace",
                        "old": "çalışan kaynakta tek kez bulunan küçük metin",
                        "new": "düzeltilmiş metin",
                    }
                ],
            }
            for path in selected.paths
        ],
    }
    semantic_guidance = ""
    if "semantik" in str(stage or "").casefold():
        semantic_guidance = (
            "\nSEMANTİK ONARIM KURALI:\n"
            "Doğrulayıcı raporundaki her `assign:`, `call:` ve `control:` öğesini "
            "koru. Bir blok yardımcı metoda çıkarılıyorsa kaynak durum başlangıçlarını "
            "(örneğin `mode = self._next_mode` ve sonraki durum ataması) helper içine "
            "eksiksiz taşı. `break` veya `continue` helper içinde doğrudan kullanılamaz; "
            "helper bir karar değeri döndürmeli ve çağıran özgün metot bu değere göre "
            "aynı `break`/`continue` kararını vermelidir. Kayıp öğeyi silerek, çağrıyı "
            "atlayarak veya yalnız `return` ile değiştirerek raporu susturma.\n"
        )
    return (
        f"Önceki patch {stage.strip() or 'kod'} doğrulamasında reddedildi. "
        "Aynı hatalı patch'i tekrarlama. Yalnızca aşağıdaki HEDEF DOSYALAR için "
        "çalışan kaynak üzerindeki küçük ve tam eşleşen operations üret. Hedef "
        "listesi dışında dosya ekleme, silme, yeniden adlandırma veya değiştirme. "
        "Hedef olmayan taslak dosyaları sistem aynı içerikle koruyacak. Hedef "
        "semboller bildirilmişse yalnızca o sembollerin doğrulama sorununu düzelt; "
        "aynı dosyadaki diğer sembol ve davranışları koru.\n"
        "Mevcut dosyalar için content ile tam dosya döndürme. replace old metni "
        "çalışan kaynakta tam olarak bir kez bulunmalı. Her hedef dosya yanıtta "
        "tam olarak bir kez bulunmalı. Yalnızca geçerli JSON nesnesi döndür; "
        "Markdown veya açıklama ekleme.\n"
        + fallback_note
        + f"\nKULLANICI İSTEĞİ:\n{instruction.strip()}\n"
        + f"\nDOĞRULAMA AŞAMASI:\n{stage.strip() or 'kod'}\n"
        + f"\nHATA KODLARI:\n{issue_codes}\n"
        + f"\nHEDEF DOSYALAR:\n{target_files}\n"
        + f"\nHEDEF SEMBOLLER:\n{target_symbols}\n"
        + f"\nDOĞRULAYICI RAPORU:\n{str(report or '').strip()}\n"
        + semantic_guidance
        + "\nYALNIZCA HEDEF DOSYALARIN REDDEDİLEN TASLAĞI:\n"
        + json.dumps(_target_payload(proposal, selected), ensure_ascii=False, sort_keys=True)
        + "\n\nBEKLENEN JSON ŞEMASI:\n"
        + json.dumps(schema_example, ensure_ascii=False, sort_keys=True)
    )


def build_semantic_repair_prompt(instruction: str, report: str, proposal: object) -> str:
    """Backward-compatible wrapper for semantic guard repair prompts."""

    return build_validation_repair_prompt(
        instruction,
        report,
        proposal,
        stage="semantik koruma",
    )


def merge_targeted_repair_response(
    rejected_proposal: object,
    repair_response: object,
    targets: RepairTargets,
) -> str:
    """Merge repaired target files with untouched rejected proposal rows.

    The repair response must contain exactly the target path set. Any scope
    expansion, missing file, duplicate path, or byte-identical retry is rejected
    before EditManager/PatchValidator sees the merged candidate.
    """

    original = proposal_as_payload(rejected_proposal)
    repaired = proposal_as_payload(repair_response)
    original_rows = original.get("files", ())
    repaired_rows = repaired.get("files", ())
    if not isinstance(original_rows, list) or not isinstance(repaired_rows, list):
        raise ValueError("Onarım taslağında geçerli files listesi gerekli.")

    target_keys = {path.casefold(): path for path in targets.paths}
    repaired_by_key: dict[str, dict[str, str]] = {}
    for row in repaired_rows:
        if not isinstance(row, Mapping):
            raise ValueError("Onarım dosya kayıtları JSON nesnesi olmalıdır.")
        path = _normalize_path(row.get("path", ""))
        key = path.casefold()
        if not path or key not in target_keys:
            raise ValueError(f"Onarım izin verilmeyen dosya kapsamına çıktı: {path or '<boş>'}")
        if key in repaired_by_key:
            raise ValueError(f"Onarım aynı hedef dosyayı yineledi: {path}")
        content = row.get("content", row.get("new_content"))
        if not isinstance(content, str):
            raise ValueError(f"Onarım dosya içeriği metin olmalıdır: {path}")
        repaired_by_key[key] = {
            "path": target_keys[key],
            "reason": str(row.get("reason", "Doğrulayıcı hatası giderildi.")),
            "content": content,
        }

    missing = [path for key, path in target_keys.items() if key not in repaired_by_key]
    if missing:
        raise ValueError("Onarım hedef dosyaları eksik bıraktı: " + ", ".join(missing))

    original_by_key = {
        _normalize_path(row.get("path", "")).casefold(): row
        for row in original_rows
        if isinstance(row, Mapping)
    }
    unchanged = [
        path for key, path in target_keys.items()
        if str(original_by_key.get(key, {}).get("content", ""))
        == repaired_by_key[key]["content"]
    ]
    if len(unchanged) == len(targets.paths):
        raise ValueError(
            "Onarım yanıtı reddedilen hedef içeriği değiştirmedi; aynı patch yeniden üretildi."
        )

    merged_rows: list[dict[str, str]] = []
    seen_original: set[str] = set()
    for row in original_rows:
        if not isinstance(row, Mapping):
            raise ValueError("Reddedilen taslakta geçersiz dosya kaydı var.")
        path = _normalize_path(row.get("path", ""))
        key = path.casefold()
        if not path or key in seen_original:
            raise ValueError("Reddedilen taslakta boş veya yinelenen dosya yolu var.")
        seen_original.add(key)
        if key in repaired_by_key:
            merged_rows.append(repaired_by_key[key])
        else:
            content = row.get("content", row.get("new_content"))
            if not isinstance(content, str):
                raise ValueError(f"Reddedilen taslak dosya içeriği metin değil: {path}")
            merged_rows.append({
                "path": path,
                "reason": str(row.get("reason", "")),
                "content": content,
            })

    summary = str(repaired.get("summary", "")).strip() or str(original.get("summary", ""))
    return json.dumps(
        {"summary": summary, "files": merged_rows},
        ensure_ascii=False,
        sort_keys=True,
    )
