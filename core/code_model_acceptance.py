from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.model_roles import ModelRoleResolver
from artmach_assistant.core.workspace import WorkspaceError, WorkspaceService


@dataclass(frozen=True, slots=True)
class CodeModelAcceptanceResult:
    passed: bool
    model: str
    attempts: int
    detail: str

    def render(self) -> str:
        status = "BAŞARILI" if self.passed else "BAŞARISIZ"
        return (
            f"Yerel kod modeli patch kabul testi: {status}\n"
            f"Model: {self.model}\n"
            f"Deneme: {self.attempts}/3\n"
            f"Ayrıntı: {self.detail}"
        )


def _prompt(source: str, feedback: str = "", previous: str = "") -> str:
    text = (
        "Aşağıdaki çalışan Python dosyasında add fonksiyonunun çıkarma hatasını "
        "düzelt. Yalnızca geçerli JSON nesnesi döndür. Mevcut dosyayı tam içerikle "
        "yeniden yazma; operations içinde tek bir exact replace kullan. Başka dosya "
        "ekleme veya değiştirme.\n\n"
        "ŞEMA:\n"
        '{"summary":"...","files":[{"path":"sample.py","reason":"...",'
        '"operations":[{"op":"replace","old":"...","new":"..."}]}]}\n\n'
        "ÇALIŞAN KAYNAK:\n--- DOSYA: sample.py ---\n"
        + source
    )
    if feedback:
        text += (
            "\n\nÖNCEKİ TASLAK REDDEDİLDİ:\n"
            + feedback[-4000:]
            + "\nAynı cevabı tekrarlama; yalnızca bu hatayı düzelt."
        )
    if previous:
        text += "\n\nÖNCEKİ REDDEDİLEN JSON:\n" + previous[-8000:]
    return text


def run_code_model_patch_acceptance(
    config: object,
    *,
    urlopen: Callable[..., object] | None = None,
    timeout: int = 90,
) -> CodeModelAcceptanceResult:
    """Verify the configured local code model on an isolated real patch task.

    The test never touches Jarvis' source tree.  It asks the configured code
    model for one exact operation, validates the resulting proposal with the
    production EditManager/PatchValidator stack, and compiles the resulting
    Python text.  Invalid output is fed back at most three times.
    """

    opener = urlopen or urllib.request.urlopen
    role = ModelRoleResolver(config).code
    source = "def add(a: int, b: int) -> int:\n    return a - b\n"
    previous = ""
    feedback = ""
    seen: set[str] = set()
    last_detail = "Kod modeli cevap üretmedi."

    with tempfile.TemporaryDirectory(prefix="jarvis_code_model_acceptance_") as temp:
        root = Path(temp)
        (root / "sample.py").write_text(source, encoding="utf-8")
        workspace = WorkspaceService(str(root))
        editor = EditManager(workspace)
        try:
            for attempt in range(1, 4):
                prompt = _prompt(source, feedback, previous)
                body = json.dumps(
                    {
                        "model": role.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Güvenli yerel kod patch motorusun. Yalnızca JSON "
                                    "üret ve mevcut dosyalarda exact operations kullan."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "stream": False,
                        "format": "json",
                        "options": {
                            "temperature": 0.0,
                            "num_ctx": role.context_window,
                            "num_predict": min(role.max_output_tokens, 2048),
                        },
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                request = urllib.request.Request(
                    f"{str(getattr(config, 'ollama_url', '')).rstrip('/')}/api/chat",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    response = opener(request, timeout=timeout)
                    with response:
                        try:
                            raw_body = response.read(500_001)
                        except TypeError:
                            raw_body = response.read()
                    if len(raw_body) > 500_000:
                        raise WorkspaceError("Kod modeli yanıt boyutu sınırını aştı.")
                    envelope = json.loads(raw_body.decode("utf-8"))
                    previous = str(
                        envelope.get("message", {}).get("content", "")
                    ).strip()
                    if not previous:
                        raise WorkspaceError("Kod modeli boş JSON üretti.")
                    fingerprint = hashlib.sha256(
                        previous.encode("utf-8", errors="replace")
                    ).hexdigest()
                    if fingerprint in seen:
                        raise WorkspaceError(
                            "Kod modeli önceki reddedilen cevabın aynısını tekrarladı."
                        )
                    seen.add(fingerprint)
                    payload = EditManager.parse_json_response(previous)
                    rows = payload.get("files")
                    if not isinstance(rows, list) or len(rows) != 1:
                        raise WorkspaceError("Tam olarak bir dosya değişikliği gerekli.")
                    row = rows[0]
                    if not isinstance(row, dict) or row.get("path") != "sample.py":
                        raise WorkspaceError("Yalnızca sample.py değiştirilebilir.")
                    if isinstance(row.get("content"), str):
                        raise WorkspaceError(
                            "Mevcut dosya tam içerikle yazılamaz; operations gerekli."
                        )
                    proposal = editor.create_proposal(
                        json.dumps(payload, ensure_ascii=False)
                    )
                    if len(proposal.files) != 1:
                        raise WorkspaceError("Tek dosyalı taslak bekleniyordu.")
                    new_content = proposal.files[0].new_content
                    compile(new_content, "sample.py", "exec")
                    tree = ast.parse(new_content, filename="sample.py")
                    function = next(
                        (
                            node for node in tree.body
                            if isinstance(node, ast.FunctionDef) and node.name == "add"
                        ),
                        None,
                    )
                    return_node = (
                        next(
                            (
                                node for node in ast.walk(function)
                                if isinstance(node, ast.Return)
                            ),
                            None,
                        )
                        if function is not None else None
                    )
                    if not (
                        isinstance(return_node, ast.Return)
                        and isinstance(return_node.value, ast.BinOp)
                        and isinstance(return_node.value.op, ast.Add)
                    ):
                        raise WorkspaceError(
                            "Taslak derlendi ancak hedef davranışı düzeltmedi."
                        )
                    return CodeModelAcceptanceResult(
                        True,
                        role.model,
                        attempt,
                        "Kod modeli exact operation üretti; patch doğrulandı ve hedef davranış geçti.",
                    )
                except (
                    WorkspaceError,
                    urllib.error.URLError,
                    TimeoutError,
                    ValueError,
                    json.JSONDecodeError,
                    UnicodeError,
                ) as exc:
                    feedback = str(exc)
                    last_detail = feedback
                    editor.reject()
                    continue
        finally:
            workspace.shutdown()

    return CodeModelAcceptanceResult(False, role.model, 3, last_detail)
